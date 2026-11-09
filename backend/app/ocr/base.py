"""Shared data types for the local and cloud OCR providers."""

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..models import CellText, Confidence, MAX_COLUMNS, MAX_ROWS


class ImageLine(BaseModel):
    """One OCR line with its role, source text, cells, and uncertain positions."""
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["heading", "record", "note"]
    source_text: Annotated[str, Field(max_length=16000)]
    cells: list[CellText | None] = Field(max_length=MAX_COLUMNS)
    uncertain: list[int] = Field(max_length=MAX_COLUMNS)


class Transcription(BaseModel):
    """The page reading direction and the recognized lines in order."""
    model_config = ConfigDict(extra="forbid", strict=True)

    direction: Literal["ltr", "rtl"]
    lines: list[ImageLine] = Field(max_length=MAX_ROWS + 20)


class Recognition(BaseModel):
    """A transcription with the provider name and any confidence or notes."""
    transcription: Transcription
    provider: str
    confidence: dict[int, list[Confidence | None]] = Field(default_factory=dict)
    notices: list[str] = Field(default_factory=list)


class OCRProvider(Protocol):
    """The interface that both OCR providers follow."""
    @property
    def configured(self) -> bool:
        """Say whether this provider is ready according to its settings."""
        ...
    def recognize(self, jpeg: bytes) -> Recognition:
        """Read a prepared JPEG and return the recognized page."""
        ...
