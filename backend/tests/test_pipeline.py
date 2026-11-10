"""Test the path from an image upload to OCR review and CSV export.

Use small images and fake OCR replies so most tests can run on their own.
Check bad input and service errors as well as normal results.
"""

import csv
from io import BytesIO, StringIO
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai.errors import APIError
from google.genai.types import FinishReason
from PIL import Image, ImageDraw

from app.config import MAX_IMAGE_BYTES, Settings
from app.errors import AppError
from app.extraction import extract
from app.main import create_app
from app.ocr.base import ImageLine, Recognition, Transcription
from app.ocr.gemini import GeminiOCR
from app.ocr.tesseract import Word, layout


def line(role, source, cells, uncertain=None):
    """Make a recognized line for a test case."""
    return ImageLine(role=role, source_text=source, cells=cells, uncertain=uncertain or [])


def sample():
    """Make a fixed OCR result with both readable and uncertain cells."""
    return Recognition(
        provider="test-double",
        transcription=Transcription(direction="ltr", lines=[
            line("note", "Demo delivery list", []),
            line("heading", "Vehicle Number  Driver  Date", ["Vehicle Number", "Driver", "Date"]),
            line("record", "102  דניאל  2026-11-08", ["102", "דניאל", "2026-11-08"]),
            line("record", "20S  03/04/2026", ["20S", None, "03/04/2026"], [0]),
            line("record", "102  דניאל  2026-11-08", ["102", "דניאל", "2026-11-08"]),
            line("record", "ink across both columns", []),
        ]),
        confidence={3: [0.39, None, 0.93]},
    )


def image(fmt="PNG", blank=False):
    """Generate an image in memory for the upload tests."""
    bitmap = Image.new("RGB", (460, 260), "white")
    if not blank:
        draw = ImageDraw.Draw(bitmap)
        draw.rectangle((25, 35, 380, 100), fill="black")
        draw.line((15, 190, 405, 200), fill="gray", width=4)
    out = BytesIO()
    bitmap.save(out, fmt)
    return out.getvalue()


class Provider:
    """A fake OCR provider that returns a fixed result or raises an error."""
    configured = True
    failure = None

    def recognize(self, jpeg):
        """Return the test page after checking that the input is a JPEG."""
        assert jpeg.startswith(b"\xff\xd8")
        if self.failure:
            raise self.failure
        return sample()


def test_rows_order_raw_text_and_validation():
    """Check row order, original text, and warnings for bad values."""
    result = extract(sample(), [])
    assert result.headings == ["Vehicle Number", "Driver", "Date"]
    assert [row.cells for row in result.records] == [
        ["102", "דניאל", "2026-11-08"], ["20S", None, "03/04/2026"],
        ["102", "דניאל", "2026-11-08"], [None, None, None],
    ]
    assert result.raw_text.startswith("Demo delivery list\nVehicle Number")
    assert result.records[0].confidence == [None, None, None]
    assert result.records[1].confidence == [0.39, None, 0.93]
    assert {w.code for w in result.records[1].warnings} == {
        "missing_value", "invalid_number", "ambiguous_date", "low_confidence", "uncertain",
    }
    assert any(w.code == "duplicate_row" for w in result.records[2].warnings)
    assert result.records[3].source_text == "ink across both columns"
    assert any(w.code == "unparsed_row" for w in result.records[3].warnings)


def test_missing_headers_and_unsupported_values():
    """Check fallback headings and cells that do not match the source."""
    page = Recognition(provider="test-double", transcription=Transcription(direction="rtl", lines=[
        line("record", "אבג 123", ["אבג", "999"]),
        line("record", "", [None, None]),
        line("record", "קרית שמונה", ["קרית שמונה"]),
    ]))
    result = extract(page, [])
    assert result.direction == "rtl"
    assert result.headings == ["עמודה 1", "עמודה 2"]
    assert result.records[0].cells == ["אבג", None]
    assert {w.code for w in result.records[0].warnings} >= {"ungrounded_cell", "unassigned_text"}
    assert result.records[1].cells == [None, None]
    assert result.records[2].cells == ["קרית שמונה", None]


