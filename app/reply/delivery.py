"""Explicitly approved Gmail delivery with durable intent and no blind retries."""
from hashlib import sha256
import json
import re
from uuid import uuid4

from app.config import RunMode
from app.db.database import open_database
from app.providers.email_base import ReviewedReply, DeliveryRejected, DeliveryUncertain


def _audit(connection, action, record_id):
    connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('owner',?,?)", (action, "outbox_id=" + record_id))


def _not_paused(connection):
    row = connection.execute("SELECT paused FROM desktop_mail_settings WHERE id=1").fetchone()
    if row and row["paused"]:
        raise ValueError("Mail activity is paused. Resume it in Gmail settings.")


def _policy(config, provider, send=False):
    if (config is None or config.mode is RunMode.MOCK or config.provider != "gmail"
            or not config.gmail_write_enabled or getattr(provider, "provider_name", None) != "gmail"):
        raise ValueError("Connect Gmail with draft permissions first. Demo mode cannot write to Gmail.")
    if send and not config.sending_enabled:
        raise ValueError("Enable reviewed sending in Live mode in Gmail settings first.")


def snapshot(database_path, draft_id, config, visible_body=None):
    with open_database(database_path) as connection:
        row = connection.execute("SELECT d.body,d.status,e.id AS email_id,e.from_email,e.subject,e.provider,e.source_mailbox,e.remote_message_id,e.remote_thread_id "
            "FROM draft_replies d JOIN emails e ON e.id=d.email_id WHERE d.id=?", (draft_id,)).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("Only a pending reply can be reviewed for delivery.")
        if visible_body is not None and visible_body != row["body"]:
            raise ValueError("Save your reply changes before creating a Gmail draft or sending.")
        if row["provider"] != "gmail":
            raise ValueError("Only replies to imported Gmail messages can be sent through Gmail.")
        recipient = row["from_email"].strip().lower()
        sender = (config.test_mailbox or "").strip().lower()
        if (row["source_mailbox"] or "").lower() != sender:
            raise ValueError("Re-import this message from the connected Gmail account before sending.")
        pattern = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}"
        if not re.fullmatch(pattern, recipient) or not re.fullmatch(pattern, sender):
            raise ValueError("A single valid Gmail account and recipient are required.")
        subject = row["subject"] if row["subject"].lower().startswith("re:") else "Re: " + row["subject"]
        if not row["body"].strip() or len(row["body"]) > 1_000_000 or "\r" in subject or "\n" in subject:
            raise ValueError("The reply body or subject is invalid.")
        values = {"sender": sender, "recipient": recipient, "subject": subject, "body": row["body"],
            "thread_id": row["remote_thread_id"], "original_message_id": row["remote_message_id"], "email_id": row["email_id"]}
        values["content_hash"] = sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return values


def outbox_for_draft(database_path, draft_id):
    with open_database(database_path) as connection:
        row = connection.execute("SELECT * FROM desktop_outbox WHERE draft_id=?", (draft_id,)).fetchone()
        return dict(row) if row else None


def list_outbox(database_path):
    with open_database(database_path) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM desktop_outbox ORDER BY created_at DESC,id").fetchall()]


def _envelope(row):
    return ReviewedReply(row["sender"], row["recipient"], row["subject"], row["body"], row["rfc_message_id"], row["thread_id"], row["original_message_id"])


def _ensure_snapshot(connection, draft_id, approved):
    row = connection.execute("SELECT d.body,d.status,e.from_email,e.subject,e.source_mailbox,e.remote_message_id,e.remote_thread_id FROM draft_replies d JOIN emails e ON e.id=d.email_id WHERE d.id=?", (draft_id,)).fetchone()
    subject = row["subject"] if row and row["subject"].lower().startswith("re:") else "Re: " + (row["subject"] if row else "")
    if (not row or row["status"] != "pending" or row["body"] != approved["body"] or row["from_email"].strip().lower() != approved["recipient"]
            or subject != approved["subject"] or (row["source_mailbox"] or "").lower() != approved["sender"]
            or row["remote_message_id"] != approved["original_message_id"] or row["remote_thread_id"] != approved["thread_id"]):
        raise ValueError("The reply changed after review. Review the current version again.")


def _stage_intent(database_path, draft_id, approved, operation):
    with open_database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _not_paused(connection)
        _ensure_snapshot(connection, draft_id, approved)
        row = connection.execute("SELECT * FROM desktop_outbox WHERE draft_id=?", (draft_id,)).fetchone()
        if row and row["status"] in {"creating", "sending", "unknown", "sent"}:
            raise ValueError("This reply already has a delivery attempt. Check its Gmail status; it will not be resent.")
        if row and row["status"] == "drafted" and row["content_hash"] != approved["content_hash"]:
            raise ValueError("The staged Gmail draft differs from this reply. Cancel it before editing.")
        if row and row["status"] == "drafted" and operation == "create":
            return dict(row), False
        if operation == "send" and (not row or row["status"] != "drafted"):
            raise ValueError("Create the Gmail draft first, then review and approve sending.")
        record_id = row["id"] if row else str(uuid4())
        duplicate = connection.execute("SELECT id FROM desktop_outbox WHERE email_id=? AND content_hash=? AND draft_id!=?", (approved["email_id"],approved["content_hash"],draft_id)).fetchone()
        if duplicate:
            raise ValueError("An identical reply already has a delivery record. Check its status first.")
        if not row:
            connection.execute("INSERT INTO desktop_outbox(id,draft_id,email_id,sender,recipient,subject,body,content_hash,rfc_message_id,thread_id,original_message_id,status,operation) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (record_id, draft_id, approved["email_id"], approved["sender"], approved["recipient"], approved["subject"], approved["body"],
                    approved["content_hash"], f"<{record_id}@mini-crm.invalid>", approved["thread_id"], approved["original_message_id"], "creating", operation))
        else:
            connection.execute("UPDATE desktop_outbox SET sender=?,recipient=?,subject=?,body=?,content_hash=?,thread_id=?,original_message_id=?,status=?,operation=?,error_code=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (approved["sender"],approved["recipient"],approved["subject"],approved["body"],approved["content_hash"],approved["thread_id"],approved["original_message_id"],
                 "sending" if operation == "send" else "creating",operation,record_id))
        _audit(connection, "gmail_send_approved" if operation == "send" else "gmail_draft_approved", record_id)
        return dict(connection.execute("SELECT * FROM desktop_outbox WHERE id=?", (record_id,)).fetchone()), True


