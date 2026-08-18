import io
import zipfile
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

from kioskarr.covers import get_or_generate_cover
from kioskarr.models import Issue


def _write_real_pdf(path):
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 280)
    pdf.save(str(path))


def _write_real_cbz(path, image_names=("002.jpg", "001.jpg", "readme.txt")):
    with zipfile.ZipFile(path, "w") as archive:
        for name in image_names:
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                buf = io.BytesIO()
                Image.new("RGB", (50, 70), color=(10, 20, 30)).save(buf, "JPEG")
                archive.writestr(name, buf.getvalue())
            else:
                archive.writestr(name, b"not an image")


def _write_real_epub(path, cover_convention="epub3"):
    if cover_convention == "epub3":
        manifest_extra = (
            '<item id="cover-img" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>'
        )
        metadata_extra = ""
    elif cover_convention == "epub2":
        manifest_extra = '<item id="cover-img" href="cover.jpg" media-type="image/jpeg"/>'
        metadata_extra = '<meta name="cover" content="cover-img"/>'
    else:
        manifest_extra = ""
        metadata_extra = ""

    container_xml = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Test</dc:title>" + metadata_extra + "</metadata>"
        "<manifest>" + manifest_extra + ""
        '<item id="page1" href="page1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="filler" href="filler.jpg" media-type="image/jpeg"/>'
        "</manifest>"
        '<spine><itemref idref="page1"/></spine></package>'
    )

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container_xml)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/page1.xhtml", "<html><body>hi</body></html>")
        if cover_convention in ("epub3", "epub2"):
            buf = io.BytesIO()
            Image.new("RGB", (60, 90), color=(50, 60, 70)).save(buf, "JPEG")
            archive.writestr("OEBPS/cover.jpg", buf.getvalue())
        # A second, differently-sized image lets tests tell "found the declared cover"
        # apart from "fell back to the first image by name".
        buf2 = io.BytesIO()
        Image.new("RGB", (10, 10), color=(1, 1, 1)).save(buf2, "JPEG")
        archive.writestr("OEBPS/filler.jpg", buf2.getvalue())


def _issue(file_path, issue_id=1):
    return Issue(id=issue_id, publication_id=1, identifier="2026-08-13", file_path=str(file_path))


def test_generates_cover_from_pdf(tmp_path):
    pdf_path = tmp_path / "issue.pdf"
    _write_real_pdf(pdf_path)

    cover_path = get_or_generate_cover(_issue(pdf_path))

    assert cover_path is not None
    assert cover_path == pdf_path.with_suffix(".jpg")
    image = Image.open(cover_path)
    assert image.format == "JPEG"


def test_generates_cover_from_cbz_using_first_image_by_name(tmp_path):
    cbz_path = tmp_path / "issue.cbz"
    _write_real_cbz(cbz_path)

    cover_path = get_or_generate_cover(_issue(cbz_path))

    assert cover_path is not None
    image = Image.open(cover_path)
    assert image.format == "JPEG"


def test_cbz_with_no_images_returns_none(tmp_path):
    cbz_path = tmp_path / "issue.cbz"
    with zipfile.ZipFile(cbz_path, "w") as archive:
        archive.writestr("readme.txt", b"no images here")

    assert get_or_generate_cover(_issue(cbz_path)) is None


def test_thumbnail_is_capped_to_600px(tmp_path):
    cbz_path = tmp_path / "issue.cbz"
    with zipfile.ZipFile(cbz_path, "w") as archive:
        buf = io.BytesIO()
        Image.new("RGB", (2000, 3000), color=(0, 0, 0)).save(buf, "JPEG")
        archive.writestr("001.jpg", buf.getvalue())

    cover_path = get_or_generate_cover(_issue(cbz_path))

    image = Image.open(cover_path)
    assert image.width <= 600
    assert image.height <= 600


def test_unsupported_extension_returns_none(tmp_path):
    mobi_path = tmp_path / "issue.mobi"
    mobi_path.write_bytes(b"not really a mobi")

    assert get_or_generate_cover(_issue(mobi_path)) is None


def test_generates_cover_from_epub_using_epub3_convention(tmp_path):
    epub_path = tmp_path / "issue.epub"
    _write_real_epub(epub_path, cover_convention="epub3")

    cover_path = get_or_generate_cover(_issue(epub_path))

    assert cover_path is not None
    image = Image.open(cover_path)
    assert image.format == "JPEG"
    assert image.size == (60, 90)  # the declared cover, not the 10x10 filler image