def test_layout_preserves_missing_middle_cell_and_numeric_leading_zero():
    """Check that a missing middle value does not move the next column."""
    words = [Word(text, x, y, len(text)*8, 19, 0.9) for text, x, y in [
        ("Vehicle", 10, 20), ("Driver", 150, 20), ("Destination", 330, 20),
        ("010", 11, 65), ("Haifa", 331, 65),
        ("205", 10, 112), ("Maya", 150, 112), ("Tel", 330, 112), ("Aviv", 361, 112),
    ]]
    transcript, scores = layout(words[::-1])
    result = extract(Recognition(provider="test-double", transcription=transcript, confidence=scores), [])
    assert result.headings == ["Vehicle", "Driver", "Destination"]
    assert [row.cells for row in result.records] == [["010", None, "Haifa"], ["205", "Maya", "Tel Aviv"]]


def test_handwriting_mode_selects_its_provider_and_preserves_hebrew():
    """Check that the handwriting route keeps Hebrew and uses its own provider."""
    class Handwriting(Provider):
        """A fake handwriting provider with a Hebrew result."""
        def recognize(self, jpeg):
            """Return a short Hebrew table with right-to-left reading order."""
            assert jpeg.startswith(b"\xff\xd8")
            return Recognition(provider="handwriting-fixture", transcription=Transcription(direction="rtl", lines=[
                line("heading", "שם  יעד", ["שם", "יעד"]),
                line("record", "דניאל  חיפה", ["דניאל", "חיפה"]),
            ]))

    settings = Settings(provider="tesseract")
    client = TestClient(create_app(settings, Provider(), Handwriting()))
    files = {"image": ("sample.png", image(), "image/png")}
    assert client.post("/api/documents/process", files=files).json()["provider"] == "test-double"
    response = client.post("/api/documents/process?mode=handwriting", files=files)
    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "handwriting-fixture" and result["direction"] == "rtl"
    assert result["headings"] == ["שם", "יעד"]
    assert result["records"][0]["cells"] == ["דניאל", "חיפה"]
    unconfigured = TestClient(create_app(settings, Provider()))
    assert unconfigured.get("/health").json()["handwriting_configured"] is False
    rejected = unconfigured.post("/api/documents/process?mode=handwriting", files=files)
    assert rejected.status_code == 503 and rejected.json()["code"] == "handwriting_not_configured"


def test_local_mode_uses_tesseract_when_gemini_is_the_default(monkeypatch):
    """Make sure the local mode stays local even with Gemini as the default."""
    cloud = Provider()
    cloud.failure = AssertionError("Local extraction must never call Gemini")
    monkeypatch.setattr("app.main.TesseractOCR", lambda languages: Provider())
    client = TestClient(create_app(Settings(provider="gemini"), cloud))
    response = client.post("/api/documents/process?mode=local", files={"image": ("sample.png", image(), "image/png")})
    assert response.status_code == 200
    assert response.json()["provider"] == "test-double"


def test_api_processes_image_and_exports_same_reviewed_payload():
    """Export corrected values and check Unicode, quoting, and formula safety."""
    client = TestClient(create_app(Settings(), Provider()))
    assert client.get("/health").json()["ocr_configured"]
    response = client.post("/api/documents/process", files={"image": ("sample.png", image(), "image/png")})
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "review_required" and len(result["records"]) == 4
    corrected = {
        "document_id": result["document_id"], "headings": ["Vehicle", "Driver", "Notes", "=1+1"],
        "rows": [['102', 'דניאל', 'Comma, "quoted"\nnext line', '=HYPERLINK("example")'],
                 ['205', 'מאיה', '-13.5', '  ＋12+2'], ['318', 'רון', 'ok', '\t@SUM(A1)']],
    }
    export = client.post("/api/exports/csv", json=corrected)
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    assert "document_records_" in export.headers["content-disposition"]
    decoded = list(csv.reader(StringIO(export.content.decode("utf-8-sig"))))
    assert decoded == [
        ["Vehicle", "Driver", "Notes", "'=1+1"],
        ["102", "דניאל", 'Comma, "quoted"\nnext line', '\'=HYPERLINK("example")'],
        ["205", "מאיה", "-13.5", "'  ＋12+2"], ["318", "רון", "ok", "'\t@SUM(A1)"],
    ]
    assert client.post("/api/exports/sheets", json=corrected).json()["code"] == "sheets_not_configured"


