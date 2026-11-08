"""Additive desktop workflow storage: settings, jobs, approvals and leases."""
from contextlib import closing
from hashlib import sha256
import json
import time
from uuid import uuid4

from .database import open_database

FREE_MODEL = "nvidia/nemotron-3.5-lightning-free"


def migrate(connection):
    statements = [
        """CREATE TABLE IF NOT EXISTS desktop_email_workflow(
            id INTEGER PRIMARY KEY CHECK(id=1), selected INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 0, interval_seconds INTEGER NOT NULL DEFAULT 300,
            auto_draft INTEGER NOT NULL DEFAULT 1, configured INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT 'nvidia/nemotron-3.5-lightning-free', key_env TEXT NOT NULL DEFAULT 'AI_GATEWAY_API_KEY',
            free_only INTEGER NOT NULL DEFAULT 1, tested_profile TEXT, ai_paused INTEGER NOT NULL DEFAULT 0,
            ai_error TEXT, last_check REAL, next_check REAL, last_result TEXT)""",
        "INSERT OR IGNORE INTO desktop_email_workflow(id,model) VALUES(1,'nvidia/nemotron-3.5-lightning-free')",
        """CREATE TABLE IF NOT EXISTS desktop_draft_jobs(
            email_id INTEGER PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE, source_hash TEXT NOT NULL,
            profile TEXT NOT NULL, status TEXT NOT NULL, draft_id INTEGER REFERENCES draft_replies(id) ON DELETE SET NULL,
            attempts INTEGER NOT NULL DEFAULT 0, sources_json TEXT, reason TEXT,
            owner TEXT, lease_until REAL, next_attempt REAL NOT NULL DEFAULT 0, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS desktop_reply_approvals(
            draft_id INTEGER PRIMARY KEY REFERENCES draft_replies(id) ON DELETE CASCADE, content_hash TEXT NOT NULL,
            approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS desktop_workflow_leases(
            name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)""",
        """CREATE TRIGGER IF NOT EXISTS desktop_revoke_reply_edit AFTER UPDATE OF body,status ON draft_replies BEGIN
            DELETE FROM desktop_reply_approvals WHERE draft_id=old.id; END""",
        """CREATE TRIGGER IF NOT EXISTS desktop_revoke_source_edit AFTER UPDATE OF from_email,subject,body,customer_id,provider,source_mailbox,remote_message_id,remote_thread_id ON emails BEGIN
            DELETE FROM desktop_reply_approvals WHERE draft_id IN (SELECT id FROM draft_replies WHERE email_id=old.id); END""",
        "CREATE INDEX IF NOT EXISTS idx_draft_job_status ON desktop_draft_jobs(status,lease_until)",
    ]
    for statement in statements:
        connection.execute(statement)
    columns = {row[1] for row in connection.execute('PRAGMA table_info(desktop_draft_jobs)')}
    if 'next_attempt' not in columns:
        connection.execute('ALTER TABLE desktop_draft_jobs ADD COLUMN next_attempt REAL NOT NULL DEFAULT 0')
    # Preserve mail settings and saved replies; validate the newly chosen model.
    connection.execute("UPDATE desktop_email_workflow SET model=?,tested_profile=NULL,ai_paused=1,ai_error=? "
                       "WHERE model='reflection/beam-free'", (FREE_MODEL,
        'Email model changed to Nemotron Free. This provider logs prompts and outputs and permits non-sensitive trial use only. Test AI connection in Setup before resuming.'))


def settings(path):
    with closing(open_database(path)) as connection:
        return dict(connection.execute("SELECT * FROM desktop_email_workflow WHERE id=1").fetchone())


def update(path, **values):
    allowed = {"selected", "enabled", "auto_draft", "configured", "model", "key_env", "free_only",
               "tested_profile", "ai_paused", "ai_error", "last_check", "next_check", "last_result"}
    if not values or set(values) - allowed:
        raise ValueError("Unsupported workflow setting.")
    with closing(open_database(path)) as connection, connection:
        connection.execute("UPDATE desktop_email_workflow SET " + ",".join(key + "=?" for key in values) + " WHERE id=1", list(values.values()))


def profile(config):
    return sha256(json.dumps([config["model"], config["key_env"], bool(config["free_only"]), "email-reply-v1"]).encode()).hexdigest()


def save_ai(path, model, key_env, free_only=True):
    from app.providers.online_model import OnlineSettings, VERCEL_ENDPOINT
    if free_only and model != FREE_MODEL:
        raise ValueError(f"Free-only mode uses {FREE_MODEL}. No paid fallback is allowed.")
    OnlineSettings(model, "vercel", VERCEL_ENDPOINT, key_env, "openai/text-embedding-3-small")
    update(path, selected=1, configured=1, model=model, key_env=key_env, free_only=bool(free_only),
           tested_profile=None, ai_paused=0, ai_error=None)


def set_running(path, enabled, auto_draft=True, now=None):
    now = time.time() if now is None else now
    update(path, selected=1, enabled=bool(enabled), auto_draft=bool(auto_draft), next_check=now if enabled else None)


