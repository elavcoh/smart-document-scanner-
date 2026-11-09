"""Read and write one Sheets data tab and keep retries from adding extra rows."""

import hashlib
import json
import threading
from contextlib import contextmanager
from unicodedata import bidirectional
from urllib.parse import quote
from uuid import UUID

import requests
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from .config import Settings
from .errors import AppError
from .models import MAX_COLUMNS, MAX_ROWS, ReviewedTable, SheetDocument, SheetDocumentEdit, SheetHistory, SheetsResult

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
METADATA_KEY = "smart_document_scanner_export"
SCHEMA_KEY = "smart_document_scanner_schema"
WRITE_LOCK = threading.Lock()
DOCUMENT_COLUMN = "_scanner_document_id"
DOCUMENT_VIEW_KEY = "smart_document_scanner_view"


def digest(text: str) -> str:
    """Make a hash for comparing the exact text of a saved value or payload."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def metadata_id(key: str) -> int:
    """Make a repeatable positive ID from a scanner metadata key."""
    return 1 + int(digest(key)[:12], 16) % 2_147_483_646


def right_to_left(headings: list[str]) -> bool:
    """Guess the sheet direction from the letters in its headings."""
    directions = [bidirectional(character) for heading in headings for character in heading]
    return directions.count("R") + directions.count("AL") > directions.count("L")


def metadata_request(id_: int, key: str, value: str, tab_id: int) -> dict:
    """Build the Sheets API request for a marker attached to a tab."""
    return {"createDeveloperMetadata": {"developerMetadata": {
        "metadataId": id_, "metadataKey": key, "metadataValue": value,
        "location": {"sheetId": tab_id}, "visibility": "DOCUMENT",
    }}}


class SheetsClient:
    """Handle Google Sheets requests for one data tab."""
    def __init__(self, settings: Settings, session: AuthorizedSession, *, export_scope: int | None = None):
        """Store the session, workbook, tab, and scope used for upload IDs."""
        self.export_scope = export_scope
        self.session = session
        self.spreadsheet_id = settings.sheets_spreadsheet_id
        self.tab = settings.sheets_tab
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(self.spreadsheet_id, safe='')}"

    def request(self, method: str, suffix: str = "", *, allow_missing: bool = False, **kwargs):
        """Call Google Sheets and turn HTTP or connection failures into app errors."""
        try:
            response = self.session.request(method, self.base + suffix, timeout=(5, 15), **kwargs)
        except (TransportError, requests.RequestException) as exc:
            raise AppError(502, "sheets_network", "The backend could not reach Google Sheets.") from exc
        except GoogleAuthError as exc:
            raise AppError(503, "sheets_credentials", "Google rejected the backend service account credentials.") from exc
        if allow_missing and response.status_code == 404:
            return None
        if response.status_code in (401, 403, 404):
            raise AppError(503, "sheets_access", "Enable the Sheets API, check the spreadsheet ID, and share it with the service account as Editor.")
        if not response.ok:
            raise AppError(502, "sheets_api_error", "Google Sheets rejected the request or is temporarily unavailable.")
        try:
            body = response.json()
            if isinstance(body, dict):
                return body
            raise ValueError("Expected an object")
        except ValueError as exc:
            raise AppError(502, "sheets_invalid_response", "Google Sheets returned an unreadable response.") from exc

    def marker(self, id_: int):
        """Read a metadata marker by ID. A missing marker can be a normal result."""
        return self.request("GET", f"/developerMetadata/{id_}", allow_missing=True)

    def tab_properties(self, include_metadata=False):
        """Return the properties of the selected data tab."""
        return self.workbook_tab(include_metadata)[0]

    def workbook_sheets(self, include_metadata=False):
        """List workbook tabs with only the fields we need."""
        fields = "properties(sheetId,title,sheetType,gridProperties(columnCount))"
        if include_metadata:
            fields += ",developerMetadata(metadataKey,metadataValue)"
        return self.request("GET", params={"fields": f"sheets({fields})"}).get("sheets", [])

    def workbook_tab(self, include_metadata):
        """Find the selected grid tab and return it with the workbook tab list."""
        sheets = self.workbook_sheets(include_metadata)
        sheet = next((item for item in sheets
                      if item["properties"]["title"] == self.tab), {})
        tab = sheet.get("properties")
        if tab is None or tab.get("sheetType", "GRID") != "GRID":
            raise AppError(409, "sheets_tab_missing", f'Create a grid tab named "{self.tab}" in the configured spreadsheet.')
        if include_metadata:
            tab = {**tab, "metadata": sheet.get("developerMetadata", [])}
        return tab, sheets

    def grid_values(self):
        """Read the used grid and keep formula text as entered."""
        a1 = "'" + self.tab.replace("'", "''") + "'"
        return self.request("GET", f"/values/{quote(a1, safe='')}",
                            params={"valueRenderOption": "FORMULA"}).get("values", [])

    @staticmethod
    def layout(grid):
        """Read the headings and check whether they include the hidden ID column."""
        headings = [str(value) for value in grid[0]] if grid else []
        tagged = bool(headings and headings[-1] == DOCUMENT_COLUMN)
        return (headings[:-1] if tagged else headings), tagged

    @staticmethod
    def update_cells(tab_id, row, column, cells):
        """Build an update for literal text cells, including cells to clear."""
        return {"updateCells": {
            "start": {"sheetId": tab_id, "rowIndex": row, "columnIndex": column},
            "fields": "userEnteredValue",
            "rows": [{"values": [{"userEnteredValue": {"stringValue": cell}} if cell else {} for cell in cells]}],
        }}

    def initialize_document_column(self, tab, grid, width):
        """Make room for the hidden IDs without overwriting existing data."""
        if any(len(row) > width and any(value != "" for value in row[width:]) for row in grid[1:]):
            raise AppError(409, "sheets_schema_changed", "The column after the headings contains data. Add a heading before saving.")
        tab_id = tab["sheetId"]
        columns = tab.get("gridProperties", {}).get("columnCount")
        requests = []
        if columns is not None and width >= columns:
            requests.append({"appendDimension": {"sheetId": tab_id, "dimension": "COLUMNS",
                                                 "length": width + 1 - columns}})
        if grid:
            requests.append(self.update_cells(tab_id, 0, width, [DOCUMENT_COLUMN]))
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": width, "endIndex": width + 1},
            "properties": {"hiddenByUser": True}, "fields": "hiddenByUser",
        }})
        return requests

    def document_groups(self, tab, grid, headings, tagged):
        """Group rows by their saved document ID.

        For older uploads, use the stored hash to recover exact matching blocks.
        Leave rows together as ungrouped when there is no reliable match.
        """
        width = len(headings)
        groups = {}
        legacy = {}
        for index, row in enumerate(grid[1:], start=1):
            cells = [str(value) for value in row[:width]] + [""] * (width - len(row))
            if not any(cells):
                continue
            document_id = None
            if tagged and len(row) > width:
                try:
                    document_id = UUID(str(row[width]))
                except ValueError:
                    pass
            if document_id:
                groups.setdefault(document_id, []).append((index, cells))
            else:
                legacy[index] = cells

        # Old uploads stored a hash of the reviewed document. Recover only blocks
        # that match an original upload exactly; keep unmatched data together.
        markers = {}
        for entry in tab["metadata"]:
            if entry["metadataKey"] != METADATA_KEY:
                continue
            raw_id, _, checksum = entry["metadataValue"].partition(":")
            try:
                document_id = UUID(raw_id)
            except ValueError:
                continue
            if document_id not in groups:
                markers[document_id] = checksum
        if len(legacy) <= MAX_ROWS and len(markers) <= 100:
            remaining = set(markers)
            for start in list(legacy):
                candidates = []
                for index in range(start, start + MAX_ROWS):
                    if index not in legacy or not remaining:
                        break
                    candidates.append((index, legacy[index]))
                    rows = [cells for _, cells in candidates]
                    match = next((doc for doc in sorted(remaining)
                                  if digest(ReviewedTable.model_construct(
                                      document_id=doc, headings=headings, rows=rows,
                                  ).model_dump_json()) == markers[doc]), None)
                    if match:
                        groups[match] = candidates
                        remaining.remove(match)
                        for row, _ in candidates:
                            del legacy[row]
                        break
        if legacy:
            groups[None] = list(legacy.items())
        return dict(sorted(groups.items(), key=lambda item: item[1][0][0]))

    def spreadsheet_url(self, tab_id: int) -> str:
        """Build a Google Sheets link to the requested tab."""
        return f"https://docs.google.com/spreadsheets/d/{quote(self.spreadsheet_id, safe='')}/edit#gid={tab_id}"

    def open_document_sheet(self, document_id: UUID) -> str:
        """Reuse or build a view that shows only this document's rows.

        The view uses a filter formula and the hidden document IDs. It reads
        from the source tab, so a saved edit also shows up in the view.
        """
        tab, sheets = self.workbook_tab(include_metadata=True)
        grid = self.grid_values()
        headings, tagged = self.layout(grid)
        groups = self.document_groups(tab, grid, headings, tagged)
        rows = groups.get(document_id)
        if not rows:
            raise AppError(404, "document_not_found", "This document is no longer in history. Refresh and try again.")

        view_id = metadata_id(f"scanner:view:{tab['sheetId']}:{document_id}")
        existing = next((sheet for sheet in sheets if sheet["properties"]["sheetId"] == view_id), None)
        url = f"https://docs.google.com/spreadsheets/d/{quote(self.spreadsheet_id, safe='')}/edit?gid={view_id}#gid={view_id}"
        view_key = f"{tab['sheetId']}:{document_id}"
        if existing:
            if any(item["metadataKey"] == DOCUMENT_VIEW_KEY and item["metadataValue"] == view_key
                   for item in existing.get("developerMetadata", [])):
                return url
            raise AppError(409, "sheets_view_conflict", "A different sheet occupies this document view. Contact the spreadsheet owner.")

        width = len(headings)
        if not 1 <= width <= MAX_COLUMNS:
            raise AppError(409, "sheets_schema_changed", "This document has an unsupported number of columns.")
        changes = [] if tagged else self.initialize_document_column(tab, grid, width)
        for index, _ in rows:
            if len(grid[index]) <= width or not grid[index][width]:
                changes.append(self.update_cells(tab["sheetId"], index, width, [str(document_id)]))

        def column_letter(number):
            """Turn a one-based column number into a Sheet column name."""
            return chr(64 + number) if number <= 26 else "A" + chr(64 + number - 26)

        source = "'" + self.tab.replace("'", "''") + "'"
        id_column = column_letter(width + 1)
        ids = f"{source}!{id_column}:{id_column}"
        values = f"{source}!A:{column_letter(width)}"
        formula = f'=FILTER({values},((ROW({ids})=1)+({ids}="{document_id}"))>0)'
        number = list(groups).index(document_id) + 1
        changes.extend([
            {"addSheet": {"properties": {"sheetId": view_id, "title": f"Document {number} · {document_id.hex[:8]}",
                                        "rightToLeft": right_to_left(headings),
                                        "gridProperties": {"rowCount": max(2, len(rows) + 1),
                                                           "columnCount": width, "frozenRowCount": 1}}}},
            {"createDeveloperMetadata": {"developerMetadata": {
                "metadataKey": DOCUMENT_VIEW_KEY, "metadataValue": view_key,
                "location": {"sheetId": view_id}, "visibility": "DOCUMENT",
            }}},
            {"updateCells": {"start": {"sheetId": view_id, "rowIndex": 0, "columnIndex": 0},
                             "fields": "userEnteredValue",
                             "rows": [{"values": [{"userEnteredValue": {"formulaValue": formula}}]}]}},
        ])
        self.request("POST", ":batchUpdate", json={"requests": changes})
        return url

    def history(self, tab=None) -> SheetHistory:
        """Read the tab and return its rows grouped by document."""
        if tab is None:
            tab = self.tab_properties(include_metadata=True)
        grid = self.grid_values()
        headings, tagged = self.layout(grid)
        groups = self.document_groups(tab, grid, headings, tagged)
        return SheetHistory(documents=[
            SheetDocument(document_id=document_id, headings=headings, rows=[cells for _, cells in rows])
            for document_id, rows in groups.items()
        ])

    def get_document(self, document_id: UUID) -> ReviewedTable:
        """Find one document in this tab and return it as a reviewed table."""
        history = self.history()
        document = next((item for item in history.documents if item.document_id == document_id), None)
        if not document:
            raise AppError(404, "document_not_found", "This document is no longer in history.")
        return ReviewedTable.model_construct(document_id=document_id, headings=document.headings, rows=document.rows)

    def update_document(self, edit: SheetDocumentEdit) -> SheetDocument:
        """Save changed cells only after comparing with the original rows.

        If the current rows already match the requested edit, the previous save
        may have worked. Return that result without writing the same edit again.
        """
        tab = self.tab_properties(include_metadata=True)
        grid = self.grid_values()
        headings, tagged = self.layout(grid)
        rows = self.document_groups(tab, grid, headings, tagged).get(edit.document_id)
        current = [cells for _, cells in rows] if rows else []
        if headings != edit.headings or current not in (edit.original, edit.rows):
            raise AppError(409, "sheets_record_changed", "This document or its columns changed. Refresh history before editing.")
        result = SheetDocument(document_id=edit.document_id, headings=headings, rows=edit.rows)
        if current == edit.rows:
            return result
        changes = [] if tagged else self.initialize_document_column(tab, grid, len(headings))
        for (row, before), after in zip(rows, edit.rows):
            for index, (old, new) in enumerate(zip(before, after)):
                if old != new:
                    changes.append(self.update_cells(tab["sheetId"], row, index, [new]))
            if len(grid[row]) <= len(headings) or not grid[row][len(headings)]:
                changes.append(self.update_cells(tab["sheetId"], row, len(headings), [str(edit.document_id)]))
        response = self.request("POST", ":batchUpdate", json={"requests": changes})
        if len(response.get("replies", [])) != len(changes):
            raise AppError(502, "sheets_outcome_unknown", "The edit could not be confirmed. Retry or refresh history.")
        return result

    def append(self, table: ReviewedTable) -> SheetsResult:
        """Append rows and a matching upload marker in one batch.

        If Google saves the batch but the reply gets lost, use the marker to
        confirm the same upload. Reject a different payload using the same ID.
        """
        tab = self.tab_properties()
        tab_id = tab["sheetId"]
        scope = self.export_scope if self.export_scope is not None else tab_id
        marker_id = metadata_id(f"scanner:export:{scope}:{table.document_id}")
        payload_hash = digest(table.model_dump_json())
        value = f"{table.document_id}:{payload_hash}"
        schema_id = metadata_id(f"scanner:schema:{tab_id}")

        def result(replayed: bool) -> SheetsResult:
            """Build the upload receipt for a fresh write or a confirmed retry."""
            return SheetsResult(
                spreadsheet_id=self.spreadsheet_id,
                spreadsheet_url=self.spreadsheet_url(tab_id),
                rows_written=len(table.rows), replayed=replayed,
            )

        def confirmed(existing) -> bool:
            """Check if this marker confirms the same tab, document, and payload."""
            if existing is None:
                return False
            if (existing.get("metadataKey") != METADATA_KEY
                or existing.get("location", {}).get("sheetId") != tab_id
                or existing.get("metadataValue", "").partition(":")[0] != str(table.document_id)):
                raise AppError(409, "sheets_marker_conflict", "This document's upload ID conflicts with existing spreadsheet metadata.")
            if existing.get("metadataValue") != value:
                raise AppError(409, "sheets_already_exported", "This document was uploaded with different reviewed values. Use its original values to confirm it.")
            return True

        if confirmed(self.marker(marker_id)):
            return result(replayed=True)

        # A dedicated demo tab is expected to be small. Read the full used grid so a
        # blank first row with existing data below it cannot be mistaken for an empty tab.
        current = self.grid_values()
        headings, tagged = self.layout(current)
        if DOCUMENT_COLUMN in table.headings or (current and headings != table.headings):
            raise AppError(409, "sheets_heading_mismatch", "The target tab has different headings. Use a blank tab or match the headings and their order.")
        if not current and self.marker(schema_id) is not None:
            # Another first write may have just committed between the grid and marker reads.
            if confirmed(self.marker(marker_id)):
                return result(replayed=True)
            raise AppError(409, "sheets_schema_changed", "The tab changed while preparing this upload. Retry the same upload or select a new blank tab if it was cleared.")

        rows = [[*row, str(table.document_id)] for row in table.rows]
        if not current:
            rows.insert(0, [*table.headings, DOCUMENT_COLUMN])
        # stringValue is the batchUpdate equivalent of values.append with RAW.
        # Both the append and its unique operation marker are atomic at Google.
        batch = [metadata_request(marker_id, METADATA_KEY, value, tab_id)]
        if not current:
            batch.append(metadata_request(schema_id, SCHEMA_KEY, digest(json.dumps(table.headings)), tab_id))
            if right_to_left(table.headings):
                batch.append({"updateSheetProperties": {
                    "properties": {"sheetId": tab_id, "rightToLeft": True}, "fields": "rightToLeft",
                }})
        if not tagged:
            batch.extend(self.initialize_document_column(tab, current, len(table.headings)))
        batch.append({"appendCells": {
            "sheetId": tab_id,
            "fields": "userEnteredValue",
            "rows": [{"values": [{"userEnteredValue": {"stringValue": cell}} for cell in row]} for row in rows],
        }})
        try:
            response = self.request("POST", ":batchUpdate", json={"requests": batch})
            if len(response.get("replies", [])) == len(batch):
                return result(replayed=False)
        except AppError as exc:
            if exc.code in ("sheets_credentials", "sheets_access"):
                raise
        # A disconnected client or concurrent retry can lose the write response.
        # A retry with the same document can never append twice while its marker exists.
        try:
            existing = self.marker(marker_id)
        except AppError:
            existing = None
        if confirmed(existing):
            return result(replayed=True)
        raise AppError(502, "sheets_outcome_unknown", "The upload could not be confirmed. Retry this same reviewed document to check or complete it.")


@contextmanager
def sheets_client(settings: Settings):
    """Open an authorized Sheets session using the configured account file."""
    if not settings.sheets_configured:
        raise AppError(503, "sheets_not_configured", "Set the Sheets credentials file and spreadsheet ID on the backend.")
    try:
        credentials = Credentials.from_service_account_file(settings.sheets_credentials, scopes=[SCOPE])
    except (OSError, ValueError, GoogleAuthError) as exc:
        raise AppError(503, "sheets_credentials", "The backend could not load the service account file. Check its path and format.") from exc
    with AuthorizedSession(credentials, refresh_timeout=10) as session:
        yield SheetsClient(settings, session)