@pytest.mark.parametrize(("data", "mime", "status", "code"), [
    (b"not a photo", "image/png", 422, "invalid_image"),
    (image(), "image/jpeg", 415, "unsupported_image"),
    (image("GIF"), "image/gif", 415, "unsupported_image"),
    (image(blank=True), "image/png", 422, "blank_image"),
    (b"a" * (MAX_IMAGE_BYTES + 1), "image/png", 413, "image_too_large"),
    (b"a" * (MAX_IMAGE_BYTES + 65537), "image/png", 413, "body_too_large"),
])
def test_upload_rejections(data, mime, status, code):
    """Check the API errors for bad, blank, or oversized images."""
    response = TestClient(create_app(Settings(), Provider())).post(
        "/api/documents/process", files={"image": ("page", data, mime)})
    assert response.status_code == status
    assert response.json()["code"] == code


def test_provider_error_and_no_text_never_look_successful():
    """Check that provider failures and unreadable pages return errors."""
    provider = Provider()
    provider.failure = AppError(502, "ocr_provider_error", "OCR failed.")
    client = TestClient(create_app(Settings(), provider))
    response = client.post("/api/documents/process", files={"image": ("page.png", image(), "image/png")})
    assert response.status_code == 502
    assert response.json() == {"code": "ocr_provider_error", "message": "OCR failed."}
    with pytest.raises(AppError, match="No readable text"):
        extract(Recognition(provider="empty", transcription=Transcription(direction="ltr", lines=[])), [])


