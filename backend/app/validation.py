"""Flag suspicious values in extracted rows so the user can check them."""

import re
import unicodedata
from datetime import date

from .models import Record, Warning

NUMERIC = {"vehicle number", "quantity", "qty", "count",
           "odometer", "mileage", "כמות", "מספר רכב", "קילומטראז"}
DATES = {"date", "delivery date", "inspection date", "תאריך", "תאריך מסירה"}
TIMES = {"time", "delivery time", "arrival time", "departure time", "שעה", "שעת מסירה"}
LABELS = NUMERIC | DATES | TIMES | {
    "vehicle", "vehicle id", "רכב", "driver", "destination", "name", "item", "equipment", "status", "notes",
    "result", "check", "description", "location", "route", "order",
    "נהג", "יעד", "שם", "פריט", "ציוד", "מצב", "הערות", "בדיקה", "תוצאה",
}


def check_date(value: str) -> str | None:
    """Flag invalid dates and dates with an unclear day and month order."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return "invalid_date"
        return None
    match = re.fullmatch(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{2}|\d{4})", value)
    if not match:
        return "invalid_date"
    first, _, second, year = match.groups()
    a, b, y = int(first), int(second), int(year)
    possibilities = []
    for month, day in [(a, b), (b, a)]:
        try:
            possibilities.append(date(y + 2000 if len(year) == 2 else y, month, day))
        except ValueError:
            continue
    if not possibilities:
        return "invalid_date"
    if len(year) == 2 or len(set(possibilities)) > 1:
        return "ambiguous_date"
    return None


def validate_records(headings: list[str], records: list[Record]) -> None:
    """Add warnings for values, low confidence, and repeated rows.

    Use the column names to decide which checks apply. Leave the original
    values as they are so the user can make the final decision.
    """
    seen: set[tuple[str, ...]] = set()
    for record in records:
        for index, (heading, cell, confidence) in enumerate(zip(headings, record.cells, record.confidence, strict=True)):
            value = (cell or "").strip()
            key = heading.strip().casefold()
            issue = None
            if not value:
                issue = ("missing_value", "This value is blank or could not be read.")
            elif key in NUMERIC and not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", value):
                issue = ("invalid_number", "This field looks numeric. Check the value.")
            elif key in TIMES and not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", value):
                issue = ("invalid_time", "Check this time. The validator expects a 24-hour value such as 09:30.")
            elif key in DATES:
                problem = check_date(value)
                if problem:
                    issue = (problem, "Check the date and its day/month order.")
            if issue:
                record.warnings.append(Warning(code=issue[0], message=issue[1], column=index))
            if confidence is not None and confidence < 0.72:
                record.warnings.append(Warning(
                    code="low_confidence", message="OCR confidence is low. Compare with the photo.", column=index
                ))
        normalized = tuple(unicodedata.normalize("NFC", (value or "").strip()).casefold() for value in record.cells)
        if any(normalized) and normalized in seen:
            record.warnings.append(Warning(code="duplicate_row", message="An earlier row has the same values."))
        seen.add(normalized)