def deliver(database_path, draft_id, approved_hash, config, provider, *, send=False):
    _policy(config, provider, send)
    approved = snapshot(database_path, draft_id, config)
    if approved["content_hash"] != approved_hash:
        raise ValueError("The reply or account changed after confirmation. Review it again.")
    if send and approved["recipient"] not in config.send_allowlist:
        raise ValueError("This recipient is not in the allowed sending recipients in Gmail settings.")
    # Authentication/profile checks happen before any durable mutation intent.
    provider.verify_connection(allow_login=False)
    row, perform = _stage_intent(database_path, draft_id, approved, "send" if send else "create")
    if not perform:
        return row
    error = None
    with open_database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT * FROM desktop_outbox WHERE id=?", (row["id"],)).fetchone()
        if current["status"] != ("sending" if send else "creating"):
            raise ValueError("This delivery attempt is no longer current.")
        try:
            _not_paused(connection)
            _ensure_snapshot(connection, draft_id, approved)
            if send:
                remote_id = provider.send_reviewed_draft(_envelope(row), row["gmail_draft_id"])
                connection.execute("UPDATE desktop_outbox SET status='sent',gmail_message_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (remote_id,row["id"]))
                connection.execute("UPDATE draft_replies SET status='approved' WHERE id=?", (draft_id,))
                connection.execute("UPDATE emails SET status='done' WHERE id=?", (row["email_id"],))
            else:
                remote_id = provider.create_reviewed_draft(_envelope(row))
                connection.execute("UPDATE desktop_outbox SET status='drafted',gmail_draft_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (remote_id,row["id"]))
            _audit(connection, "gmail_sent" if send else "gmail_draft_created", row["id"])
        except (DeliveryRejected, ValueError) as caught:
            error = caught
            connection.execute("UPDATE desktop_outbox SET status=?,error_code='rejected',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                ("drafted" if send else "failed", row["id"]))
            _audit(connection, "gmail_operation_rejected", row["id"])
        except Exception:
            error = DeliveryUncertain("Gmail's response is uncertain. Check delivery status; no automatic retry will occur.")
            connection.execute("UPDATE desktop_outbox SET status='unknown',error_code='uncertain',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            _audit(connection, "gmail_outcome_unknown", row["id"])
    if error:
        raise error
    return outbox_for_draft(database_path, draft_id)


def reconcile(database_path, draft_id, config, provider):
    _policy(config, provider)
    with open_database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _not_paused(connection)
        row = connection.execute("SELECT * FROM desktop_outbox WHERE draft_id=?", (draft_id,)).fetchone()
        if row is None:
            raise ValueError("There is no Gmail operation to check.")
        if row["sender"] != config.test_mailbox.lower():
            raise ValueError("Reconnect the Gmail account that owns this delivery record.")
        if row["status"] == "sent":
            return dict(row)
        remote_id = provider.lookup_reviewed_reply(_envelope(row), row["operation"])
        if remote_id:
            if row["operation"] == "send":
                connection.execute("UPDATE desktop_outbox SET status='sent',gmail_message_id=?,error_code=NULL WHERE id=?", (remote_id,row["id"]))
                connection.execute("UPDATE draft_replies SET status='approved' WHERE id=?", (draft_id,))
                connection.execute("UPDATE emails SET status='done' WHERE id=?", (row["email_id"],))
            else:
                connection.execute("UPDATE desktop_outbox SET status='drafted',gmail_draft_id=?,error_code=NULL WHERE id=?", (remote_id,row["id"]))
            _audit(connection, "gmail_operation_reconciled", row["id"])
        else:
            _audit(connection, "gmail_status_unconfirmed", row["id"])
        return dict(connection.execute("SELECT * FROM desktop_outbox WHERE id=?", (row["id"],)).fetchone())


def cancel_gmail_draft(database_path, draft_id, config, provider):
    _policy(config, provider)
    with open_database(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _not_paused(connection)
        row = connection.execute("SELECT * FROM desktop_outbox WHERE draft_id=?", (draft_id,)).fetchone()
        if not row or row["status"] != "drafted":
            raise ValueError("Only a confirmed Gmail draft can be cancelled here.")
        provider.delete_reviewed_draft(_envelope(row), row["gmail_draft_id"])
        connection.execute("UPDATE desktop_outbox SET status='cancelled',gmail_draft_id=NULL,operation='create',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        _audit(connection, "gmail_draft_cancelled", row["id"])


def require_editable(connection, draft_id):
    row = connection.execute("SELECT status FROM desktop_outbox WHERE draft_id=?", (draft_id,)).fetchone()
    if row and row["status"] not in {"failed", "cancelled"}:
        raise ValueError("Cancel the staged Gmail draft, or check the uncertain delivery status, before editing or discarding.")
