"""Bounded local readers; render scanned PDFs instead of requiring cloud OCR."""
from dataclasses import dataclass
from contextlib import closing
from io import BytesIO
from pathlib import Path
from threading import Lock
import warnings
import zipfile
from xml.etree import ElementTree

from app.providers.document_model import DocumentError

MAX_FILE_BYTES = 20_000_000
MAX_SECTIONS = 20
MAX_TEXT = 200_000
SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".txt", ".md", ".csv", ".json", ".docx", ".xlsx"}
# PDFium is process-global and must not run concurrently across Qt workers.
PDF_LOCK = Lock()


@dataclass(frozen=True)
class DocumentPage:
    number: int
    text: str = ""
    image: bytes | None = None


def stored_path(record, storage_root):
    root = Path(storage_root).resolve()
    relative = Path(record["path"])
    target = (root / relative).resolve()
    if relative.is_absolute() or not target.is_relative_to(root) or not relative.parts or relative.parts[0] != f"customer_{record['customer_id']}":
        raise DocumentError("The stored file path is outside its customer storage folder.")
    return target


def read_bytes(path):
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if not raw or len(raw) > MAX_FILE_BYTES:
            raise DocumentError("The file is empty or exceeds the 20 MB extraction limit.")
        return raw
    except OSError:
        raise DocumentError("The stored file could not be read. Check that it still exists.") from None


def _image_bytes(image):
    from PIL import ImageOps
    if image.width * image.height > 30_000_000:
        raise DocumentError("The image exceeds the 30 megapixel extraction limit.")
    with ImageOps.exif_transpose(image).convert("RGB") as rgb:
        rgb.thumbnail((1000, 1000))
        output = BytesIO()
        rgb.save(output, format="JPEG", quality=90)
        return output.getvalue()


def _zip(raw):
    archive = zipfile.ZipFile(BytesIO(raw))
    entries = archive.infolist()
    if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 40_000_000:
        archive.close()
        raise DocumentError("The Office document exceeds the unpacked size limit.")
    return archive


def _text_pages(text):
    if not text.strip():
        raise DocumentError("No readable text was found in this document.")
    if len(text) > MAX_TEXT:
        raise DocumentError("The document exceeds 200,000 text characters. Split it into smaller files.")
    return [DocumentPage(index // 10_000 + 1, text[index:index + 10_000]) for index in range(0, len(text), 10_000)]


def read_document(path, raw=None):
    """Return pages/sections without ever executing macros, formulas or links."""
    suffix = Path(path).suffix.lower()
    if suffix not in SUPPORTED:
        raise DocumentError("Unsupported format. Use PDF, images, DOCX, XLSX or UTF-8 text/CSV/JSON.")
    raw = read_bytes(path) if raw is None else raw
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise DocumentError("The file is empty or exceeds the 20 MB extraction limit.")
    try:
        if suffix == ".pdf":
            import pypdfium2 as pdfium
            pages = []
            with PDF_LOCK:
                with pdfium.PdfDocument(raw) as pdf:
                    if not 1 <= len(pdf) <= MAX_SECTIONS:
                        raise DocumentError("PDF extraction supports 1–20 pages per file. Split longer documents first.")
                    for index in range(len(pdf)):
                        with closing(pdf[index]) as page:
                            with closing(page.get_textpage()) as text_page:
                                if text_page.count_chars() > 10_000:
                                    raise DocumentError("A PDF page contains too much text. Split or simplify the document.")
                                text = text_page.get_text_range()
                            scale = min(1.8, 1000 / max(page.get_size()))
                            with closing(page.render(scale=scale)) as bitmap:
                                with bitmap.to_pil() as image:
                                    picture = _image_bytes(image)
                            pages.append(DocumentPage(index + 1, text, picture))
            return pages
        if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
            from PIL import Image
            pages = []
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(raw)) as image:
                    count = getattr(image, "n_frames", 1)
                    if not 1 <= count <= MAX_SECTIONS:
                        raise DocumentError("Image extraction supports up to 20 frames. Split this file first.")
                    for index in range(count):
                        image.seek(index)
                        pages.append(DocumentPage(index + 1, image=_image_bytes(image)))
            return pages
        if suffix == ".docx":
            with _zip(raw) as archive:
                content = archive.read("word/document.xml")
                if b"<!DOCTYPE" in content or b"<!ENTITY" in content:
                    raise DocumentError("XML entities are not supported in document extraction.")
                root = ElementTree.fromstring(content)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                text = "\n".join("".join(node.itertext()) for node in root.findall(".//w:p", ns))
            return _text_pages(text)
        if suffix == ".xlsx":
            from openpyxl import load_workbook
            with _zip(raw):
                pass
            workbook = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
            try:
                lines, total = [], 0
                for sheet in workbook:
                    if sheet.max_row and sheet.max_row > 20_000 or sheet.max_column and sheet.max_column > 200:
                        raise DocumentError("This sheet exceeds the extraction row/column limit.")
                    for row in sheet.iter_rows():
                        cells = [f"{cell.coordinate}={cell.value}" for cell in row if cell.value is not None]
                        if cells:
                            line = sheet.title + ": " + " | ".join(cells)
                            total += len(line) + 1
                            if total > MAX_TEXT:
                                raise DocumentError("The spreadsheet exceeds 200,000 text characters.")
                            lines.append(line)
                return _text_pages("\n".join(lines))
            finally:
                workbook.close()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        return _text_pages(raw.decode(encoding))
    except DocumentError:
        raise
    except ImportError:
        raise DocumentError("Install the desktop requirements to enable PDF, image and Excel support.") from None
    except Exception:
        raise DocumentError("This document could not be decoded. It may be damaged, encrypted or unsupported.") from None
