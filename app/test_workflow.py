"""Session-approved workflows confined to a marked, separate test workspace."""
from contextlib import closing
from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from pathlib import Path
from threading import Event, Lock

from app.config import AppConfig, RunMode
from app.db.database import open_database
from app.db.connections import load_connection, mail_paused
from app.db import documents
from app.db.emails import get_email, assign_email_to_customer
from app.providers.llm_fake import FakeLLMProvider
from app.reply.pipeline import process_email
from app.reply.drafter import _is_safe_rewrite, _unsafe_additions
from app.reply import delivery


def require_test_workspace(path):
    with closing(open_database(path)) as connection:
        table = connection.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_test_workspace'").fetchone()
        if not table or not connection.execute("SELECT 1 FROM desktop_test_workspace WHERE id=1").fetchone():
            raise ValueError("Workflow approval is available only in the separate test workspace.")


def create_test_workspace(root):
    from app.demo import create_demo_workspace
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    database, storage = root / "demo.sqlite", root / "files"
    if database.exists():
        require_test_workspace(database)
        from app.db.database import initialize_database
        initialize_database(database)
        return database, storage
    database, storage = create_demo_workspace(root)
    with closing(open_database(database)) as connection, connection:
        connection.execute("CREATE TABLE desktop_test_workspace(id INTEGER PRIMARY KEY CHECK(id=1))")
        connection.execute("INSERT INTO desktop_test_workspace VALUES(1)")
        connection.execute("""CREATE TABLE desktop_test_results(
            email_id INTEGER NOT NULL REFERENCES emails(id), mode TEXT NOT NULL, offline INTEGER NOT NULL,
            outcome TEXT NOT NULL, reason TEXT NOT NULL, draft_id INTEGER, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(email_id,mode,offline))""")
    from app.providers.online_model import OnlineSettings
    online = OnlineSettings.vercel()
    documents.save_settings(database, "qwen3-vl:2b-instruct", root / "exports" / "crm_documents.xlsx",
                            provider=online.provider, online_model=online.model, online_endpoint=online.endpoint,
                            api_key_env=online.api_key_env, embedding_model=online.embedding_model)
    return database, storage


def _fingerprint(path, config):
    payload = {"config": asdict(config), "mail": load_connection(path), "ai": documents.settings(path)}
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class WorkflowApproval:
    database_path: Path
    config: AppConfig
    mode: str
    offline: bool
    batch_size: int
    remaining: int
    fingerprint: str
    revoked: Event = field(default_factory=Event, repr=False)
    run_lock: Lock = field(default_factory=Lock, repr=False)

    def check(self):
        require_test_workspace(self.database_path)
        if self.revoked.is_set():
            raise ValueError("Workflow approval was stopped or settings changed. Approve the workflow again.")
        if mail_paused(self.database_path):
            raise ValueError("Mail activity is paused. Resume it and approve the workflow again.")
        if self.fingerprint != _fingerprint(self.database_path, self.config):
            self.revoked.set()
            raise ValueError("Settings changed. Approve the new workflow before running it.")


def approve_workflow(path, config, *, mode="send", offline=True, batch_size=5, session_limit=20):
    require_test_workspace(path)
    if mode not in {"prepare", "draft", "send"} or not 1 <= batch_size <= 50 or not batch_size <= session_limit <= 200:
        raise ValueError("Choose a valid workflow mode and message limits.")
    if not offline and mode != "prepare":
        if config.provider != "gmail" or not config.test_mailbox or not config.gmail_write_enabled:
            raise ValueError("Connect Gmail with draft permission in Test setup first.")
        if mode == "send" and not config.sending_enabled:
            raise ValueError("Enable sending and enter allowed test recipients in the Gmail section first.")
    approval = WorkflowApproval(Path(path).resolve(), config, mode, offline, batch_size, session_limit, _fingerprint(path, config))
    approval.check()
    with closing(open_database(path)) as connection, connection:
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('test-owner','test_workflow_approved',?)",
                           (f"mode={mode}; offline={offline}; batch={batch_size}; limit={session_limit}",))
    return approval


