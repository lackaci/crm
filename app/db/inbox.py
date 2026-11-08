"""Indexed, bounded desktop inbox queries and additive migration."""
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import SimpleNamespace

from .database import open_database
from app.reply.guardrails import check_escalation


def received_timestamp(value):
    try:
        try:
            date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            date = parsedate_to_datetime(str(value))
        return int(date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp())
    except (ValueError, TypeError, OverflowError):
        return 0


def attention_reason(subject, body):
    result = check_escalation(SimpleNamespace(subject=subject, body=body))
    return result.reason if result else ""


def migrate_inbox(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(emails)")}
    for name, definition in {"received_ts": "INTEGER NOT NULL DEFAULT 0",
                             "needs_attention": "INTEGER NOT NULL DEFAULT 0",
                             "attention_reason": "TEXT NOT NULL DEFAULT ''"}.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE emails ADD COLUMN {name} {definition}")
    if "received_ts" not in columns:
        for row in connection.execute("SELECT id,received_at,subject,body FROM emails").fetchall():
            reason = attention_reason(row[2], row[3])
            connection.execute("UPDATE emails SET received_ts=?,needs_attention=?,attention_reason=? WHERE id=?",
                               (received_timestamp(row[1]), bool(reason), reason, row[0]))
    for name, expression in {
        "idx_inbox_date": "emails(received_ts DESC,id DESC)",
        "idx_inbox_customer": "emails(customer_id,received_ts DESC,id DESC)",
        "idx_inbox_unread": "emails(status,received_ts DESC,id DESC)",
        "idx_inbox_attention": "emails(needs_attention,status)",
        "idx_inbox_pending": "draft_replies(email_id,status)",
        "idx_customer_email_folded": "customers(lower(email))",
        "idx_sent_reply_date": "desktop_outbox(status,updated_at DESC,id)",
    }.items():
        connection.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {expression}")
    # External-content FTS stores only the search index, not another body copy.
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='desktop_email_search'").fetchone()
    connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS desktop_email_search USING fts5("
                       "from_name,from_email,subject,body,content='emails',content_rowid='id',tokenize='trigram')")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS desktop_email_search_insert AFTER INSERT ON emails BEGIN
        INSERT INTO desktop_email_search(rowid,from_name,from_email,subject,body)
        VALUES(new.id,new.from_name,new.from_email,new.subject,new.body); END""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS desktop_email_search_delete AFTER DELETE ON emails BEGIN
        INSERT INTO desktop_email_search(desktop_email_search,rowid,from_name,from_email,subject,body)
        VALUES('delete',old.id,old.from_name,old.from_email,old.subject,old.body); END""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS desktop_email_search_update AFTER UPDATE OF from_name,from_email,subject,body ON emails BEGIN
        INSERT INTO desktop_email_search(desktop_email_search,rowid,from_name,from_email,subject,body)
        VALUES('delete',old.id,old.from_name,old.from_email,old.subject,old.body);
        INSERT INTO desktop_email_search(rowid,from_name,from_email,subject,body)
        VALUES(new.id,new.from_name,new.from_email,new.subject,new.body); END""")
    if not exists:
        connection.execute("INSERT INTO desktop_email_search(desktop_email_search) VALUES('rebuild')")
    connection.execute("""CREATE TABLE IF NOT EXISTS desktop_gmail_sync(
        id INTEGER PRIMARY KEY, mailbox TEXT NOT NULL, profile TEXT NOT NULL,
        history_id TEXT, mode TEXT, page_token TEXT, target_history_id TEXT,
        UNIQUE(mailbox,profile))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS desktop_gmail_queue(
        id INTEGER PRIMARY KEY, sync_id INTEGER NOT NULL REFERENCES desktop_gmail_sync(id),
        remote_id TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        UNIQUE(sync_id,remote_id))""")


PENDING = "EXISTS(SELECT 1 FROM draft_replies d WHERE d.email_id=e.id AND d.status='pending')"
REPLIED = "EXISTS(SELECT 1 FROM desktop_outbox o WHERE o.email_id=e.id AND o.status='sent')"
ATTENTION = "(e.needs_attention=1 OR t.outcome='needs_attention' OR EXISTS(SELECT 1 FROM draft_replies d WHERE d.email_id=e.id AND d.status='escalated')) AND e.status!='done'"
FILTERS = {"all": "1", "unread": "e.status='new'", "assignment": "e.customer_id IS NULL",
           "attention": ATTENTION, "pending": PENDING}
JOIN = "FROM emails e LEFT JOIN customers c ON c.id=e.customer_id LEFT JOIN desktop_email_triage t ON t.email_id=e.id LEFT JOIN desktop_staff s ON s.id=t.staff_id"


def _scope(search="", customer_id=None):
    # Keep sent originals in SQLite/history, outside every active Inbox filter.
    terms, parameters = [f"NOT ({REPLIED})"], []
    if customer_id is not None:
        terms.append("e.customer_id=?")
        parameters.append(customer_id)
    text = search.strip()
    if text:
        if len(text) >= 3:
            terms.append("(e.id IN (SELECT rowid FROM desktop_email_search WHERE desktop_email_search MATCH ?) OR s.name LIKE ? ESCAPE '\\')")
            parameters.extend(['"' + text.replace('"', '""') + '"', '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'])
        else:
            terms.append("(e.from_name||' '||e.from_email||' '||e.subject||' '||e.body) LIKE ? ESCAPE '\\'")
            parameters.append('%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    return " AND ".join(terms), parameters


def counts(database_path, search="", customer_id=None):
    where, parameters = _scope(search, customer_id)
    with closing(open_database(database_path)) as connection:
        row = connection.execute("SELECT " + ",".join(f'COALESCE(SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END),0) AS "{key}"' for key,predicate in FILTERS.items())
                                 + " " + JOIN + " WHERE " + where, parameters).fetchone()
        return dict(row)


def messages(database_path, *, filter_name="all", search="", customer_id=None, limit=100, offset=0):
    if filter_name not in FILTERS:
        raise ValueError("Choose a valid inbox filter.")
    where, parameters = _scope(search, customer_id)
    with closing(open_database(database_path)) as connection:
        return [dict(row) for row in connection.execute(
            f"SELECT e.*,c.name AS customer_name,s.name AS staff_name,t.category,t.priority,t.reason AS triage_reason,"
            "(SELECT j.status FROM desktop_draft_jobs j WHERE j.email_id=e.id) AS ai_status,"
            "EXISTS(SELECT 1 FROM desktop_reply_approvals a JOIN draft_replies d ON d.id=a.draft_id WHERE d.email_id=e.id AND d.status='pending') AS reply_approved,"
            "EXISTS(SELECT 1 FROM desktop_outbox o WHERE o.email_id=e.id AND o.status='sent') AS reply_sent,"
            f"CASE WHEN {PENDING} THEN 1 ELSE 0 END AS reply_pending,CASE WHEN {ATTENTION} THEN 1 ELSE 0 END AS attention "
            + JOIN + f" WHERE {where} AND ({FILTERS[filter_name]}) ORDER BY e.received_ts DESC,e.id DESC LIMIT ? OFFSET ?",
            [*parameters, min(max(int(limit), 1), 200), max(int(offset), 0)])]


def mark_attention(database_path, email_id, reason):
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("UPDATE emails SET needs_attention=1,attention_reason=? WHERE id=?", (reason, email_id))
