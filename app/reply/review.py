"""Exact-content human approval and one-click durable Gmail delivery."""
from contextlib import closing
from hashlib import sha256
import json

from app.db.database import open_database
from app.db import email_workflow as store
from . import delivery


def snapshot(path, draft_id, config, visible_body=None):
    with closing(open_database(path)) as connection:
        row = connection.execute("SELECT d.body,d.status,e.id AS email_id,e.status AS source_status,e.from_email,e.subject,e.provider,e.source_mailbox,e.remote_message_id,e.remote_thread_id "
                                 "FROM draft_replies d JOIN emails e ON e.id=d.email_id WHERE d.id=?", (draft_id,)).fetchone()
    if not row or row["status"] != "pending" or row["source_status"] == "done":
        raise ValueError("Select an unfinished pending reply.")
    if visible_body is not None and row["body"] != visible_body:
        raise ValueError("Save and approve your changed reply before sending.")
    if row["provider"] == "gmail":
        return delivery.snapshot(path, draft_id, config, visible_body)
    values = {"sender": config.test_mailbox or "offline@example.invalid", "recipient": row["from_email"],
              "subject": row["subject"], "body": row["body"], "email_id": row["email_id"],
              "thread_id": row["remote_thread_id"], "original_message_id": row["remote_message_id"]}
    values["content_hash"] = sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return values


def approve(path, draft_id, config, visible_body):
    with closing(open_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        preview = snapshot(path, draft_id, config, visible_body)
        connection.execute("INSERT INTO desktop_reply_approvals(draft_id,content_hash) VALUES(?,?) "
                           "ON CONFLICT(draft_id) DO UPDATE SET content_hash=excluded.content_hash,approved_at=CURRENT_TIMESTAMP",
                           (draft_id, preview["content_hash"]))
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('owner','reply_content_approved',?)", (f"draft_id={draft_id}",))
    return preview


def require_approved(path, draft_id, config, expected_hash=None, visible_body=None):
    preview = snapshot(path, draft_id, config, visible_body)
    with closing(open_database(path)) as connection:
        row = connection.execute("SELECT content_hash FROM desktop_reply_approvals WHERE draft_id=?", (draft_id,)).fetchone()
    if not row or row["content_hash"] != preview["content_hash"] or (expected_hash and expected_hash != row["content_hash"]):
        raise ValueError("Approve this exact reply before sending. Changes require new approval.")
    return preview


class ApprovedDelivery:
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
    def send_reviewed_draft(self, reply, remote_id):
        self.check()
        return self.provider.send_reviewed_draft(reply, remote_id)


def send_approved(path, draft_id, approved_hash, config, provider):
    owner = store.acquire(path, "send:" + str(draft_id), ttl=600)
    if not owner:
        raise ValueError("This reply is already being sent. Check its status.")
    try:
        def check():
            require_approved(path, draft_id, config, approved_hash)
        check()
        guarded = ApprovedDelivery(provider, check)
        # This policy check precedes creating a remote draft as part of Send.
        delivery._policy(config, provider, send=True)
        preview = require_approved(path, draft_id, config, approved_hash)
        if preview["recipient"] not in config.send_allowlist:
            raise ValueError("The recipient is not in the allowed sending list in Setup.")
        delivery.deliver(path, draft_id, approved_hash, config, guarded)
        check()
        return delivery.deliver(path, draft_id, approved_hash, config, guarded, send=True)
    finally:
        store.release(path, "send:" + str(draft_id), owner)