def _save_result(path, email_id, approval, outcome, reason="", draft_id=None):
    with closing(open_database(path)) as connection, connection:
        connection.execute("""INSERT INTO desktop_test_results(email_id,mode,offline,outcome,reason,draft_id)
            VALUES(?,?,?,?,?,?) ON CONFLICT(email_id,mode,offline) DO UPDATE SET outcome=excluded.outcome,
            reason=excluded.reason,draft_id=excluded.draft_id,updated_at=CURRENT_TIMESTAMP""",
            (email_id, approval.mode, approval.offline, outcome, reason, draft_id))
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('test-workflow','test_workflow_result',?)",
                           (f"email_id={email_id}; mode={approval.mode}; outcome={outcome}",))
        connection.execute("UPDATE emails SET needs_attention=?,attention_reason=? WHERE id=? AND status!='done'",
                           (outcome in {"needs_attention", "uncertain"}, reason if outcome in {"needs_attention", "uncertain"} else "", email_id))


def _check_answer(path, draft_id, faq):
    with closing(open_database(path)) as connection:
        row = connection.execute("SELECT d.body,d.status,f.status AS faq_status,f.question,f.answer FROM draft_replies d "
                                 "JOIN faq_entries f ON f.id=d.faq_id WHERE d.id=? AND d.faq_id=?", (draft_id, faq.id)).fetchone()
    if (not row or row["status"] != "pending" or row["faq_status"] != "approved"
            or row["question"] != faq.question or row["answer"] != faq.answer
            or not _is_safe_rewrite(row["body"], faq.answer) or _unsafe_additions(row["body"], faq.answer)):
        raise ValueError("The saved answer or reply changed. This message needs attention.")


class ApprovedProvider:
    """Recheck session approval immediately before each remote operation."""
    def __init__(self, provider, check):
        self.provider, self.check = provider, check

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def verify_connection(self, **options):
        self.check()
        return self.provider.verify_connection(**options)

    def create_reviewed_draft(self, reply):
        self.check()
        return self.provider.create_reviewed_draft(reply)

    def send_reviewed_draft(self, reply, draft_id):
        self.check()
        return self.provider.send_reviewed_draft(reply, draft_id)


class ApprovedModel:
    """Honor Stop before every embedding/completion, preserving masking."""
    def __init__(self, provider, check):
        self.provider, self.check = provider, check

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def embed(self, text):
        self.check()
        return self.provider.embed(text)

    def complete_masked(self, system, prompt, json_mode=False):
        self.check()
        method = getattr(self.provider, "complete_masked", None) or self.provider.complete
        return method(system, prompt, json_mode)

    def complete(self, system, prompt, json_mode=False):
        from app.providers.llm_masked import MaskedLLMProvider
        return MaskedLLMProvider(self).complete(system, prompt, json_mode)


