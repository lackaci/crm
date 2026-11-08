"""Create an isolated fictional desktop workspace for presentations."""
import json
import gc
from pathlib import Path

from app.db.customers import add_customer
from app.db.database import initialize_database
from app.db.emails import import_emails
from app.db.faqs import create_faq
from app.db.files import store_file
from app.providers.email_mock import MockEmailProvider


def create_demo_workspace(root: Path) -> tuple[Path, Path]:
    """Seed a fresh caller-owned directory; never open the normal CRM database."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    database_path = root / "demo.sqlite"
    if database_path.exists():
        raise FileExistsError("A demo workspace must start with a fresh database")
    storage_root = root / "files"
    initialize_database(database_path)
    for name, email in (("Anna Kovacs", "anna@example.com"),
                        ("Peter Szabo", "peter@example.com"),
                        ("Maria Nagy", "maria@example.com")):
        add_customer(database_path, name, email, notes="Fictional demo customer")
    fixture_root = Path(__file__).resolve().parents[1] / "sample_data"
    for faq in json.loads((fixture_root / "faqs.json").read_text(encoding="utf-8")):
        create_faq(database_path, faq["question"], faq["answer"], approved=True, actor="demo-setup")
    import_emails(database_path, MockEmailProvider(), storage_root=storage_root)
    # Keep sample document originals in the disposable workspace as well.
    invoice = root / "demo-invoice.txt"
    invoice.write_text("Invoice DEMO-INV-001\nDemo Supplies Ltd\nIssue: 2026-11-01\nDue: 2026-11-15\nCurrency: EUR\nSubtotal: 100.00\nTax: 20.00\nTotal: 120.00\nOffice supplies: 2 x 50.00 = 100.00", encoding="utf-8")
    contract = root / "demo-contract.txt"
    contract.write_text("Contract DEMO-CONTRACT-001\nDemo Support Ltd and Anna Kovacs\nEffective: 2026-11-01\nExpiry: 2027-10-31\nNotice period: 30 days", encoding="utf-8")
    store_file(database_path, 1, invoice, "invoice", storage_root)
    store_file(database_path, 1, contract, "contract", storage_root)
    # Legacy SQLite helpers rely on connection finalization. Collect before
    # the temporary workspace is removed on Windows, where open files lock.
    gc.collect()
    return database_path, storage_root
