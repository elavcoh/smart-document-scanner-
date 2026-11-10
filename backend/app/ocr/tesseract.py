"""Use local Tesseract to read printed text from a JPEG.

Tesseract gives us words with positions and scores.
Use the positions to find rows and columns, then return a shared OCR result.
"""

import csv
import io
import re
import shutil
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

from ..config import OCR_TIMEOUT
from ..errors import AppError
from ..models import MAX_COLUMNS, MAX_ROWS
from ..validation import LABELS
from .base import ImageLine, Recognition, Transcription

BIDI_MARKS = str.maketrans("", "", "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
HEBREW = re.compile(r"[\u05d0-\u05ea]")


@dataclass
class Word:
    """A recognized word with its position, size, and confidence."""
    text: str
    x: int
    y: float
    width: int
    height: int
    confidence: float

    @property
    def right(self) -> int:
        """Return the x position of the right edge."""
        return self.x + self.width


def parse_words(tsv: str) -> list[Word]:
    """Read word boxes and confidence scores from Tesseract's TSV output."""
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"text", "conf", "left", "top", "width", "height"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("Incomplete TSV header")
    words = []
    for row in reader:
        value = (row["text"] or "").translate(BIDI_MARKS).strip()
        confidence = float(row["conf"])
        if value and 0 <= confidence <= 100:
            height = int(row["height"])
            words.append(Word(value, int(row["left"]), int(row["top"]) + height / 2,
                              int(row["width"]), height, confidence / 100))
    return words


def text_of(segment: list[Word]) -> str:
    # Reverse visual word order for Hebrew runs while keeping contiguous Latin/numeric runs together.
    """Join one group of words in reading order.

    Use the text direction while keeping nearby words of the same direction together.
    """
    joined = " ".join(word.text for word in segment)
    if not HEBREW.search(joined):
        return joined
    groups: list[list[str]] = []
    for word in segment:
        if groups and not HEBREW.search(groups[-1][0]) and not HEBREW.search(word.text):
            groups[-1].append(word.text)
        else:
            groups.append([word.text])
    return " ".join(" ".join(group) for group in reversed(groups))


def skew_slope(words: list[Word], scale: float) -> float:
    """Estimate mild tilt from pairs of distant, readable words on the same baseline."""
    clear = sorted((word for word in words if word.confidence >= 0.45), key=lambda word: word.y)
    clear = clear[::max(1, (len(clear) + 119) // 120)]
    votes: dict[int, list[float]] = defaultdict(list)
    for first, second in combinations(clear, 2):
        dx = second.x + second.width / 2 - first.x - first.width / 2
        if abs(dx) < 4 * scale:
            continue
        slope = (second.y - first.y) / dx
        if abs(slope) <= 0.09:
            votes[round(slope / 0.009)].append(slope)
    if not votes:
        return 0
    winner = max(votes, key=lambda key: (len(votes[key]), -abs(key)))
    if abs(winner) < 2 or len(votes[winner]) < max(4, 1.5 * len(votes.get(0, []))):
        return 0
    return statistics.median(votes[winner])


def split_rows(words: list[Word]) -> tuple[list[list[list[Word]]], float]:
    """Group word boxes that sit at about the same height.

    Use a tilt estimate first, then split each row at wide gaps to find cells.
    """
    scale = statistics.median(word.height for word in words)
    slope = skew_slope(words, scale)

    def baseline(word: Word) -> float:
        """Estimate the word's vertical position after removing the page tilt."""
        return word.y - slope * (word.x + word.width / 2)

    bands: list[list[Word]] = []
    for word in sorted(words, key=baseline):
        if bands and baseline(word) - statistics.median(map(baseline, bands[-1])) <= 0.6 * scale:
            bands[-1].append(word)
        else:
            bands.append([word])
    rows = []
    for band in bands:
        chunks: list[list[Word]] = []
        right = -1.0
        for word in sorted(band, key=lambda item: item.x):
            if not chunks or word.x - right > scale * 1.45:
                chunks.append([])
            chunks[-1].append(word)
            right = max(right, word.right)
        rows.append(chunks)
    return rows, scale


def layout(words: list[Word]) -> tuple[Transcription, dict[int, list[float | None]]]:
    """Turn word boxes into a transcription with optional column positions.

    Look for a clear header or repeated column positions. Keep the source
    line when the boxes do not give enough evidence to place the cells.
    """
    rtl = sum(len(HEBREW.findall(w.text)) for w in words) > sum(
        len(re.findall("[A-Za-z]", w.text)) for w in words
    )
    if not words:
        return Transcription(direction="ltr", lines=[]), {}
    rows, scale = split_rows(words)
    if len(rows) > MAX_ROWS:
        raise AppError(422, "too_many_rows", "Use an image with no more than 300 records.")
    def anchor(chunk: list[Word]) -> int:
        """Pick a horizontal position for this group of words."""
        return max(w.right for w in chunk) if rtl else chunk[0].x

    def repeated_columns(candidate: list[list[Word]], other: list[list[Word]]) -> bool:
        """Check if another row lines up with the same column positions."""
        positions = [anchor(chunk) for chunk in candidate]
        near = set()
        for chunk in other:
            offset = anchor(chunk)
            closest = min(positions, key=lambda position: abs(position - offset))
            if abs(closest - offset) <= 2.2 * scale:
                near.add(closest)
        return len(near) >= 2

    candidate = next((
        (i, row) for i, row in enumerate(rows)
        if 2 <= len(row) <= MAX_COLUMNS
        and sum(text_of(chunk).strip().casefold() in LABELS for chunk in row) >= 2
    ), None)
    # A row with familiar labels can be data. Keep earlier aligned rows in review.
    if candidate and not any(repeated_columns(candidate[1], row) for row in rows[:candidate[0]]):
        header, reference = candidate
    else:
        header = -1
        reference = next((
            row for row in sorted(rows, key=len, reverse=True)
            if 2 <= len(row) <= MAX_COLUMNS
            and any(other is not row and repeated_columns(row, other) for other in rows)
        ), [])

    anchors = [anchor(chunk) for chunk in reference]
    if rtl:
        anchors.reverse()

    lines, confidence = [], {}
    for i, row in enumerate(rows):
        ordered = row[::-1] if rtl else row
        raw = "  ".join(text_of(chunk) for chunk in ordered)
        role = "heading" if i == header else "note" if 0 <= i < header else "record"
        cells: list[str | None] = [None] * len(anchors)
        scores: list[float | None] = [None] * len(anchors)
        if anchors:
            for chunk in ordered:
                index = min(range(len(anchors)), key=lambda j: abs(anchor(chunk) - anchors[j]))
                if cells[index] is not None or abs(anchor(chunk) - anchors[index]) > 2.2 * scale:
                    cells, scores = [], []
                    break
                cells[index] = text_of(chunk)
                scores[index] = min(word.confidence for word in chunk)
        if not anchors:
            cells = [raw]
            scores = [min(word.confidence for chunk in ordered for word in chunk)]
        lines.append(ImageLine(role=role, source_text=raw, cells=cells, uncertain=[]))
        confidence[i] = scores
    return Transcription(direction="rtl" if rtl else "ltr", lines=lines), confidence


class TesseractOCR:
    """Run Tesseract on the Mac and turn its output into the shared OCR format."""
    def __init__(self, languages: str):
        """Store the language packs to use for recognition."""
        self.languages = languages

    @property
    def configured(self) -> bool:
        """Check if the Tesseract program is available."""
        return shutil.which("tesseract") is not None

    def recognize(self, jpeg: bytes) -> Recognition:
        """Send the JPEG bytes to the local Tesseract program.

        Read its TSV output into word boxes, then build the lines and confidence scores.
        Stop the OCR call after the time limit.
        """
        if not self.configured:
            raise AppError(503, "ocr_not_configured", "Install Tesseract and its English and Hebrew language data.")
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", self.languages, "--psm", "11", "-c", "tessedit_create_tsv=1"],
                input=jpeg, capture_output=True, timeout=OCR_TIMEOUT, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppError(504, "ocr_timeout", "Local OCR timed out. Try a smaller image.") from exc
        except OSError as exc:
            raise AppError(503, "ocr_unavailable", "The backend could not start Tesseract.") from exc
        if result.returncode != 0 or b"Failed loading language" in result.stderr:
            raise AppError(502, "ocr_provider_error", "Tesseract could not read the image. Check the installed language data.")
        try:
            page, confidence = layout(parse_words(result.stdout.decode("utf-8")))
        except (ValueError, TypeError) as exc:
            raise AppError(502, "ocr_invalid_response", "Tesseract returned unreadable OCR data.") from exc
        return Recognition(transcription=page, confidence=confidence, provider=f"tesseract/{self.languages}", notices=[
            "Local OCR is intended for print. Compare handwriting with the photo and correct or add any missed rows.",
        ])
