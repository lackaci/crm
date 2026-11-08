"""Atomic, formula-safe Excel register generated from CRM extraction records."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from app.db import documents as store
from app.db.database import open_database
from app.document_reader import stored_path, read_bytes
from app.providers.document_model import DocumentError, FIELD_NAMES, ITEM_FIELDS
from app.documents import normalize_labeled_facts


def export_workbook(database_path, destination, storage_root):
    requested = Path(destination)
    target = requested.resolve()
    root = Path(storage_root).resolve()
    if target.suffix.lower() != ".xlsx" or target.is_relative_to(root) or target == Path(database_path).resolve():
        raise DocumentError("Choose an .xlsx export outside the customer file storage directory.")
    sources = store.sources(database_path)
    if target.exists():
        try:
            if target.stat().st_size > 40_000_000:
                raise DocumentError("The existing workbook is too large to verify. Choose a new export path.")
            previous = load_workbook(target, read_only=True, keep_links=False)
            try:
                if previous.properties.identifier != "mini-crm-document-register-v1":
                    raise DocumentError("This is not a CRM-generated workbook. Choose a new filename to preserve the existing Excel file.")
            finally:
                previous.close()
        except DocumentError:
            raise
        except Exception:
            raise DocumentError("The existing workbook could not be verified. Close it or choose a new export path.") from None
    counts = Counter(row["source_hash"] for row in sources if row["source_hash"])
    workbook = Workbook()
    workbook.properties.identifier = "mini-crm-document-register-v1"
    workbook.remove(workbook.active)
    headers = {
        "Documents": ["File ID", "Customer ID", "Customer", "Customer email", "Filename", "Stored type", "Detected type", "Review status", "Warnings", "Source path", "SHA-256", "Duplicate content", "Model", "Model digest", "Extracted UTC", "Reviewed UTC", "Summary", *FIELD_NAMES],
        "Fields": ["File ID", "Customer", "Filename", "Section", "Field", "Value", "Evidence", "Review status"],
        "Line items": ["File ID", "Customer", "Filename", "Section", *ITEM_FIELDS, "Review status"],
        "Facts": ["File ID", "Customer", "Filename", "Section", "Label", "Value", "Evidence", "Review status"],
        "Sections": ["File ID", "Customer", "Filename", "Section", "Title", "Summary", "Review status"],
        "Readme": ["Topic", "Details"],
    }
    sheets = {name: workbook.create_sheet(name) for name in headers}

    def append(sheet, values):
        sheet.append(values)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                # Force all untrusted values to text, including =,+,-,@ and URLs.
                cell.value = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", cell.value)
                if len(cell.value) > 32767:
                    raise DocumentError("An extracted value exceeds Excel's cell limit.")
                cell.data_type = "s"

    def numeric_item(name, value):
        if (name in {"quantity", "unit_price", "tax_rate", "total"} and isinstance(value, str)
                and re.fullmatch(r"-?\d{1,12}(?:\.\d{1,6})?", value)
                and len(value.lstrip("-").replace(".", "").lstrip("0")) <= 15):
            return Decimal(value)
        return value

    for name, sheet in sheets.items():
        append(sheet, headers[name])
    for source in sources:
        data, status, warnings, actual_hash, path_text = None, source["extraction_status"] or "not_processed", [], "", ""
        try:
            path = stored_path(source, root)
            path_text = str(path)
            actual_hash = sha256(read_bytes(path)).hexdigest()
            if source["data_json"] and actual_hash == source["source_hash"]:
                data = normalize_labeled_facts(json.loads(source["data_json"]))
                warnings = data.get("warnings", [])
            elif source["data_json"]:
                status, warnings = "source_changed", ["Stored extraction is out of date. Re-extract this file; old values are excluded."]
        except DocumentError as error:
            status, warnings = "source_unavailable", [str(error)]
        if source["error_code"]:
            warnings = [*warnings, source["error_code"]]
        if data and status != "reviewed":
            warnings = ["Model output: verify against the source before use.", *warnings]
        field_values = {}
        if data:
            for entry in data["fields"]:
                field_values.setdefault(entry["name"], [])
                if entry["value"] not in field_values[entry["name"]]:
                    field_values[entry["name"]].append(entry["value"])
        summary = " | ".join(entry["summary"] for entry in data["summaries"]) if data else ""
        if len(summary) > 4000:
            summary = summary[:3900] + " [Full summaries are in Sections.]"
        values = [source["id"], source["customer_id"], source["customer_name"], source["customer_email"], source["filename"], source["file_type"],
            data["document_type"] if data else "", status, "\n".join(warnings)[:30000], path_text, actual_hash,
            "yes" if counts.get(actual_hash, 0) > 1 else "no", source["model"] or "", source["model_digest"] or "", source["updated_at"] or "", source["reviewed_at"] or "", summary]
        for name in FIELD_NAMES:
            entries = field_values.get(name, [])
            value = " | ".join(entries)
            if len(value) > 4000:
                value = value[:3900] + " [Full values are in Fields.]"
            # Numeric summaries support Excel calculations without interpreting text formulas.
            if name in {"subtotal", "tax", "total"} and len(entries) == 1 and re.fullmatch(r"-?\d{1,12}(?:\.\d{1,2})?", value):
                value = Decimal(value)
            values.append(value)
        append(sheets["Documents"], values)
        if not data:
            continue
        prefix = [source["id"], source["customer_name"], source["filename"]]
        for name, keys in (("fields", ("name", "value", "evidence")), ("line_items", ITEM_FIELDS), ("facts", ("label", "value", "evidence"))):
            sheet = sheets[{"fields": "Fields", "line_items": "Line items", "facts": "Facts"}[name]]
            for row in data[name]:
                append(sheet, [*prefix, row["page"], *(numeric_item(key, row[key]) if name == "line_items" else row[key] for key in keys), status])
        for row in data["summaries"]:
            append(sheets["Sections"], [*prefix, row["page"], row["title"], row["summary"], status])
    notes = [
        ("Generated UTC", datetime.now(timezone.utc).isoformat()),
        ("Scope", "All registered customer files in the desktop SQLite workspace. Arbitrary folders are not scanned."),
        ("Review", "AI extraction can misread or omit information. Pending output must be checked against the source. Reviewed means a person marked it reviewed."),
        ("Source sections", "PDF/image section numbers are pages/frames. DOCX, XLSX and text sections are bounded text chunks, not printed page numbers."),
        ("Evidence", "Evidence is a model-supplied quotation, not independent proof. Image evidence must be checked visually."),
        ("Updating", "This is a generated workbook: refresh replaces its sheets. Save corrections in CRM; Excel-only changes are not imported back."),
        ("Duplicates", "Identical content is reused for extraction but each stored file remains a separate register entry. Repeated page headers are retained in Fields."),
        ("Limits", "20 MB per file, 20 PDF pages/image frames or 200,000 text characters; unsupported/failed files remain visible."),
    ]
    for row in notes:
        append(sheets["Readme"], row)
    for sheet in sheets.values():
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="245BCE")
        for index, header in enumerate(headers[sheet.title], 1):
            sheet.column_dimensions[get_column_letter(index)].width = 45 if header in {"Summary", "Evidence", "Warnings", "Details", "Source path"} else 22
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".xlsx", delete=False) as handle:
            temporary = Path(handle.name)
        workbook.save(temporary)
        os.replace(temporary, target)
    except OSError:
        raise DocumentError("Could not update the Excel workbook. Close it in Excel and check the export folder permissions.") from None
    finally:
        workbook.close()
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('owner','document_workbook_exported',?)", (f"file_count={len(sources)}",))
    return target
