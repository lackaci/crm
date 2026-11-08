"""Read stored CRM documents with a local vision model and consolidate facts."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

from app.db import documents as store
from app.db.files import DEFAULT_STORAGE_ROOT
from app.document_reader import read_document, read_bytes, stored_path, SUPPORTED
from app.providers.document_model import DocumentError, LocalDocumentExtractor, validate_page, FIELD_NAMES

PIPELINE = "document-register-page-v1"
NUMERIC_FIELDS = {"subtotal", "tax", "total", "quantity", "unit_price", "tax_rate"}
DATE_FIELDS = {"issue_date", "due_date", "effective_date", "expiry_date", "renewal_date"}


def standard_fact_name(label):
    aliases = {"expiration_date": "expiry_date", "contract_end_date": "expiry_date", "contract_start_date": "effective_date",
        "contract_no": "contract_number", "invoice_no": "invoice_number"}
    label = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    name = aliases.get(label, label)
    return name if name in FIELD_NAMES else None


def normalize_labeled_facts(data):
    """Copy explicitly named facts into standard columns, without inferring values."""
    if data.get("fields_manually_reviewed"):
        data["warnings"] = data_warnings(data)
        return data
    existing = {(row["name"], row["value"], row["page"]) for row in data["fields"]}
    for fact in data["facts"]:
        name = standard_fact_name(fact["label"])
        key = (name, fact["value"], fact["page"])
        if name in FIELD_NAMES and key not in existing:
            data["fields"].append({"name": name, "value": fact["value"], "page": fact["page"], "evidence": fact["evidence"]})
            existing.add(key)
    data["warnings"] = data_warnings(data)
    return data


@dataclass(frozen=True)
class ExtractionBatch:
    extracted: int = 0
    reused: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    workbook: str | None = None


def data_warnings(data):
    warnings = list(data.get("source_warnings", data.get("warnings", [])))
    values = {}
    for row in data["fields"]:
        name, value = row["name"], row["value"]
        values.setdefault(name, set()).add(value)
        if name in NUMERIC_FIELDS and not re.fullmatch(r"-?\d{1,15}(?:\.\d{1,6})?", value):
            warnings.append(f"Check the numeric format of {name} on section {row['page']}.")
        if name in DATE_FIELDS:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError()
                date.fromisoformat(value)
            except ValueError:
                warnings.append(f"Check the date format of {name} on section {row['page']}.")
        if name == "currency" and not re.fullmatch(r"[A-Z]{3}", value):
            warnings.append("Check the currency code.")
    for name, entries in values.items():
        if len(entries) > 1:
            warnings.append(f"Different values found for {name}; check Fields and source pages. This may contain multiple documents.")
    for fact in data["facts"]:
        name = standard_fact_name(fact["label"])
        if name in values and fact["value"] not in values[name]:
            warnings.append(f"Standard field and extracted fact differ for {name}; review both entries.")
    for row in data["line_items"]:
        for name in ("quantity", "unit_price", "tax_rate", "total"):
            value = row[name]
            if value is not None and not re.fullmatch(r"-?\d{1,15}(?:\.\d{1,6})?", value):
                warnings.append(f"Check line-item {name} on section {row['page']}.")
    if data["document_type"] in {"invoice", "receipt"}:
        for name in ("supplier_name", "issue_date", "currency", "total"):
            if not values.get(name):
                warnings.append(f"Missing invoice/receipt field: {name}.")
        if data["document_type"] == "invoice" and not values.get("invoice_number"):
            warnings.append("Missing invoice number.")
    if all(len(values.get(name, set())) == 1 for name in ("subtotal", "tax", "total")):
        try:
            subtotal, tax, total = (Decimal(next(iter(values[name]))) for name in ("subtotal", "tax", "total"))
            if not all(value.is_finite() for value in (subtotal, tax, total)) or abs(subtotal + tax - total) > Decimal("0.02"):
                warnings.append("Subtotal plus tax does not match total; check discounts, fees and rounding.")
        except InvalidOperation:
            warnings.append("The invoice totals could not be checked.")
    if not data["fields"] and not data["facts"] and not data["line_items"]:
        warnings.append("No structured facts were found. Check whether this document is legible.")
    return list(dict.fromkeys(warnings))


def combine_pages(pages):
    data = {"document_type": "other", "fields": [], "facts": [], "line_items": [], "summaries": [], "warnings": [], "sections": len(pages)}
    types = set()
    for number, value in pages:
        validate_page(value)
        if value["document_type"] != "other":
            types.add(value["document_type"])
        for name in ("fields", "facts", "line_items"):
            data[name].extend(dict(row, page=number) for row in value[name])
        data["summaries"].append({"page": number, "title": value["title"], "summary": value["summary"]})
        data["warnings"].extend(f"Section {number}: {warning}" for warning in value["warnings"])
    if len(types) == 1:
        data["document_type"] = next(iter(types))
    elif len(types) > 1:
        data["warnings"].append("This file contains more than one document type. Check each section.")
    data["source_warnings"] = list(data["warnings"])
    return normalize_labeled_facts(data)


def validate_saved_data(data):
    if not isinstance(data, dict) or not isinstance(data.get("sections"), int) or not 1 <= data["sections"] <= 20:
        raise DocumentError("Invalid document extraction data.")
    # Reuse the provider boundary on each section after manual edits.
    if any(not isinstance(data.get(name), list) or len(data[name]) > 2000 for name in ("fields", "facts", "line_items", "summaries", "warnings")):
        raise DocumentError("Invalid document extraction entries.")
    for name in ("fields", "facts", "line_items"):
        for row in data[name]:
            if not isinstance(row, dict) or not isinstance(row.get("page"), int) or not 1 <= row["page"] <= data["sections"]:
                raise DocumentError("Invalid source section reference.")
    for number in range(1, data["sections"] + 1):
        summary = next((row for row in data["summaries"] if row.get("page") == number), {})
        page = {"document_type": data["document_type"], "title": summary.get("title", ""), "summary": summary.get("summary", ""), "warnings": [],
            **{name: [{key: value for key, value in row.items() if key != "page"} for row in data[name] if row["page"] == number] for name in ("fields", "facts", "line_items")}}
        validate_page(page)


def process_documents(database_path, storage_root=None, provider=None, *, progress=None, cancelled=None, workbook_path=None):
    root = Path(storage_root) if storage_root is not None else DEFAULT_STORAGE_ROOT
    config = store.settings(database_path)
    if provider is None:
        from app.providers.document_factory import document_provider
        provider = document_provider(config)
    progress = progress or (lambda message: None)
    cancelled = cancelled or (lambda: False)
    extracted = reused = skipped = failed = 0
    digest = None
    for source in store.sources(database_path):
        if cancelled():
            break
        progress(f"Reading file #{source['id']}: {source['filename']}")
        content_hash = ""
        unsupported = Path(source["filename"]).suffix.lower() not in SUPPORTED
        try:
            path = stored_path(source, root)
            raw = read_bytes(path)
            content_hash = sha256(raw).hexdigest()
            if unsupported:
                raise DocumentError("Unsupported file format; this file remains listed in the workbook.")
            # Resolve once per batch, then use content/profile identity for cache.
            if digest is None:
                progress("Checking the selected document model…")
                digest = provider.resolve()
            if (source["source_hash"] == content_hash and source["model_digest"] == digest and source["pipeline"] == PIPELINE
                    and source["extraction_status"] in {"pending_review", "reviewed"}):
                skipped += 1
                continue
            data = store.cached(database_path, content_hash, digest, PIPELINE)
            was_cached = data is not None
            if data is None:
                pages = read_document(path, raw)
                results = []
                for page in pages:
                    if cancelled():
                        break
                    progress(f"File #{source['id']}: section {page.number}/{len(pages)} — model working…")
                    provider.cancelled = cancelled
                    provider.progress = lambda count: progress(f"File #{source['id']}, section {page.number}: model generated {count} characters…")
                    results.append((page.number, provider.extract_page(page)))
                if cancelled():
                    break
                provider.verify_profile()
                data = combine_pages(results)
            if sha256(read_bytes(path)).hexdigest() != content_hash:
                raise DocumentError("The source file changed during extraction. Run it again.")
            validate_saved_data(data)
            if store.save_extraction(database_path, source, content_hash, provider.model, digest, PIPELINE, data):
                reused += was_cached
                extracted += not was_cached
        except DocumentError as error:
            if cancelled():
                break
            if getattr(error, "fatal", False):
                raise
            # A model connection/setup failure should stop a batch rather than
            # retry a missing model once for every customer file.
            if digest is None and not unsupported and content_hash:
                raise
            store.save_extraction(database_path, source, content_hash, provider.model, digest or "unresolved", PIPELINE,
                error=str(error), unsupported=unsupported)
            failed += 1
        except Exception:
            store.save_extraction(database_path, source, content_hash, provider.model, digest or "unresolved", PIPELINE,
                error="Extraction failed. Check this file and the local model, then retry.")
            failed += 1
    workbook = None
    if workbook_path is not None and not cancelled():
        progress("Updating the consolidated Excel workbook…")
        from app.document_workbook import export_workbook
        workbook = str(export_workbook(database_path, workbook_path, root))
    return ExtractionBatch(extracted, reused, skipped, failed, cancelled(), workbook)


def review_extraction(database_path, source, data, storage_root=None, *, approve=False):
    root = Path(storage_root) if storage_root is not None else DEFAULT_STORAGE_ROOT
    if sha256(read_bytes(stored_path(source, root))).hexdigest() != source["source_hash"]:
        raise DocumentError("The source file changed. Extract it again before reviewing.")
    validate_saved_data(data)
    data["fields_manually_reviewed"] = True
    data["warnings"] = data_warnings(data)
    store.review(database_path, source["id"], source["source_hash"], data, source["data_json"], source["model_digest"], approve)
