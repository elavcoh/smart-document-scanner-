"""Tests for Sheets uploads, history, editing, and safe retries."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import pytest
import requests
from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError, TransportError

from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.models import ReviewedTable, SheetDocumentEdit
from app.sheets_documents import SheetDocuments
from app.sheets_export import DOCUMENT_COLUMN, METADATA_KEY, SheetsClient, digest, sheets_client

SETTINGS = Settings(sheets_spreadsheet_id="example", sheets_credentials="/test/credentials.json", sheets_tab="Demo's Scans")


def payload(document_id=None, value="=SUM(1,2)"):
    """Make a reviewed table with a fixed set of example rows."""
    return ReviewedTable(document_id=document_id or uuid4(), headings=["Vehicle", "Notes"],
                         rows=[["010", "שלום"], ["205", value]])


def response(status, data=None):
    """Make a small fake HTTP response for the Sheets tests."""
    return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: data or {})


class FakeSheets:
    """Minimal REST double with atomic metadata ID uniqueness."""

    def __init__(self, rows=None):
        """Set up a data tab and the options for simulating failures."""
        self.tabs = {42: {"title": SETTINGS.sheets_tab, "rows": rows or [], "column_count": 26}}
        self.metadata = {}
        self.calls = []
        self.writes = 0
        self.fail = None
        self.fail_after_write = False
        self.fail_post_before_write = False

    @property
    def rows(self):
        """Return the rows in the main data tab."""
        return self.tabs[42]["rows"]

    @property
    def column_count(self):
        """Return the width of the main data tab."""
        return self.tabs[42]["column_count"]

    def request(self, method, url, **kwargs):
        """Read the fake workbook or apply a batch of Sheets updates."""
        self.calls.append((method, url, kwargs))
        assert kwargs["timeout"] == (5, 15)
        if self.fail:
            raise self.fail
        if "/developerMetadata/" in url:
            item = self.metadata.get(int(url.split("/")[-1]))
            return response(200, item) if item else response(404)
        if "/values/" in url:
            tab = next(tab for tab in self.tabs.values()
                       if url.endswith("/values/" + quote("'" + tab["title"].replace("'", "''") + "'", safe="")))
            assert kwargs["params"] == {"valueRenderOption": "FORMULA"}
            return response(200, {"values": tab["rows"]})
        if method == "GET":
            return response(200, {"sheets": [
                {"properties": {"title": tab["title"], "sheetId": tab_id, "sheetType": "GRID",
                                "gridProperties": {"columnCount": tab["column_count"]}},
                 "developerMetadata": [item for item in self.metadata.values() if item["location"]["sheetId"] == tab_id]}
                for tab_id, tab in self.tabs.items()
            ]})
        if self.fail_post_before_write:
            raise requests.Timeout("before remote apply")
        assert url.endswith(":batchUpdate")
        batch = kwargs["json"]["requests"]
        markers = [req["createDeveloperMetadata"]["developerMetadata"] for req in batch if "createDeveloperMetadata" in req]
        if any(item["metadataId"] in self.metadata for item in markers):
            return response(400)
        for req in batch:
            if "addSheet" in req:
                sheet = req["addSheet"]["properties"]
                self.tabs[sheet["sheetId"]] = {"title": sheet["title"], "rows": [], "column_count": 26}
            if "appendDimension" in req:
                expansion = req["appendDimension"]
                assert expansion["dimension"] == "COLUMNS"
                self.tabs[expansion["sheetId"]]["column_count"] += expansion["length"]
            if "updateCells" in req:
                update = req["updateCells"]
                row = self.tabs[update["start"]["sheetId"]]["rows"][update["start"]["rowIndex"]]
                for index, cell in enumerate(update["rows"][0]["values"], update["start"]["columnIndex"]):
                    row.extend([""] * max(0, index + 1 - len(row)))
                    row[index] = cell.get("userEnteredValue", {}).get("stringValue", "")
            if "appendCells" in req:
                append = req["appendCells"]
                assert append["fields"] == "userEnteredValue"
                tab = self.tabs[append["sheetId"]]
                rows = [[cell["userEnteredValue"]["stringValue"] for cell in row["values"]] for row in append["rows"]]
                assert all(len(row) <= tab["column_count"] for row in rows)
                tab["rows"].extend(rows)
        self.metadata.update({item["metadataId"]: item for item in markers})
        self.writes += 1
        if self.fail_after_write:
            raise requests.Timeout("after remote commit")
        return response(200, {"replies": [{} for _ in batch]})


def test_sheets_raw_order_headings_and_independent_batches():
    """Check literal cells, row order, and separate document uploads."""
    fake = FakeSheets()
    client = SheetsClient(SETTINGS, fake)
    table = payload()
    receipt = client.append(table)
    assert receipt.rows_written == 2 and not receipt.replayed
    assert receipt.spreadsheet_url.endswith("edit#gid=42")
    assert fake.rows == [[*table.headings, DOCUMENT_COLUMN], *[[*row, str(table.document_id)] for row in table.rows]]
    assert client.append(table).replayed
    assert fake.writes == 1
    second = payload(value="reviewed")
    client.append(second)
    assert [document.rows for document in client.history().documents] == [table.rows, second.rows]
    assert fake.writes == 2


def test_confirm_lost_success_after_backend_restart():
    """Retry a committed upload using a new client and check that it is still saved once."""
    fake = FakeSheets()
    fake.fail_after_write = True
    table = payload()
    assert SheetsClient(SETTINGS, fake).append(table).replayed
    assert SheetsClient(SETTINGS, fake).append(table).replayed
    assert fake.writes == 1
    with pytest.raises(AppError) as err:
        SheetsClient(SETTINGS, fake).append(payload(table.document_id, value="different"))
    assert err.value.code == "sheets_already_exported"


def test_unconfirmed_is_never_success_and_same_payload_can_retry():
    """Check that a failed write can be retried with the same payload."""
    fake = FakeSheets()
    fake.fail_post_before_write = True
    client = SheetsClient(SETTINGS, fake)
    table = payload()
    with pytest.raises(AppError) as err:
        client.append(table)
    assert err.value.code == "sheets_outcome_unknown"
    assert fake.writes == 0
    fake.fail_post_before_write = False
    assert client.append(table).rows_written == 2
    assert fake.writes == 1


@pytest.mark.parametrize("rows", [[["Other"], ["existing"]], [[], ["existing"]]])
def test_wrong_columns_do_not_touch_existing_rows(rows):
    """Reject a bad heading layout without changing the existing sheet."""
    fake = FakeSheets(rows)
    with pytest.raises(AppError) as err:
        SheetsClient(SETTINGS, fake).append(payload())
    assert err.value.code == "sheets_heading_mismatch"
    assert fake.writes == 0


@pytest.mark.parametrize(("failure", "expected"), [
    (RefreshError("invalid"), "sheets_credentials"),
    (requests.ConnectionError("disconnected"), "sheets_network"),
    (TransportError("token endpoint unreachable"), "sheets_network"),
])
def test_auth_and_network_errors(failure, expected):
    """Check how Sheets connection and login errors are reported."""
    fake = FakeSheets()
    fake.fail = failure
    with pytest.raises(AppError) as err:
        SheetsClient(SETTINGS, fake).append(payload())
    assert err.value.code == expected


def test_missing_sheet_permissions():
    """Check the error shown when the service account has no access."""
    fake = SimpleNamespace(request=lambda *_, **__: response(403))
    with pytest.raises(AppError) as err:
        SheetsClient(SETTINGS, fake).append(payload())
    assert err.value.code == "sheets_access"


def test_bad_service_account_file():
    """Check the error for a missing or unreadable account key file."""
    with pytest.raises(AppError) as err:
        with sheets_client(SETTINGS) as client:
            client.append(payload())
    assert err.value.code == "sheets_credentials"


@pytest.mark.parametrize("same_values", [True, False])
def test_concurrent_uploads_with_same_document_id(monkeypatch, same_values):
    """Start two uploads together and check that only one write is made."""
    class Session(FakeSheets):
        """Let the in-memory workbook act as an authorized session."""
        def __enter__(self):
            """Return the fake session when a route opens it."""
            return self

        def __exit__(self, *exc):
            """Finish the fake Sheets session."""
            pass

    session = Session()
    monkeypatch.setattr("app.sheets_export.Credentials.from_service_account_file", lambda *a, **kw: None)
    monkeypatch.setattr("app.sheets_export.AuthorizedSession", lambda *a, **kw: session)
    app = create_app(SETTINGS)
    table = payload()
    other = table if same_values else payload(table.document_id, value="Conflicting edit")
    start = Barrier(2, timeout=5)

    def upload(candidate):
        """Wait for the other worker, then send the reviewed table to the API."""
        with TestClient(app) as client:
            start.wait()
            return client.post("/api/exports/sheets", json=candidate.model_dump(mode="json"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(upload, (table, other)))

    assert sorted(reply.status_code for reply in replies) == ([200, 200] if same_values else [200, 409])
    confirmed = [reply.json() for reply in replies if reply.status_code == 200]
    assert all(receipt["rows_written"] == 2 for receipt in confirmed)
    assert sum(receipt["replayed"] for receipt in confirmed) == int(same_values)
    if not same_values:
        assert next(reply.json()["code"] for reply in replies if reply.status_code == 409) == "sheets_already_exported"
    winner = table if replies[0].status_code == 200 else other
    assert session.writes == 1
    assert session.rows[1:] == [[*row, str(table.document_id)] for row in winner.rows]


def test_blank_sheet_expands_for_wide_schema():
    """Add enough columns for a wide table and the hidden document ID."""
    fake = FakeSheets()
    headings = [f"Column {index}" for index in range(1, 31)]
    row = ["sample"] + [""] * 29
    table = ReviewedTable(document_id=uuid4(), headings=headings, rows=[row])
    result = SheetsClient(SETTINGS, fake).append(table)
    assert result.rows_written == 1
    assert fake.column_count == 31 and fake.rows == [[*headings, DOCUMENT_COLUMN], [*row, str(table.document_id)]]


def test_edit_legacy_document_keeps_other_upload_separate():
    """Edit an older upload without touching its neighbor and reject stale changes."""
    first, second = payload(), payload(value="Other upload")
    fake = FakeSheets([first.headings[:], *[row[:] for row in first.rows + second.rows]])
    fake.metadata = {
        i: {"metadataKey": METADATA_KEY, "metadataValue": f"{table.document_id}:{digest(table.model_dump_json())}",
            "location": {"sheetId": 42}}
        for i, table in enumerate((first, second), 1)
    }
    client = SheetsClient(SETTINGS, fake)
    assert [doc.document_id for doc in client.history().documents] == [first.document_id, second.document_id]
    edit = SheetDocumentEdit(document_id=first.document_id, headings=first.headings,
                             original=first.rows, rows=[["011", "Updated"], first.rows[1]])
    client.update_document(edit)
    client.update_document(edit)
    assert fake.writes == 1
    documents = client.history().documents
    assert [doc.rows for doc in documents] == [edit.rows, second.rows]
    assert len(fake.rows) == 5
    with pytest.raises(AppError, match="changed"):
        client.update_document(edit.model_copy(update={"rows": first.rows}))


def test_document_sheet_tags_legacy_rows_and_reuses_its_view():
    """Tag only the selected rows and reuse the same document view on repeat opens."""
    first, second = payload(), payload(value="Another document")
    grid = [first.headings, *first.rows, *second.rows]
    client = SheetsClient(SETTINGS, None)
    source = {"sheetId": 42, "title": SETTINGS.sheets_tab, "metadata": [
        {"metadataKey": METADATA_KEY, "metadataValue": f"{doc.document_id}:{digest(doc.model_dump_json())}"}
        for doc in (first, second)
    ]}
    views, batches = [], []
    client.workbook_tab = lambda include_metadata: (source, views)
    client.grid_values = lambda: grid

    def commit(method, suffix, **kwargs):
        """Record the batch and make the new view visible to later reads."""
        assert (method, suffix) == ("POST", ":batchUpdate")
        batch = kwargs["json"]["requests"]
        batches.append(batch)
        views.append({"properties": batch[-3]["addSheet"]["properties"],
                      "developerMetadata": [batch[-2]["createDeveloperMetadata"]["developerMetadata"]]})
        return {"replies": [{} for _ in batch]}

    client.request = commit
    link = client.open_document_sheet(first.document_id)
    assert client.open_document_sheet(first.document_id) == link
    assert len(batches) == 1
    batch = batches[0]
    source_updates = [item["updateCells"] for item in batch if "updateCells" in item
                      and item["updateCells"]["start"]["sheetId"] == 42]
    assert [item["start"]["rowIndex"] for item in source_updates] == [0, 1, 2]
    assert all(item["start"]["columnIndex"] == 2 for item in source_updates)
    assert str(first.document_id) in batch[-1]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]["formulaValue"]
    assert f"?gid={views[0]['properties']['sheetId']}" in link


def test_english_and_hebrew_uploads_keep_their_own_headings_and_edits():
    """Keep different heading layouts separate while sharing the same history."""
    fake = FakeSheets()
    base = SheetsClient(SETTINGS, fake)
    english = payload()
    hebrew = ReviewedTable(document_id=uuid4(), headings=["רכב", "נהג"], rows=[["010", "דניאל"]])
    SheetDocuments(SETTINGS, base).append(english)
    result = SheetDocuments(SETTINGS, base).append(hebrew)
    assert not result.spreadsheet_url.endswith("#gid=42")
    history = SheetDocuments(SETTINGS, base).history()
    assert [(item.headings, item.rows) for item in history.documents] == [
        (english.headings, english.rows), (hebrew.headings, hebrew.rows),
    ]
    assert SheetDocuments(SETTINGS, base).append(hebrew).replayed
    assert len(fake.tabs) == 2
    assert SheetDocuments(SETTINGS, base).get_document(hebrew.document_id) == hebrew
    edit = SheetDocumentEdit(document_id=hebrew.document_id, headings=hebrew.headings,
                             original=hebrew.rows, rows=[["010", "דניאלה"]])
    SheetDocuments(SETTINGS, base).update_document(edit)
    assert SheetDocuments(SETTINGS, base).get_document(hebrew.document_id).rows == edit.rows
    assert SheetDocuments(SETTINGS, base).get_document(english.document_id).rows == english.rows
    with pytest.raises(AppError) as error:
        SheetDocuments(SETTINGS, base).append(hebrew.model_copy(update={"headings": english.headings}))
    assert error.value.code == "sheets_already_exported"
