"""Find which Google Sheets tab holds a document.

Keep tables with different headings in separate data tabs.
Read them together in history and send each edit to its own data tab.
"""
import json
from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID

from .config import Settings
from .errors import AppError
from .models import ReviewedTable, SheetDocumentEdit, SheetHistory
from .sheets_export import DOCUMENT_COLUMN, METADATA_KEY, SheetsClient, digest, metadata_id, metadata_request, sheets_client

LAYOUT_TAB_KEY = "smart_document_scanner_layout"


class SheetDocuments:
    """Find the right Sheets data tab for each document."""
    def __init__(self, settings: Settings, base: SheetsClient):
        """Find the main tab and other data tabs that belong to it."""
        self.settings = settings
        self.base = base
        self.primary, sheets = base.workbook_tab(include_metadata=True)
        self.scope = self.primary["sheetId"]
        self.tabs = [
            {**sheet["properties"], "metadata": sheet.get("developerMetadata", [])}
            for sheet in sheets
            if sheet["properties"]["sheetId"] == self.scope or any(
                item["metadataKey"] == LAYOUT_TAB_KEY
                and item["metadataValue"].startswith(f"{self.scope}:")
                for item in sheet.get("developerMetadata", [])
            )
        ]

    def client(self, tab):
        """Create a client for this tab while keeping the same upload scope."""
        return SheetsClient(replace(self.settings, sheets_tab=tab["title"]),
                            self.base.session, export_scope=self.scope)

    def uploaded_tab(self, document_id: UUID):
        """Find the tab that already has an upload marker for the document."""
        return next((tab for tab in self.tabs if any(
            marker["metadataKey"] == METADATA_KEY
            and marker["metadataValue"].partition(":")[0] == str(document_id)
            for marker in tab["metadata"]
        )), None)

    def document_client(self, document_id: UUID):
        """Get the client for a saved document or return a not found error."""
        tab = self.uploaded_tab(document_id)
        if tab is None:
            raise AppError(404, "document_not_found", "This document is no longer in history.")
        return self.client(tab)

    def layout_tab(self, headings: list[str]):
        """Find or create a data tab for this exact set and order of headings.

        Use a short hash so the same headings can find the same tab again.
        """
        checksum = digest(json.dumps(headings, ensure_ascii=False))
        layout = f"{self.scope}:{checksum}"
        for tab in self.tabs:
            if any(item["metadataKey"] == LAYOUT_TAB_KEY and item["metadataValue"] == layout
                   for item in tab["metadata"]):
                return tab
        tab_id = metadata_id(f"scanner:layout:{layout}")
        title = f"{self.settings.sheets_tab[:72]} · {checksum[:10]}"
        self.base.request("POST", ":batchUpdate", json={"requests": [
            {"addSheet": {"properties": {"sheetId": tab_id, "title": title,
                                         "gridProperties": {"frozenRowCount": 1}}}},
            metadata_request(metadata_id(f"scanner:layout-marker:{layout}"), LAYOUT_TAB_KEY, layout, tab_id),
        ]})
        return {"sheetId": tab_id, "title": title}

    def append(self, table: ReviewedTable):
        """Choose where to save a reviewed table.

        Check the previous upload first for retries, then pick a tab by the headings.
        """
        previous = self.uploaded_tab(table.document_id)
        if previous:
            return self.client(previous).append(table)
        if DOCUMENT_COLUMN in table.headings:
            raise AppError(409, "sheets_heading_mismatch", "Choose a different name for the reserved scanner column.")
        grid = self.base.grid_values()
        current_headings, _ = self.base.layout(grid)
        tab = self.primary if not grid or current_headings == table.headings else self.layout_tab(table.headings)
        return self.client(tab).append(table)

    def history(self):
        """Combine the documents from all data tabs into one list."""
        return SheetHistory(documents=[
            document for tab in self.tabs for document in self.client(tab).history(tab).documents
        ])

    def get_document(self, document_id: UUID):
        """Read the rows for one saved document from its data tab."""
        return self.document_client(document_id).get_document(document_id)

    def update_document(self, edit: SheetDocumentEdit):
        """Send changes to the tab that holds the saved document."""
        return self.document_client(edit.document_id).update_document(edit)

    def open_document_sheet(self, document_id: UUID):
        """Open the separate view for the document from the right data tab."""
        return self.document_client(document_id).open_document_sheet(document_id)


@contextmanager
def sheet_documents(settings: Settings):
    """Open a Sheets session and close it when the request is done."""
    with sheets_client(settings) as client:
        yield SheetDocuments(settings, client)
