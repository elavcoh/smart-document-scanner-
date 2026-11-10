"""Start the FastAPI server and define the API routes.

The routes handle uploads, CSV, Google Sheets, and the browser editor.
They pass the work to smaller files and turn errors into API replies.
"""

from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import MAX_IMAGE_BYTES, Settings
from .csv_export import open_csv_on_mac, render_csv
from .errors import AppError
from .extraction import extract
from .images import prepare_image
from .models import ProcessResult, ReviewedTable, SheetDocument, SheetDocumentEdit, SheetHistory, SheetsResult
from .ocr.base import OCRProvider
from .ocr.gemini import GeminiOCR
from .ocr.tesseract import TesseractOCR
from .sheets_documents import sheet_documents
from .sheets_export import WRITE_LOCK


WEB_DIR = Path(__file__).with_name("web")
WEB_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
}


class BoundedBody:
    """Apply limits to received bytes before parsing multipart or JSON."""

    def __init__(self, app: ASGIApp):
        """Keep the app that will receive requests after the size check."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        """Count the request bytes before FastAPI reads the upload or JSON.

        Stop early if the body is too big. Send valid requests on to the API.
        """
        path = scope.get("path", "")
        if scope["type"] != "http" or scope["method"] != "POST" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        limit = MAX_IMAGE_BYTES + 65536 if path.rstrip("/") == "/api/documents/process" else 4 * 1024 * 1024
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await JSONResponse(
                    {"code": "body_too_large", "message": "The upload exceeds the allowed size."},
                    status_code=413,
                )(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def buffered_receive():
            """Give the checked body to the app once, then forward later events."""
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, buffered_receive, send)


def build_provider(settings: Settings) -> OCRProvider:
    """Create the default OCR provider from the server settings.

    Use the model names for Gemini or the language packs for Tesseract.
    """
    if settings.provider == "gemini":
        return GeminiOCR(settings.gemini_api_key, settings.gemini_model, settings.gemini_fallback_model)
    return TesseractOCR(settings.tesseract_languages)


def create_app(settings: Settings | None = None, provider: OCRProvider | None = None,
               handwriting_provider: OCRProvider | None = None) -> FastAPI:
    """Set up the API routes, error handlers, and available OCR modes.

    Tests can pass fake providers here so they can exercise the real routes
    without calling Gemini or running Tesseract.
    """
    settings = settings or Settings.from_env()
    provider = provider or build_provider(settings)
    handwriting_provider = handwriting_provider or (
        provider if settings.provider == "gemini" else
        GeminiOCR(settings.gemini_api_key, settings.gemini_model, settings.gemini_fallback_model)
    )
    recognizers = {
        "default": provider,
        "handwriting": handwriting_provider,
        "local": provider if settings.provider == "tesseract" else TesseractOCR(settings.tesseract_languages),
    }
    app = FastAPI(title="Smart Document Scanner", version="1.0.0")
    app.add_middleware(BoundedBody)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        """Return a known application error in the format the app expects."""
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        """Turn invalid request fields into a short list of issues."""
        issues = [{"path": [str(part) for part in item["loc"]], "message": item["msg"]} for item in exc.errors()[:12]]
        return JSONResponse({"code": "invalid_request", "message": "Check the required inputs and table limits.", "issues": issues}, status_code=422)

    @app.get("/health")
    def health():
        """Return a quick check for the app connection screen.

        Show which services have settings in place. This does not run OCR or open Sheets.
        """
        return {"status": "ok", "ocr_provider": settings.provider, "ocr_configured": provider.configured,
                "sheets_configured": settings.sheets_configured,
                "handwriting_configured": handwriting_provider.configured}

    def process(data: bytes, mime: str | None, recognizer: OCRProvider) -> ProcessResult:
        """Take the uploaded bytes and MIME type and prepare a JPEG.

        Run the chosen OCR provider, build review rows, and include the time used.
        """
        start = perf_counter()
        jpeg, image_warnings = prepare_image(data, mime)
        result = extract(recognizer.recognize(jpeg), image_warnings)
        result.duration_ms = round((perf_counter() - start) * 1000)
        return result

    @app.post("/api/documents/process", response_model=ProcessResult)
    async def process_document(image: Annotated[UploadFile, File(description="One JPEG, PNG or WebP page")],
                               mode: Literal["default", "handwriting", "local"] = "default"):
        """Read one photo and choose the OCR provider for the requested mode.

        Run the image work in a worker thread so it does not block the async API.
        """
        try:
            recognizer = recognizers[mode]
            if mode == "handwriting" and not recognizer.configured:
                raise AppError(503, "handwriting_not_configured", "Set GEMINI_API_KEY on the Mac to enable handwriting recognition.")
            data = await image.read(MAX_IMAGE_BYTES + 1)
            return await run_in_threadpool(process, data, image.content_type, recognizer)
        finally:
            await image.close()

    @app.post("/api/exports/csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
    def export_csv(table: ReviewedTable):
        """Return the confirmed table as a downloadable CSV attachment."""
        data, filename = render_csv(table)
        return Response(data, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                                 "Cache-Control": "no-store"})

    @app.post("/api/exports/csv/mac")
    def export_csv_to_mac(table: ReviewedTable, request: Request) -> dict[str, str]:
        """Save and open the reviewed CSV for a local request."""
        if request.headers.get("origin") or request.url.hostname not in ("127.0.0.1", "::1", "localhost", "10.0.2.2"):
            raise AppError(403, "csv_mac_local_only", "Open on Mac is available from the scanner connected to the local Mac backend.")
        return {"filename": open_csv_on_mac(table, request.client.host if request.client else "")}

    @app.post("/api/exports/sheets", response_model=SheetsResult)
    def export_sheets(table: ReviewedTable):
        """Take the checked rows and send them to the correct Sheets tab.

        Hold the write lock while saving so requests in this server take turns.
        """
        with WRITE_LOCK, sheet_documents(settings) as documents:
            return documents.append(table)

    @app.get("/api/records", response_model=SheetHistory)
    def record_history(response: Response):
        """Load the current list of documents from Sheets without caching."""
        response.headers["Cache-Control"] = "no-store"
        with sheet_documents(settings) as client:
            return client.history()

    @app.get("/documents/{document_id}", include_in_schema=False)
    def document_editor(document_id: UUID):
        """Serve the browser editor page for a document ID."""
        return FileResponse(WEB_DIR / "document.html", media_type="text/html", headers=WEB_HEADERS)

    @app.get("/document-editor.js", include_in_schema=False)
    def document_editor_script():
        """Serve the JavaScript used by the browser editor."""
        return FileResponse(WEB_DIR / "document.js", media_type="text/javascript", headers=WEB_HEADERS)

    @app.get("/api/records/{document_id}", response_model=ReviewedTable)
    def get_document(document_id: UUID, response: Response):
        """Read the latest rows for one document from Sheets."""
        response.headers["Cache-Control"] = "no-store"
        with sheet_documents(settings) as client:
            return client.get_document(document_id)

    @app.post("/api/records/{document_id}/sheet")
    def open_document_sheet(document_id: UUID) -> dict[str, str]:
        """Find or create the Sheet view that shows this document's rows."""
        with WRITE_LOCK, sheet_documents(settings) as client:
            return {"spreadsheet_url": client.open_document_sheet(document_id)}

    @app.post("/api/records/edit", response_model=SheetDocument)
    def update_document(edit: SheetDocumentEdit):
        """Take the old and new rows from the phone or browser.

        Send the edit to the document store while holding the write lock.
        """
        with WRITE_LOCK, sheet_documents(settings) as client:
            return client.update_document(edit)

    return app


app = create_app()
