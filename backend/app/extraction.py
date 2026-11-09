"""Turn OCR lines into a table the user can review."""

import re
import unicodedata
from uuid import uuid4

from .errors import AppError
from .models import MAX_ROWS, ProcessResult, Record, Warning
from .ocr.base import ImageLine, Recognition
from .validation import validate_records


def normalized_text(value: str) -> str:
    """Make spacing and Unicode consistent for text comparisons."""
    return unicodedata.normalize("NFC", " ".join(value.split()))


def evidence(cell: str, source: str) -> re.Match | None:
    """Find a proposed cell in its source text without matching inside a word."""
    value = normalized_text(cell)
    if not value or "\x00" in value:
        return None
    left = r"(?<!\w)" if value[0].isalnum() else ""
    right = r"(?!\w)" if value[-1].isalnum() else ""
    return re.search(left + re.escape(value) + right, source)


def grounded(cell: str, source: str) -> bool:
    """Check whether the source line contains the proposed cell."""
    return evidence(cell, normalized_text(source)) is not None


def headings_for(line: ImageLine | None, width: int, rtl: bool) -> list[str]:
    """Use readable headings and make fallback names unique."""
    headings, used = [], set()
    for index in range(width):
        candidate = line.cells[index] if line and index < len(line.cells) else None
        label = candidate.strip() if candidate and grounded(candidate, line.source_text) else ""
        if not label or len(label) > 120 or "\x00" in label:
            if width == 1:
                label = "טקסט" if rtl else "Text"
            else:
                label = f"עמודה {index + 1}" if rtl else f"Column {index + 1}"
        base, suffix = label, 2
        while label.casefold() in used:
            label = f"{base[:110]} ({suffix})"
            suffix += 1
        used.add(label.casefold())
        headings.append(label)
    return headings


def extract(recognition: Recognition, image_warnings: list[Warning], duration_ms: int = 0) -> ProcessResult:
    """Build reviewable rows from the provider's transcription.

    Each cell has to match text in the same source line. Once matched, that
    piece of text cannot be used for another cell. Keep uncertain rows so the
    user can compare them with the photo and fill in missing values.
    """
    page = recognition.transcription
    raw_text = "\n".join(line.source_text for line in page.lines)
    if not raw_text.strip() and not any(line.role == "record" for line in page.lines):
        raise AppError(422, "no_text", "No readable text was detected. Retake the photo or enter records manually.")
    header_index = next((i for i, line in enumerate(page.lines) if line.role == "heading" and line.cells), None)
    header = page.lines[header_index] if header_index is not None else None
    indexed = [(i, line) for i, line in enumerate(page.lines)
               if line.role == "record" or (line.role == "heading" and i != header_index)]
    warnings = [*image_warnings, *(Warning(code="provider_notice", message=note) for note in recognition.notices)]
    if not indexed:
        indexed = [(i, line) for i, line in enumerate(page.lines) if line.source_text.strip()]
        header = None
        warnings.append(Warning(code="no_clear_records", message="No clear table was found. Review the detected text lines."))
    if not indexed:
        raise AppError(422, "no_text", "No readable text was detected. Retake the photo or enter records manually.")
    if len(indexed) > MAX_ROWS:
        raise AppError(422, "too_many_rows", "Use an image with no more than 300 records.")
    if header is None:
        warnings.append(Warning(code="headings_inferred", message="No clear header was detected. Rename the columns as needed."))
    elif header.uncertain or any(not cell or not grounded(cell, header.source_text) for cell in header.cells):
        warnings.append(Warning(code="uncertain_heading", message="One or more column headings are unclear. Check them against the photo."))

    width = max(1, len(header.cells) if header else 0, *(len(line.cells) for _, line in indexed))
    headings = headings_for(header, width, page.direction == "rtl")
    records = []
    for index, line in indexed:
        cells = [*line.cells, *([None] * (width - len(line.cells)))]
        confidence = [*recognition.confidence.get(index, [])][:width]
        confidence.extend([None] * (width - len(confidence)))
        row_warnings = []
        remaining = normalized_text(line.source_text)
        for column, value in enumerate(cells):
            if column in line.uncertain:
                row_warnings.append(Warning(code="uncertain", message="The provider flagged this cell. Check the photo.", column=column))
            if value is None:
                continue
            match = evidence(value, remaining)
            if match is None:
                cells[column], confidence[column] = None, None
                row_warnings.append(Warning(code="ungrounded_cell", message="This value was omitted because it did not match the row transcript.", column=column))
            else:
                remaining = remaining[:match.start()] + "\x00" + remaining[match.end():]
        if not any(cells) and line.source_text.strip():
            if width == 1 and len(line.source_text.strip()) <= 3000:
                cells[0] = line.source_text.strip()
            row_warnings.append(Warning(code="unparsed_row", message="Check the source text and assign these values to columns."))
        elif any(cells):
            if any(character.isalnum() for character in remaining):
                row_warnings.append(Warning(code="unassigned_text", message="Some source text was not assigned to a column."))
        else:
            row_warnings.append(Warning(code="unreadable_row", message="A row was detected, but its text could not be read."))
        records.append(Record(cells=cells, source_text=line.source_text, confidence=confidence, warnings=row_warnings))
    validate_records(headings, records)
    return ProcessResult(document_id=uuid4(), provider=recognition.provider, raw_text=raw_text,
                         headings=headings, records=records, warnings=warnings,
                         direction=page.direction, duration_ms=duration_ms)