def test_generates_cover_from_epub_using_epub2_convention(tmp_path):
    epub_path = tmp_path / "issue.epub"
    _write_real_epub(epub_path, cover_convention="epub2")

    cover_path = get_or_generate_cover(_issue(epub_path))

    assert cover_path is not None
    image = Image.open(cover_path)
    assert image.format == "JPEG"
    assert image.size == (60, 90)


def test_epub_with_no_declared_cover_falls_back_to_first_image_by_name(tmp_path):
    epub_path = tmp_path / "issue.epub"
    _write_real_epub(epub_path, cover_convention="none")

    cover_path = get_or_generate_cover(_issue(epub_path))

    assert cover_path is not None
    image = Image.open(cover_path)
    assert image.format == "JPEG"
    assert image.size == (10, 10)  # the only image present, since no convention matched


def test_corrupt_epub_returns_none_not_raises(tmp_path):
    bad_epub = tmp_path / "issue.epub"
    bad_epub.write_bytes(b"this is not a real zip file")

    assert get_or_generate_cover(_issue(bad_epub)) is None


_CBR_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "issue.cbr"


@pytest.mark.skipif(not _CBR_FIXTURE.is_file(), reason="CBR fixture not present")
def test_generates_cover_from_cbr_using_first_image_by_name(tmp_path):
    cbr_path = tmp_path / "issue.cbr"
    cbr_path.write_bytes(_CBR_FIXTURE.read_bytes())

    cover_path = get_or_generate_cover(_issue(cbr_path))

    assert cover_path is not None
    image = Image.open(cover_path)
    assert image.format == "JPEG"


def test_corrupt_cbr_returns_none_not_raises(tmp_path):
    bad_cbr = tmp_path / "issue.cbr"
    bad_cbr.write_bytes(b"this is not a real rar file")

    assert get_or_generate_cover(_issue(bad_cbr)) is None


def test_corrupt_pdf_returns_none_not_raises(tmp_path):
    bad_pdf = tmp_path / "issue.pdf"
    bad_pdf.write_bytes(b"this is not a real pdf file")

    assert get_or_generate_cover(_issue(bad_pdf)) is None


def test_corrupt_cbz_returns_none_not_raises(tmp_path):
    bad_cbz = tmp_path / "issue.cbz"
    bad_cbz.write_bytes(b"this is not a real zip file")

    assert get_or_generate_cover(_issue(bad_cbz)) is None


def test_missing_source_file_returns_none_not_raises(tmp_path):
    missing = tmp_path / "does-not-exist.pdf"

    assert get_or_generate_cover(_issue(missing)) is None


def test_second_call_is_a_cache_hit_not_regenerated(tmp_path):
    pdf_path = tmp_path / "issue.pdf"
    _write_real_pdf(pdf_path)

    first = get_or_generate_cover(_issue(pdf_path))
    first_mtime = first.stat().st_mtime_ns
    # Overwrite the source with different content — if this were regenerated, the
    # cover would change; since it's a cache hit, the source is never even reopened.
    _write_real_pdf(pdf_path)

    second = get_or_generate_cover(_issue(pdf_path))

    assert second == first
    assert second.stat().st_mtime_ns == first_mtime


@pytest.mark.parametrize("name", ["Cover.JPG", "COVER.PNG"])
def test_cbz_image_extension_matching_is_case_insensitive(tmp_path, name):
    cbz_path = tmp_path / "issue.cbz"
    with zipfile.ZipFile(cbz_path, "w") as archive:
        buf = io.BytesIO()
        Image.new("RGB", (50, 70)).save(buf, "PNG" if name.lower().endswith("png") else "JPEG")
        archive.writestr(name, buf.getvalue())

    assert get_or_generate_cover(_issue(cbz_path)) is not None


def test_cbz_skips_macosx_junk_entries(tmp_path):
    cbz_path = tmp_path / "issue.cbz"
    with zipfile.ZipFile(cbz_path, "w") as archive:
        buf = io.BytesIO()
        Image.new("RGB", (1, 1)).save(buf, "JPEG")
        archive.writestr("__MACOSX/._001.jpg", buf.getvalue())
        buf2 = io.BytesIO()
        Image.new("RGB", (50, 70), color=(1, 2, 3)).save(buf2, "JPEG")
        archive.writestr("001.jpg", buf2.getvalue())

    cover_path = get_or_generate_cover(_issue(cbz_path))

    assert cover_path is not None
    image = Image.open(cover_path)
    assert image.size == (50, 70)  # the real image, not the 1x1 __MACOSX junk entry
