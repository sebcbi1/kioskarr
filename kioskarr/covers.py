"""Cover thumbnail extraction for OPDS entries. Supported formats: PDF (render page 0) and
CBZ (first image in the archive, by name). Anything else — CBR, EPUB, MOBI, or a corrupt/
unreadable file in a supported format — is out of scope; callers fall back to a generic
placeholder image instead, since a missing cover must never be a hard error.

The generated cover lives right next to the issue file itself (same name, .jpg extension)
rather than in a separate cache directory — no new setting needed, and it doubles as a real
cover for any tool that scans the library folder directly (Komga, Kavita, Calibre all look
for a same-name image next to a book/comic file).
"""

import logging
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from kioskarr.models import Issue

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = (600, 600)
_JPEG_QUALITY = 80
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def _cover_path_for(issue: Issue) -> Path:
    return Path(issue.file_path).with_suffix(".jpg")


def _extract_from_pdf(source: Path) -> Image.Image:
    pdf = pdfium.PdfDocument(str(source))
    try:
        page = pdf[0]
        return page.render(scale=2).to_pil().convert("RGB")
    finally:
        pdf.close()


def _extract_from_cbz(source: Path) -> Image.Image:
    with zipfile.ZipFile(source) as archive:
        names = sorted(
            name
            for name in archive.namelist()
            if not name.startswith("__MACOSX/") and name.lower().endswith(_IMAGE_EXTENSIONS)
        )
        if not names:
            raise ValueError("no images found in cbz archive")
        with archive.open(names[0]) as f:
            return Image.open(f).convert("RGB")


_EXTRACTORS = {
    ".pdf": _extract_from_pdf,
    ".cbz": _extract_from_cbz,
}


def get_or_generate_cover(issue: Issue) -> Path | None:
    """Returns the path to a cached/generated cover JPEG for this issue, or None if the
    format isn't supported or extraction failed for any reason — never raises."""
    cover_path = _cover_path_for(issue)
    if cover_path.is_file():
        return cover_path

    source = Path(issue.file_path)
    extractor = _EXTRACTORS.get(source.suffix.lower())
    if extractor is None:
        return None

    try:
        image = extractor(source)
        image.thumbnail(_THUMBNAIL_SIZE)
        image.save(cover_path, "JPEG", quality=_JPEG_QUALITY)
    except Exception:
        logger.exception("Failed to generate cover for issue %s (%s)", issue.id, source)
        return None
    return cover_path
