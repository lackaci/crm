"""Resumable desktop Gmail discovery and per-message import (read-only)."""
from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json

from app.config import ConfigurationError
from app.db.database import open_database
from app.db.emails import import_emails
from app.db.connections import mail_paused
from app.providers.gmail import GmailSyncError


@dataclass
class ImportResult:
    imported: int = 0
    existing: int = 0
    failed: int = 0
    remaining: int = 0
    error: str | None = None


def readable_error(error):
    """Never expose provider response bodies or credential values."""
    status = getattr(getattr(error, "resp", None), "status", None)
    if status == 401 or error.__class__.__name__ == "RefreshError":
        return "Gmail authorization expired. Open Gmail connection and reconnect, then Refresh."
    if status in {403, 429}:
        return "Gmail quota or access limit reached. Wait before refreshing; check Gmail API access in your Google project."
    if isinstance(error, ConfigurationError):
        return "Gmail needs authorization or its client file is missing. Open Gmail connection and reconnect."
    if isinstance(error, GmailSyncError):
        return "Gmail could not verify the account or read a message. Check Gmail connection and retry Refresh."
    if isinstance(error, (OSError, TimeoutError)) or error.__class__.__name__ in {"TransportError", "ServerNotFoundError"}:
        return "Cannot reach Gmail. Check your internet connection, then Refresh."
    return "Gmail could not finish this refresh. Saved messages are safe; use Refresh to retry unfinished messages."


def _update(database_path, state_id, **changes):
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("UPDATE desktop_gmail_sync SET " + ",".join(key + "=?" for key in changes) + " WHERE id=?",
                           [*changes.values(), state_id])


def synchronize_gmail(database_path, provider, *, storage_root=None, progress=None, allow_login=False):
    from app.db import email_workflow as leases
    name = 'gmail-import:' + (provider.expected_mailbox or '').casefold()
    owner = leases.acquire(database_path, name, ttl=180)
    if not owner:
        return ImportResult(error='Another Gmail import is already running. Its saved results will appear when it finishes.')
    def notify(message):
        leases.renew(database_path, name, owner, ttl=180)
        if progress:
            progress(message)
    try:
        return _synchronize_gmail(database_path, provider, storage_root=storage_root, progress=notify, allow_login=allow_login)
    finally:
        leases.release(database_path, name, owner)


