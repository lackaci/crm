"""Explicit real-model check using generated fictional documents only."""
import argparse
from decimal import Decimal
import gc
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont
from app.db.database import initialize_database
from app.db.customers import add_customer
from app.db.files import store_file
from app.documents import process_documents
from app.providers.document_model import LocalDocumentExtractor, DEFAULT_DOCUMENT_MODEL
from app.providers.document_factory import OnlineDocumentExtractor
from app.providers.online_model import OnlineSettings, DEFAULT_ONLINE_MODEL, OPENAI_ENDPOINT
from app.providers.online_model import VERCEL_ENDPOINT, VERCEL_MODEL, VERCEL_EMBEDDING_MODEL, VERCEL_KEY_ENV
from openpyxl import load_workbook


def verify_workbook(path):
    """Verify the model's saved facts regardless of which suitable sheet holds them."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["Documents"]
        rows = list(sheet.iter_rows(values_only=True))
        sources = {row["Filename"]: row for row in (dict(zip(rows[0], values)) for values in rows[1:])}
        invoice = sources["synthetic-invoice.pdf"]
        if invoice["invoice_number"] != "DEMO-SMOKE-001" or Decimal(str(invoice["total"])) != Decimal("120.00"):
            raise RuntimeError("The local model did not correctly read the synthetic invoice number/total.")
        contract = sources["synthetic-contract.txt"]
        facts = list(workbook["Facts"].iter_rows(values_only=True))
        expiry_facts = [dict(zip(facts[0], row)) for row in facts[1:]]
        expiry_found = contract.get("expiry_date") == "2027-10-31" or any(
            row["Filename"] == "synthetic-contract.txt" and str(row["Label"]).casefold() == "expiry date" and row["Value"] == "2027-10-31"
            for row in expiry_facts)
        if contract["Detected type"] != "contract" or not expiry_found:
            raise RuntimeError("The local model did not correctly read the synthetic contract expiry.")
        if any(row["Review status"] != "pending_review" for row in sources.values()):
            raise RuntimeError("Synthetic model results must still require human review.")
        print("Saved real-model workbook verified: scanned invoice number/total and contract expiry are correct.")
    finally:
        workbook.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("vercel", "openai", "compatible", "local"), default="openai")
    parser.add_argument("--model")
    parser.add_argument("--endpoint")
    parser.add_argument("--api-key-env")
    parser.add_argument("--verify-existing", action="store_true", help="Verify the previously generated fictional workbook without calling the model")
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "docs" / "examples" / "document-register-synthetic.xlsx"
    if args.verify_existing:
        verify_workbook(output)
        return
    # Use the same private .env convention as the desktop, without printing it.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    model = args.model or (DEFAULT_DOCUMENT_MODEL if args.provider == "local" else VERCEL_MODEL if args.provider == "vercel" else DEFAULT_ONLINE_MODEL)
    provider = LocalDocumentExtractor(model) if args.provider == "local" else OnlineDocumentExtractor(
        OnlineSettings(model=model, provider=args.provider, endpoint=args.endpoint or (VERCEL_ENDPOINT if args.provider == "vercel" else OPENAI_ENDPOINT),
            api_key_env=args.api_key_env or (VERCEL_KEY_ENV if args.provider == "vercel" else "COMPATIBLE_AI_API_KEY" if args.provider == "compatible" else "OPENAI_API_KEY"),
            embedding_model=VERCEL_EMBEDDING_MODEL if args.provider == "vercel" else "text-embedding-3-small"))
    with TemporaryDirectory(prefix="mini-crm-document-smoke-") as folder:
        root = Path(folder)
        database, storage = root / "synthetic.sqlite", root / "files"
        initialize_database(database)
        customer = add_customer(database, "Fictional Test Customer", "fictional@example.invalid")
        font_path = Path("C:/Windows/Fonts/arial.ttf")
        font = ImageFont.truetype(str(font_path), 30) if font_path.is_file() else ImageFont.load_default(size=30)
        invoice = root / "synthetic-invoice.pdf"
        with Image.new("RGB", (1000, 1200), "white") as image:
            draw = ImageDraw.Draw(image)
            lines = ["INVOICE", "Supplier: Fictional Supplies Ltd", "Invoice number: DEMO-SMOKE-001", "Issue date: 2026-11-01",
                "Due date: 2026-11-15", "Currency: EUR", "Item: Office supplies", "Quantity: 2", "Unit price: 50.00",
                "Subtotal: 100.00", "Tax (20%): 20.00", "TOTAL: 120.00", "FICTIONAL TEST DOCUMENT"]
            for index, line in enumerate(lines):
                draw.text((55, 55 + index * 75), line, fill="black", font=font)
            image.save(invoice, "PDF", resolution=100)
        contract = root / "synthetic-contract.txt"
        contract.write_text("SUPPORT CONTRACT\nContract number: DEMO-CONTRACT-001\nParties: Fictional Supplies Ltd and Fictional Test Customer\nEffective date: 2026-11-01\nExpiry date: 2027-10-31\nRenewal: yearly by written agreement\nNotice period: 30 days\nObligation: provide email support on weekdays.\nFICTIONAL TEST DOCUMENT", encoding="utf-8")
        store_file(database, customer, invoice, "invoice", storage)
        store_file(database, customer, contract, "contract", storage)
        result = process_documents(database, storage, provider, progress=lambda message: print(message, flush=True), workbook_path=output)
        if result.failed or result.extracted != 2:
            raise RuntimeError("Synthetic document extraction did not complete both files.")
        verify_workbook(output)
        print("Synthetic model check passed: scanned invoice number/total and contract expiry verified.")
        print("Fictional example workbook:", output)
        gc.collect()


if __name__ == "__main__":
    main()