def claim_poll(path, now=None):
    now = time.time() if now is None else now
    with closing(open_database(path)) as connection, connection:
        cursor = connection.execute("UPDATE desktop_email_workflow SET next_check=? WHERE id=1 AND enabled=1 "
                                    "AND (next_check IS NULL OR next_check<=?)", (now + 300, now))
        return cursor.rowcount == 1


def acquire(path, name, ttl=180, now=None):
    now = time.time() if now is None else now
    owner = str(uuid4())
    with closing(open_database(path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT expires FROM desktop_workflow_leases WHERE name=?", (name,)).fetchone()
        if row and row["expires"] > now:
            return None
        connection.execute("INSERT INTO desktop_workflow_leases(name,owner,expires) VALUES(?,?,?) "
                           "ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires", (name, owner, now + ttl))
    return owner


def renew(path, name, owner, ttl=180):
    with closing(open_database(path)) as connection, connection:
        cursor = connection.execute("UPDATE desktop_workflow_leases SET expires=? WHERE name=? AND owner=? AND expires>?",
                                    (time.time() + ttl, name, owner, time.time()))
        if cursor.rowcount != 1:
            raise ValueError("The workflow lease expired. Another check will resume unfinished work.")


def release(path, name, owner):
    with closing(open_database(path)) as connection, connection:
        connection.execute("DELETE FROM desktop_workflow_leases WHERE name=? AND owner=?", (name, owner))


def source_hash(email):
    return sha256(json.dumps([email.id, email.subject, email.body, email.customer_id, email.from_email], ensure_ascii=False).encode()).hexdigest()


def source_version(path, email_id):
    with closing(open_database(path)) as c:
        row = c.execute('SELECT subject,body,customer_id,from_email,from_name,provider,source_mailbox,remote_message_id,remote_thread_id FROM emails WHERE id=?', (email_id,)).fetchone()
    return sha256(json.dumps(list(row) if row else None, ensure_ascii=False).encode()).hexdigest()


def enqueue(path, *, mailbox=None, email_id=None, force=False):
    config = settings(path)
    from .emails import get_email
    with closing(open_database(path)) as connection, connection:
        where, arguments = "e.status!='done'", []
        if email_id is not None:
            where += " AND e.id=?"
            arguments.append(email_id)
        if mailbox:
            where += " AND e.provider='gmail' AND lower(e.source_mailbox)=lower(?)"
            arguments.append(mailbox)
        identifiers = [row[0] for row in connection.execute("SELECT e.id FROM emails e WHERE " + where, arguments)]
        for identifier in identifiers:
            email = get_email(path, identifier)
            pending = connection.execute("SELECT id FROM draft_replies WHERE email_id=? AND status='pending'", (identifier,)).fetchone()
            if pending and not force:
                continue  # Never overwrite a person's existing draft.
            current = connection.execute("SELECT * FROM desktop_draft_jobs WHERE email_id=?", (identifier,)).fetchone()
            digest = source_version(path, identifier)
            if current and not force and current["source_hash"] == digest and current["profile"] == profile(config) and current["status"] in {"ready", "needs_info"}:
                continue
            if current and current["status"] == "generating" and (current["lease_until"] or 0) > time.time():
                continue
            connection.execute("""INSERT INTO desktop_draft_jobs(email_id,source_hash,profile,status)
                VALUES(?,?,?,'queued') ON CONFLICT(email_id) DO UPDATE SET source_hash=excluded.source_hash,
                profile=excluded.profile,status='queued',reason=NULL,owner=NULL,lease_until=NULL""", (identifier, digest, profile(config)))


def job_counts(path):
    with closing(open_database(path)) as connection:
        return dict(connection.execute("SELECT CASE WHEN EXISTS(SELECT 1 FROM desktop_reply_approvals a WHERE a.draft_id=j.draft_id) THEN 'approved' ELSE j.status END AS state,COUNT(*) "
            "FROM desktop_draft_jobs j JOIN emails e ON e.id=j.email_id WHERE e.status!='done' AND "
            "(j.status NOT IN ('ready','needs_info') OR EXISTS(SELECT 1 FROM draft_replies d WHERE d.id=j.draft_id AND d.status='pending')) GROUP BY state").fetchall())


def jobs_due(path, mailbox=None):
    with closing(open_database(path)) as c:
        if c.execute("SELECT 1 FROM desktop_workflow_leases WHERE name='email-drafting' AND expires>?", (time.time(),)).fetchone():
            return False
        condition = "e.status!='done' AND (j.status='queued' OR (j.status='failed' AND j.next_attempt<=?) OR (j.status='generating' AND j.lease_until<=?))"
        args = [time.time(), time.time()]
        if mailbox:
            condition += " AND e.provider='gmail' AND lower(e.source_mailbox)=lower(?)"
            args.append(mailbox)
        return c.execute('SELECT 1 FROM desktop_draft_jobs j JOIN emails e ON e.id=j.email_id WHERE ' + condition + ' LIMIT 1', args).fetchone() is not None
