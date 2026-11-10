"""Define the data that goes in and out of the API.

OCR results have proposed values and warnings. Reviewed tables have checked values.
These models also check that headings and rows fit before we export or save.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_COLUMNS = 32
MAX_ROWS = 300
CellText = Annotated[str, Field(max_length=3000)]
Heading = Annotated[str, Field(min_length=1, max_length=120)]
Confidence = Annotated[float, Field(ge=0, le=1)]


class Warning(BaseModel):
    """A message about the page, a row, or one cell."""
    code: str
    message: str
    column: int | None = None


class Record(BaseModel):
    """A row with its text, proposed cells, and OCR warnings."""
    cells: list[str | None]
    source_text: str
    confidence: list[Confidence | None]
    warnings: list[Warning]


class ProcessResult(BaseModel):
    """The OCR result the API sends to the review screen.

    It has the raw text, proposed headings and rows, reading direction, and warnings.
    """
    document_id: UUID
    provider: str
    raw_text: str
    headings: list[str]
    records: list[Record]
    warnings: list[Warning]
    direction: Literal["ltr", "rtl"]
    status: Literal["review_required"] = "review_required"
    duration_ms: int


class ReviewedTable(BaseModel):
    """The table the user has checked and can export.

    The same document ID and row values can go to either CSV or Sheets.
    """
    model_config = ConfigDict(extra="forbid", strict=True)

    document_id: UUID = Field(strict=False)
    headings: list[Heading] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[Annotated[list[CellText], Field(min_length=1, max_length=MAX_COLUMNS)]] = Field(
        min_length=1, max_length=MAX_ROWS
    )

    @model_validator(mode="after")
    def validate_table(self):
        """Check that headings and rows form a table we can export.

        Reject invalid or repeated headings and check that each row fits the table.
        """
        if any(not heading.strip() for heading in self.headings):
            raise ValueError("Name each column before exporting.")
        if len({heading.strip().casefold() for heading in self.headings}) != len(self.headings):
            raise ValueError("Use a distinct name for each column.")
        if any(len(row) != len(self.headings) for row in self.rows):
            raise ValueError("Each row must have one cell per heading.")
        if any(not any(cell.strip() for cell in row) for row in self.rows):
            raise ValueError("Fill or remove wholly empty rows before exporting.")
        if any("\x00" in cell for row in [self.headings, *self.rows] for cell in row):
            raise ValueError("Remove null characters before exporting.")
        return self


class SheetDocument(BaseModel):
    """A document loaded from Sheets. Older ungrouped rows can have no ID."""
    document_id: UUID | None
    headings: list[str]
    rows: list[list[str]]


class SheetHistory(BaseModel):
    """The list of documents available in the connected workbook."""
    documents: list[SheetDocument]


class SheetDocumentEdit(ReviewedTable):
    """The new rows plus the original rows from when editing started.

    The server uses the old rows to see if the saved data changed during editing.
    """
    original: list[list[CellText]] = Field(min_length=1, max_length=MAX_ROWS)

    @model_validator(mode="after")
    def validate_original(self):
        """Make sure the old and new tables have the same shape."""
        if len(self.original) != len(self.rows) or any(len(row) != len(self.headings) for row in self.original):
            raise ValueError("Keep the original number of rows and columns.")
        return self


class SheetsResult(BaseModel):
    """Confirmation that the rows were saved or had already been saved."""
    status: Literal["confirmed"] = "confirmed"
    spreadsheet_id: str
    spreadsheet_url: str
    rows_written: int
    updated_range: str | None = None
    replayed: bool
