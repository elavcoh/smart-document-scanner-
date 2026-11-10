"""Read the settings used by the server.

The project .env can set the OCR mode, Gemini models, and Sheets access.
This file also keeps the shared size and time limits in one place.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
MAX_IMAGE_BYTES = 12 * 1024 * 1024
OCR_TIMEOUT = 55


@dataclass(frozen=True)
class Settings:
    """Options for local OCR, Gemini, and the connected spreadsheet."""
    provider: Literal["gemini", "tesseract"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    tesseract_languages: str = "eng+heb"
    sheets_credentials: str = ""
    sheets_spreadsheet_id: str = ""
    sheets_tab: str = "Scans"

    @property
    def sheets_configured(self) -> bool:
        """Check if both required Sheets settings are present."""
        return bool(self.sheets_credentials and self.sheets_spreadsheet_id)

    @classmethod
    def from_env(cls):
        """Load .env from the project folder and read the server options.

        Use the defaults when an option is missing and check the default OCR provider.
        """
        load_dotenv(ROOT / ".env")
        provider = os.getenv("OCR_PROVIDER", "gemini")
        if provider not in ("gemini", "tesseract"):
            raise ValueError("OCR_PROVIDER must be gemini or tesseract.")
        return cls(
            provider=provider,
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", cls.gemini_model),
            gemini_fallback_model=os.getenv("GEMINI_FALLBACK_MODEL", cls.gemini_fallback_model),
            tesseract_languages=os.getenv("TESSERACT_LANGUAGES", cls.tesseract_languages),
            sheets_credentials=os.getenv("SHEETS_CREDENTIALS_FILE", ""),
            sheets_spreadsheet_id=os.getenv("SHEETS_SPREADSHEET_ID", ""),
            sheets_tab=os.getenv("SHEETS_TAB", cls.sheets_tab),
        )
