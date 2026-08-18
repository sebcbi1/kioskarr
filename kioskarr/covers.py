"""Cover thumbnail extraction for OPDS entries. Supported formats: PDF (render page 0), CBZ
(first image in the archive, by name), EPUB (the manifest's declared cover image, EPUB3 or
EPUB2 convention, falling back to the first image by name), and CBR (same as CBZ, via an
external `unar` binary). MOBI, or a corrupt/unreadable file in a supported format, is out of
scope; callers fall back to a generic placeholder image instead, since a missing cover must
never be a hard error.

The generated cover lives right next to the issue file itself (same name, .jpg extension)
rather than in a separate cache directory — no new setting needed, and it doubles as a real
cover for any tool that scans the library folder directly (Komga, Kavita, Calibre all look
for a same-name image next to a book/comic file).

CBR needs a real RAR-reading capability — there is no pure-Python RAR decompressor at all
(RAR's compression algorithm is proprietary; nobody has legally reverse-engineered an open
implementation of the *encoder*, only decoders). This uses `unar` (The Unarchiver's CLI
engine) specifically, not the official `unrar` tool: `unar` is fully LGPL, a clean-room
implementation with no unRAR-derived licensing restrictions, and supports RAR3/4/5. The
official `unrar` binary carries non-free redistribution terms (Debian/Ubuntu exclude it from
their main repos for exactly this reason), and 7-Zip's own RAR codec is mixed-licensed
(LGPL + some unRAR-restricted portions) — `unar` is the only genuinely clean option of the
three. Requires the `unar` package installed on the host (see Dockerfile / README); if it's
missing, CBR extraction just fails like any other extraction error and falls back to the
placeholder — never a hard error.
"""

import logging
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
import rarfile
from PIL import Image

from kioskarr.models import Issue

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = (600, 600)
_JPEG_QUALITY = 80
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_OPF_NS = "http://www.idpf.org/2007/opf"

# Lock in `unar` specifically (see module docstring) — force=True so this always wins over
# whatever rarfile would otherwise auto-detect (e.g. an `unrar` binary happening to also be
# on PATH somewhere).
rarfile.tool_setup(unrar=False, unar=True, bsdtar=False, sevenzip=False, sevenzip2=False, force=True)


def _cover_path_for(issue: Issue) -> Path:
    return Path(issue.file_path).with_suffix(".jpg")


def _first_image_by_name(archive) -> Image.Image:
    names = sorted(
        name
        for name in archive.namelist()
        if not name.startswith("__MACOSX/") and name.lower().endswith(_IMAGE_EXTENSIONS)
    )
    if not names:
        raise ValueError("no images found in archive")
    with archive.open(names[0]) as f:
        return Image.open(f).convert("RGB")


def _extract_from_pdf(source: Path) -> Image.Image:
    pdf = pdfium.PdfDocument(str(source))
    try:
        page = pdf[0]
        return page.render(scale=2).to_pil().convert("RGB")
    finally:
        pdf.close()


def _extract_from_cbz(source: Path) -> Image.Image:
    with zipfile.ZipFile(source) as archive:
        return _first_image_by_name(archive)


def _extract_from_cbr(source: Path) -> Image.Image:
    with rarfile.RarFile(source) as archive:
        return _first_image_by_name(archive)


def _epub_cover_href(opf: ET.Element) -> str | None:
    manifest = opf.find(f"{{{_OPF_NS}}}manifest")
    if manifest is None:
        return None

    # EPUB3 convention: <item properties="cover-image" href="...">
    for item in manifest.findall(f"{{{_OPF_NS}}}item"):
        if "cover-image" in (item.get("properties") or "").split():
            return item.get("href")

    # EPUB2 convention: <meta name="cover" content="{id}"> in <metadata>, resolved
    # against the manifest item with that id.
    metadata = opf.find(f"{{{_OPF_NS}}}metadata")
    if metadata is not None:
        for meta in metadata.findall(f"{{{_OPF_NS}}}meta"):
            if meta.get("name") == "cover":
                cover_id = meta.get("content")
                for item in manifest.findall(f"{{{_OPF_NS}}}item"):
                    if item.get("id") == cover_id:
                        return item.get("href")
    return None


def _extract_from_epub(source: Path) -> Image.Image:
    with zipfile.ZipFile(source) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(f".//{{{_CONTAINER_NS}}}rootfile")
        opf_path = rootfile.get("full-path")
        opf_dir = posixpath.dirname(opf_path)

        opf = ET.fromstring(archive.read(opf_path))
        cover_href = _epub_cover_href(opf)

        if cover_href is not None:
            full_path = posixpath.normpath(posixpath.join(opf_dir, cover_href))
            with archive.open(full_path) as f:
                return Image.open(f).convert("RGB")

        # Some real-world EPUBs don't declare a cover per either convention — fall
        # back to the first image in the archive, same last resort as CBZ.
        return _first_image_by_name(archive)


_EXTRACTORS = {
    ".pdf": _extract_from_pdf,
    ".cbz": _extract_from_cbz,
    ".cbr": _extract_from_cbr,
    ".epub": _extract_from_epub,
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