def run_workflow(approval, provider, llm=None, progress=None):
    """Process a bounded batch. No message-by-message confirmation dialogs."""
    if not approval.run_lock.acquire(blocking=False):
        raise ValueError("This approved workflow is already running.")
    notify = progress or (lambda text: None)
    counts = {"prepared": 0, "drafted": 0, "sent": 0, "simulated_sent": 0, "attention": 0}
    try:
        approval.check()
        if approval.remaining <= 0:
            raise ValueError("The session message limit is reached. Approve a new workflow to continue.")
        path = approval.database_path
        source = "mock" if approval.offline or approval.config.provider != "gmail" else "gmail"
        with closing(open_database(path)) as connection:
            rows = connection.execute("""SELECT e.id FROM emails e WHERE e.status!='done' AND e.provider=?
                AND (?='mock' OR lower(e.source_mailbox)=lower(?)) AND NOT EXISTS(
                SELECT 1 FROM desktop_test_results r WHERE r.email_id=e.id AND r.mode=? AND r.offline=?
                AND r.outcome IN ('prepared','drafted','sent','simulated_sent')) ORDER BY e.received_ts DESC,e.id DESC LIMIT ?""",
                (source, source, approval.config.test_mailbox, approval.mode, approval.offline,
                 min(approval.batch_size, approval.remaining))).fetchall()
        if not rows:
            notify("No eligible messages remain. Add a sample email, import new mail, or check messages needing assignment/attention.")
        for index, row in enumerate(rows, 1):
            approval.check()
            approval.remaining -= 1
            email = get_email(path, row["id"])
            notify(f"Processing email #{row['id']} ({index}/{len(rows)})...")
            if email.customer_id is None:
                with closing(open_database(path)) as connection:
                    matches = connection.execute("SELECT id FROM customers WHERE lower(email)=lower(?)", (email.from_email,)).fetchall()
                if len(matches) == 1:
                    try:
                        assign_email_to_customer(path, email.id, matches[0]["id"])
                    except ValueError:
                        pass  # A concurrent explicit assignment wins.
                    email = get_email(path, email.id)
            if email.customer_id is None:
                _save_result(path, email.id, approval, "needs_attention", "Link the sender to a customer first.")
                notify(f"Email #{email.id}: needs attention — link the sender to a customer.")
                counts["attention"] += 1
                continue
            if not approval.offline and approval.mode == "send" and email.from_email.strip().lower() not in approval.config.send_allowlist:
                _save_result(path, email.id, approval, "needs_attention", "Recipient is outside the approved sending list.")
                notify(f"Email #{email.id}: skipped — recipient is outside the approved sending list.")
                counts["attention"] += 1
                continue
            result = None
            try:
                # Offline simulation never trusts a caller-supplied network model.
                model = FakeLLMProvider() if approval.offline else llm or FakeLLMProvider()
                result = process_email(email, path, ApprovedModel(model, approval.check))
                approval.check()
                if result.status != "pending":
                    _save_result(path, email.id, approval, "needs_attention", result.reason or "No safe approved answer matched.")
                    notify(f"Email #{email.id}: needs attention — " + (result.reason or "No safe approved answer matched."))
                    counts["attention"] += 1
                    continue
                def check():
                    approval.check()
                    _check_answer(path, result.draft_id, result.faq)
                check()
                outcome = "prepared"
                if approval.offline and approval.mode in {"draft", "send"}:
                    outcome = "drafted" if approval.mode == "draft" else "simulated_sent"
                    if approval.mode == "send":
                        with closing(open_database(path)) as connection, connection:
                            connection.execute("BEGIN IMMEDIATE")
                            check()
                            connection.execute("UPDATE draft_replies SET status='approved' WHERE id=?", (result.draft_id,))
                            connection.execute("UPDATE emails SET status='done' WHERE id=?", (email.id,))
                elif approval.mode in {"draft", "send"}:
                    guarded = ApprovedProvider(provider, check)
                    preview = delivery.snapshot(path, result.draft_id, approval.config)
                    delivery.deliver(path, result.draft_id, preview["content_hash"], approval.config, guarded)
                    outcome = "drafted"
                    if approval.mode == "send":
                        check()
                        delivery.deliver(path, result.draft_id, preview["content_hash"], approval.config, guarded, send=True)
                        outcome = "sent"
                _save_result(path, email.id, approval, outcome, draft_id=result.draft_id)
                counts[outcome] += 1
                notify(f"Email #{email.id}: {outcome.replace('_', ' ')}")
            except Exception as error:
                outbox = delivery.outbox_for_draft(path, result.draft_id) if result and result.draft_id else None
                uncertain = bool(outbox and outbox["status"] in {"unknown", "creating", "sending"})
                _save_result(path, email.id, approval, "uncertain" if uncertain else "needs_attention",
                             "Check Gmail status before any further delivery." if uncertain else "Workflow stopped for this message. Check settings, saved answers or reply state.")
                counts["attention"] += 1
                from app.providers.document_model import DocumentError
                from app.providers.email_base import EmailDeliveryError
                detail = str(error) if isinstance(error, (DocumentError, EmailDeliveryError)) else "Check settings, saved answers or reply state."
                notify(f"Email #{email.id}: workflow could not finish. " + detail)
                if uncertain or approval.revoked.is_set() or getattr(error, "fatal", False):
                    approval.revoked.set()
                    break
        notify("Finished: " + ", ".join(f"{value} {key.replace('_', ' ')}" for key, value in counts.items()) + f". Session budget left: {approval.remaining}.")
        return counts
    finally:
        approval.run_lock.release()