@pytest.mark.parametrize("rows", [[["1"]], [[1, "x"]], [["", "  "]], []])
def test_bad_export_shape(rows):
    """Reject invalid cell types, empty rows, and rows of the wrong width."""
    payload = {"document_id": "17fe2881-a6be-40a9-8934-2130cf7b078e", "headings": ["A", "B"], "rows": rows}
    response = TestClient(create_app(Settings(), Provider())).post("/api/exports/csv", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


class ModelClient:
    """A fake Gemini client for testing the OCR adapter."""
    def __init__(self, text, finish=FinishReason.STOP, fail=None):
        """Set the text, finish reason, or error for the next Gemini response."""
        self.models = self
        self.text = text
        self.finish = finish
        self.fail = fail

    def __enter__(self):
        """Return the fake client when the OCR adapter opens it."""
        return self

    def __exit__(self, *exc):
        """Finish the fake client session."""
        pass

    def generate_content(self, **kwargs):
        """Check the OCR request and return the selected fake response."""
        assert kwargs["config"].response_json_schema
        assert "untrusted" in kwargs["config"].system_instruction
        assert kwargs["contents"][0].inline_data.mime_type == "image/jpeg"
        if self.fail:
            raise self.fail
        return SimpleNamespace(text=self.text, candidates=[SimpleNamespace(finish_reason=self.finish)])


@pytest.mark.parametrize(("text", "finish", "fail", "code"), [
    ('{"lines": "bad", "direction": "rtl"}', FinishReason.STOP, None, "ocr_invalid_response"),
    ('{', FinishReason.STOP, None, "ocr_invalid_response"),
    ('{}', FinishReason.MAX_TOKENS, None, "ocr_incomplete"),
    ('', FinishReason.STOP, httpx.ReadTimeout("timeout"), "ocr_timeout"),
])
def test_bad_model_response(monkeypatch, text, finish, fail, code):
    """Check how the adapter reports invalid, incomplete, or slow replies."""
    monkeypatch.setattr("app.ocr.gemini.genai.Client", lambda **_: ModelClient(text, finish, fail))
    with pytest.raises(AppError) as err:
        GeminiOCR("test-key", "test-model").recognize(b"jpeg")
    assert err.value.code == code


def test_model_response_adapter(monkeypatch):
    """Check that valid model JSON becomes usable OCR data."""
    model = ModelClient(sample().transcription.model_dump_json())
    monkeypatch.setattr("app.ocr.gemini.genai.Client", lambda **_: model)
    page = GeminiOCR("test-key", "test-model").recognize(b"jpeg")
    assert page.provider == "gemini/test-model"
    assert len(extract(page, []).records) == 4
    assert not page.confidence


@pytest.mark.parametrize(("primary_status", "backup_status", "error_code"), [
    (None, None, None),
    (429, None, None),
    (503, None, None),
    (429, 429, "ocr_quota"),
    (403, None, "ocr_credentials"),
])
def test_gemini_model_fallback(monkeypatch, primary_status, backup_status, error_code):
    """Check which errors try the backup and what happens if both models fail."""
    class FallbackClient(ModelClient):
        """A fake client that can return a different error for each model."""
        def generate_content(self, **kwargs):
            """Record the model used and return its test response."""
            model = kwargs["model"]
            calls.append(model)
            status = primary_status if model == "primary" else backup_status
            if status:
                raise APIError(status, {"error": {"message": "test error"}})
            return super().generate_content(**kwargs)

    calls = []
    client = FallbackClient(sample().transcription.model_dump_json())
    monkeypatch.setattr("app.ocr.gemini.genai.Client", lambda **_: client)
    recognizer = GeminiOCR("test-key", "primary", "backup")
    uses_backup = primary_status in (429, 503)
    if error_code:
        with pytest.raises(AppError) as err:
            recognizer.recognize(b"jpeg")
        assert err.value.code == error_code
    else:
        result = recognizer.recognize(b"jpeg")
        assert result.provider == ("gemini/backup" if uses_backup else "gemini/primary")
        assert any("backup model" in notice for notice in result.notices) == uses_backup
    assert calls == (["primary", "backup"] if uses_backup else ["primary"])


def test_local_geometry_groups_mildly_tilted_rows():
    """Check that a small tilt does not split a printed row."""
    rows = [
        ["Vehicle", "Driver", "Destination"],
        ["102", "Daniel", "Haifa"],
        ["205", "Maya", "Ashdod"],
    ]
    words = [Word("Weekly report", 12, 10, 104, 23, 0.94)]
    for row, cells in enumerate(rows):
        for x, text in zip((12, 325, 720), cells, strict=True):
            words.append(Word(text, x, 90 + row * 105 - x * 0.052, 8 * len(text), 23, 0.94))
    transcript, confidence = layout(words)
    result = extract(Recognition(provider="tesseract/test", transcription=transcript, confidence=confidence), [])
    assert result.headings == rows[0]
    assert [record.cells for record in result.records] == rows[1:]
    assert "Weekly report" in result.raw_text


def test_local_geometry_keeps_rows_above_a_false_header():
    """Keep the table when a data row happens to contain familiar labels."""
    rows = [
        ["Column A", "Column B"],
        ["Traffic", "Signal"],
        ["Time", "Date"],
        ["Morning", "Tuesday"],
    ]
    words = [
        Word(cell, x, 90 + row * 70, 8 * len(cell), 23, 0.94)
        for row, cells in enumerate(rows)
        for x, cell in zip((12, 325), cells, strict=True)
    ]
    transcript, confidence = layout(words)
    result = extract(Recognition(provider="tesseract/test", transcription=transcript, confidence=confidence), [])
    assert [record.cells for record in result.records] == rows
    assert any(warning.code == "headings_inferred" for warning in result.warnings)


def test_extremely_narrow_image_returns_a_clear_error():
    """Reject a photo that becomes too narrow for the image quality check."""
    photo = Image.new("RGB", (100_000, 120), "white")
    ImageDraw.Draw(photo).rectangle((0, 0, 49_999, 119), fill="black")
    data = BytesIO()
    photo.save(data, format="PNG")
    client = TestClient(create_app(Settings(provider="tesseract")))
    response = client.post("/api/documents/process", files={"image": ("narrow.png", data.getvalue(), "image/png")})
    assert response.status_code == 422
    assert response.json()["code"] == "image_too_narrow"
