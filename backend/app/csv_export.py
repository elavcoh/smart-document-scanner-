"""Make CSV files from tables the user has checked.

Use the same CSV output for download and for opening a file on the Mac.
Protect cells that a spreadsheet could read as formulas.
"""

import csv
import io
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .errors import AppError
from .models import ReviewedTable

SIGNED_NUMBER = re.compile(r"^[+-][0-9]+(?:[.,][0-9]+)?$")


def safe_cell(value: str) -> str:
    """Escape values that a spreadsheet could treat as a formula.

    Check past spaces and invisible characters. Keep plain signed numbers
    as they are so values like -13.5 still work as numbers.
    """
    normalized = unicodedata.normalize("NFKC", value)
    trimmed = next((normalized[index:] for index, character in enumerate(normalized)
                    if not character.isspace() and unicodedata.category(character) not in {"Cc", "Cf"}), "")
    if value.startswith(("\t", "\r", "\n")) or (
        trimmed.startswith(("=", "+", "-", "@"))
        and not SIGNED_NUMBER.fullmatch(trimmed)
    ):
        return "'" + value
    return value


def render_csv(table: ReviewedTable) -> tuple[bytes, str]:
    """Take the reviewed headings and rows and make UTF-8 CSV bytes.

    Use the CSV writer for quotes and line breaks. Return a file name with the time.
    """
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    for row in [table.headings, *table.rows]:
        writer.writerow([safe_cell(cell) for cell in row])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return output.getvalue().encode("utf-8-sig"), f"document_records_{stamp}.csv"


def open_csv_on_mac(table: ReviewedTable, client_host: str) -> str:
    """Save a CSV in Downloads and open it with the Mac's file handler.

    Only a local caller can do this. Keep the saved file if opening it fails.
    """
    if sys.platform != "darwin" or client_host not in ("127.0.0.1", "::1"):
        raise AppError(403, "csv_mac_local_only", "Open on Mac is available when the scanner connects locally to its Mac backend.")
    data, filename = render_csv(table)
    downloads = Path.home() / "Downloads"
    try:
        with NamedTemporaryFile(dir=downloads, prefix=Path(filename).stem + "_", suffix=".csv", delete=False) as file:
            file.write(data)
        path = Path(file.name)
    except OSError as exc:
        raise AppError(500, "csv_mac_save_failed", "Could not save the CSV in Downloads on the Mac.") from exc
    try:
        subprocess.run(["/usr/bin/open", str(path)], check=True, capture_output=True, timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppError(502, "csv_mac_open_failed", f"Saved {path.name} in Mac Downloads. Open it there with Excel or Numbers.") from exc
    return path.name