def _synchronize_gmail(database_path, provider, *, storage_root=None, progress=None, allow_login=False):
    result = ImportResult()
    notify = progress or (lambda message: None)
    notify("Connecting to Gmail...")
    state_id = None
    try:
        service = provider._service_or_login(allow_login=allow_login)
        profile = service.users().getProfile(userId="me").execute(num_retries=0)
        mailbox = str(profile.get("emailAddress", "")).strip().casefold()
        if not mailbox or not provider.expected_mailbox or mailbox != provider.expected_mailbox.casefold():
            raise GmailSyncError("Mailbox mismatch")
        fingerprint = sha256(json.dumps(["incoming-v1", sorted(provider.allowed_senders), provider.newer_than_days]).encode()).hexdigest()
        with closing(open_database(database_path)) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO desktop_gmail_sync(mailbox,profile) VALUES(?,?)", (mailbox, fingerprint))
            state = dict(connection.execute("SELECT * FROM desktop_gmail_sync WHERE mailbox=? AND profile=?", (mailbox, fingerprint)).fetchone())
        state_id = state["id"]
        if not state["mode"]:
            state.update(mode="history" if state["history_id"] else "full", page_token=None,
                         target_history_id=str(profile["historyId"]))
            _update(database_path, state_id, mode=state["mode"], page_token=None, target_history_id=state["target_history_id"])
        while True:
            if mail_paused(database_path):
                result.error = "Mail activity is paused. Resume it in Gmail settings, then Refresh."
                break
            notify("Checking for new Gmail messages...")
            options = {"userId": "me", "maxResults": 500}
            if state["page_token"]:
                options["pageToken"] = state["page_token"]
            try:
                if state["mode"] == "history":
                    page = service.users().history().list(**options, startHistoryId=state["history_id"],
                                                        historyTypes=["messageAdded"]).execute(num_retries=0)
                    identifiers = [str(added["message"]["id"]) for event in page.get("history", [])
                                   for added in event.get("messagesAdded", []) if added.get("message", {}).get("id")]
                else:
                    clauses = ["-in:sent", "-in:drafts"]
                    if provider.allowed_senders:
                        clauses.append("{" + " ".join("from:" + sender for sender in provider.allowed_senders) + "}")
                    if provider.newer_than_days:
                        clauses.append(f"newer_than:{provider.newer_than_days}d")
                    page = service.users().messages().list(**options, q=" ".join(clauses)).execute(num_retries=0)
                    identifiers = [str(item["id"]) for item in page.get("messages", []) if item.get("id")]
            except Exception as error:
                status = getattr(getattr(error, "resp", None), "status", None)
                if status == 404 and state["mode"] == "history":
                    state.update(mode="full", page_token=None, target_history_id=str(profile["historyId"]))
                    _update(database_path, state_id, mode="full", page_token=None, target_history_id=state["target_history_id"])
                    notify("Gmail sync history expired; checking the configured lookback again...")
                    continue
                if status == 400 and state["page_token"]:
                    _update(database_path, state_id, page_token=None)
                raise
            next_page = page.get("nextPageToken")
            cursor = str(page.get("historyId") or state["target_history_id"])
            # Save discoveries and the page cursor together before downloading.
            with closing(open_database(database_path)) as connection, connection:
                connection.executemany("INSERT OR IGNORE INTO desktop_gmail_queue(sync_id,remote_id) VALUES(?,?)",
                                       [(state_id, item) for item in identifiers])
                connection.execute("UPDATE desktop_gmail_sync SET page_token=?,mode=?,history_id=? WHERE id=?",
                                   (next_page, state["mode"] if next_page else None,
                                    state["history_id"] if next_page else cursor, state_id))
            if not next_page:
                break
            state["page_token"] = next_page
    except Exception as error:
        result.error = readable_error(error)
        # Account verification failures must never use another account's queue.
        return result
    if result.error:
        return result
    with closing(open_database(database_path)) as connection:
        queued = connection.execute("SELECT remote_id FROM desktop_gmail_queue WHERE sync_id=? ORDER BY attempts,id LIMIT ?",
                                    (state_id, provider.max_messages)).fetchall()
    for number, row in enumerate(queued, 1):
        if mail_paused(database_path):
            result.error = "Mail activity is paused. Resume it in Gmail settings, then Refresh."
            break
        message_id = row["remote_id"]
        notify(f"Importing... {number - 1}/{len(queued)}")
        remove = False
        stop = False
        try:
            with closing(open_database(database_path)) as connection:
                existing = connection.execute("SELECT 1 FROM emails WHERE provider='gmail' AND source_mailbox=? AND remote_message_id=?",
                                              (mailbox, message_id)).fetchone()
            if existing:
                result.existing += 1
                remove = True
            else:
                message = service.users().messages().get(userId="me", id=message_id, format="full").execute(num_retries=0)
                if set(message.get("labelIds", [])) & {"SENT", "DRAFT", "SPAM", "TRASH"}:
                    with closing(open_database(database_path)) as connection, connection:
                        connection.execute("DELETE FROM desktop_gmail_queue WHERE sync_id=? AND remote_id=?", (state_id, message_id))
                    notify(f"Importing... {number}/{len(queued)}")
                    continue
                record = provider._normalize(message, service)
                # History is mailbox-wide; enforce configured sender/date scope.
                from app.db.inbox import received_timestamp
                import time
                in_scope = (not provider.allowed_senders or record["from_email"].casefold() in provider.allowed_senders)
                if provider.newer_than_days and received_timestamp(record["received_at"]):
                    in_scope = in_scope and received_timestamp(record["received_at"]) >= time.time() - provider.newer_than_days * 86400
                if in_scope:
                    class SingleMessage:
                        def fetch_new_emails(self):
                            return [record]
                    added = import_emails(database_path, SingleMessage(), storage_root=storage_root)
                    result.imported += added
                    result.existing += int(not added)
                remove = True
        except Exception as error:
            if getattr(getattr(error, "resp", None), "status", None) == 404:
                # Message removed from Gmail before download; no retry needed.
                remove = True
            else:
                result.failed += 1
                result.error = readable_error(error)
                stop = (getattr(getattr(error, "resp", None), "status", None) in {401, 403, 429}
                        or isinstance(error, (ConfigurationError, OSError)))
        with closing(open_database(database_path)) as connection, connection:
            if remove:
                connection.execute("DELETE FROM desktop_gmail_queue WHERE sync_id=? AND remote_id=?", (state_id, message_id))
            else:
                connection.execute("UPDATE desktop_gmail_queue SET attempts=attempts+1 WHERE sync_id=? AND remote_id=?", (state_id, message_id))
        notify(f"Importing... {number}/{len(queued)}")
        if stop:
            break
    with closing(open_database(database_path)) as connection:
        result.remaining = connection.execute("SELECT COUNT(*) FROM desktop_gmail_queue WHERE sync_id=?", (state_id,)).fetchone()[0]
    return result
