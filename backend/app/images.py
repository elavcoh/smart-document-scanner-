"""Check each uploaded photo before running OCR.

Fix its rotation, reduce large images, and turn it into a JPEG.
Return warnings for weak photos and reject files we cannot read.
"""

from io import BytesIO

from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from .config import MAX_IMAGE_BYTES
from .errors import AppError
from .models import Warning

MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MIN_SIDE = 120
MAX_PIXELS = 32_000_000


def prepare_image(data: bytes, mime: str | None) -> tuple[bytes, list[Warning]]:
    """Turn one supported image into a JPEG and report quality problems.

    Check the file and pixel limits first. Fix the image rotation and warn about
    dark or blurry photos. Reject blank photos before calling an OCR provider.
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError(413, "image_too_large", "Choose an image under 12 MiB.")
    if mime not in {*MIME_TYPES.values(), "application/octet-stream", None}:
        raise AppError(415, "unsupported_image", "Choose a JPEG, PNG or WebP image.")
    try:
        with Image.open(BytesIO(data)) as original:
            if original.format not in MIME_TYPES or mime not in (
                None, "application/octet-stream", MIME_TYPES[original.format]
            ):
                raise AppError(415, "unsupported_image", "Choose a valid JPEG, PNG or WebP image.")
            if original.width * original.height > MAX_PIXELS:
                raise AppError(413, "image_too_large", "Choose an image under 32 megapixels.")
            if min(original.size) < MIN_SIDE:
                raise AppError(422, "image_too_small", "Retake the photo at a higher resolution.")
            if getattr(original, "n_frames", 1) != 1:
                raise AppError(415, "unsupported_image", "Choose a still image of one page.")
            image = ImageOps.exif_transpose(original)
            image.thumbnail((2600, 2600))
            if "A" in image.getbands() or "transparency" in image.info:
                rgba = image.convert("RGBA")
                white = Image.new("RGB", rgba.size, "white")
                white.paste(rgba, mask=rgba.getchannel("A"))
                image = white
            else:
                image = image.convert("RGB")
    except Image.DecompressionBombError as exc:
        raise AppError(413, "image_too_large", "Choose an image under 32 megapixels.") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AppError(422, "invalid_image", "The selected file could not be decoded.") from exc

    # Use a small gray copy for the quality checks. Keep the color image for OCR.
    gray = image.convert("L")
    gray.thumbnail((850, 850))
    if min(gray.size) <= 2:
        raise AppError(422, "image_too_narrow", "Choose a photo that shows the full page.")
    brightness, contrast = ImageStat.Stat(gray).mean[0], ImageStat.Stat(gray).stddev[0]
    if contrast < 1.5:
        raise AppError(422, "blank_image", "The image appears blank. Try retaking the photo.")
    warnings = []
    if brightness < 68:
        warnings.append(Warning(code="dark_image", message="The image is dark. Check faint writing or retake the photo."))
    edges = gray.filter(ImageFilter.FIND_EDGES).crop((1, 1, gray.width - 1, gray.height - 1))
    if contrast < 12 or ImageStat.Stat(edges).stddev[0] < 4:
        warnings.append(Warning(code="low_detail", message="The image may be blurry or low contrast. Check the result against the photo."))
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=94)
    return buffer.getvalue(), warnings
