"""Desktop triage persistence. Suggestions and their history commit together."""
from contextlib import closing
from types import SimpleNamespace

from .database import open_database

CATEGORIES = ("question", "quote", "complaint", "billing", "other")


def _audit(connection, action, details):
    connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('automation',?,?)", (action, details))


def settings(database_path):
    with closing(open_database(database_path)) as connection:
        return dict(connection.execute("SELECT * FROM desktop_automation_settings WHERE id=1").fetchone())


def save_settings(database_path, *, enabled, auto_draft=True, poll_seconds=120):
    if not isinstance(poll_seconds, int) or not 30 <= poll_seconds <= 3600:
        raise ValueError("Check interval must be between 30 and 3600 seconds.")
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("UPDATE desktop_automation_settings SET enabled=?,auto_draft=?,poll_seconds=?,revision=revision+1 WHERE id=1",
            (bool(enabled), bool(auto_draft), poll_seconds))
        _audit(connection, "automation_settings_changed", f"enabled={bool(enabled)}; auto_draft={bool(auto_draft)}; poll_seconds={poll_seconds}")


def list_staff(database_path):
    with closing(open_database(database_path)) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM desktop_staff WHERE active=1 ORDER BY name")]


def add_staff(database_path, name):
    name = name.strip()
    if not name or len(name) > 100 or any(character in name for character in "\r\n\x00"):
        raise ValueError("Enter a staff name between 1 and 100 characters.")
    with closing(open_database(database_path)) as connection, connection:
        if connection.execute("SELECT 1 FROM desktop_staff WHERE name=? COLLATE NOCASE", (name,)).fetchone():
            raise ValueError("That staff member already exists.")
        cursor = connection.execute("INSERT INTO desktop_staff(name) VALUES(?)", (name,))
        _audit(connection, "automation_staff_added", f"staff_id={cursor.lastrowid}")
        return cursor.lastrowid


def list_rules(database_path):
    with closing(open_database(database_path)) as connection:
        return {row["category"]: dict(row) for row in connection.execute("SELECT * FROM desktop_assignment_rules")}


def save_rules(database_path, rules):
    if set(rules) != set(CATEGORIES):
        raise ValueError("Choose an assignment and priority for each category.")
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        for category, rule in rules.items():
            staff_id, priority = rule["staff_id"], rule["priority"]
            if priority not in {"normal", "high"}:
                raise ValueError("Choose normal or high priority.")
            if staff_id is not None and not connection.execute("SELECT 1 FROM desktop_staff WHERE id=? AND active=1", (staff_id,)).fetchone():
                raise ValueError("Choose an active staff member.")
            connection.execute("UPDATE desktop_assignment_rules SET staff_id=?,priority=? WHERE category=?", (staff_id, priority, category))
        connection.execute("UPDATE desktop_automation_settings SET revision=revision+1 WHERE id=1")
        _audit(connection, "automation_rules_changed", "category_rules_updated")


def candidates(database_path, limit=50, mailbox=None):
    with closing(open_database(database_path)) as connection:
        rows = connection.execute("""SELECT e.* FROM emails e LEFT JOIN desktop_email_triage t ON t.email_id=e.id
            WHERE e.status!='done' AND (? IS NULL OR (e.provider='gmail' AND lower(e.source_mailbox)=lower(?)))
            AND (t.email_id IS NULL OR (t.outcome='needs_assignment' AND (e.customer_id IS NOT NULL OR
                (SELECT count(*) FROM customers c WHERE lower(c.email)=lower(e.from_email))=1)))
            ORDER BY e.id LIMIT ?""", (mailbox, mailbox, limit)).fetchall()
        return [SimpleNamespace(**dict(row)) for row in rows]


def matching_customer(database_path, email):
    if email.customer_id is not None:
        return email.customer_id
    with closing(open_database(database_path)) as connection:
        matches = connection.execute("SELECT id FROM customers WHERE lower(email)=lower(?)", (email.from_email,)).fetchall()
        return matches[0]["id"] if len(matches) == 1 else None


def has_reply(database_path, email_id):
    with closing(open_database(database_path)) as connection:
        return bool(connection.execute("SELECT 1 FROM draft_replies WHERE email_id=? LIMIT 1", (email_id,)).fetchone())


def record_result(database_path, email, customer_id, category, priority, reason, result, revision):
    """Recheck stale work, pause, FAQ approval and duplicates before one commit."""
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        config = connection.execute("SELECT * FROM desktop_automation_settings WHERE id=1").fetchone()
        paused = connection.execute("SELECT paused FROM desktop_mail_settings WHERE id=1").fetchone()
        if not config["enabled"] or config["revision"] != revision or (paused and paused["paused"]):
            return None
        current = connection.execute("SELECT * FROM emails WHERE id=?", (email.id,)).fetchone()
        fields = ("customer_id", "from_email", "from_name", "subject", "body", "status")
        if not current or current["status"] == "done" or any(current[field] != getattr(email, field) for field in fields):
            return None
        previous = connection.execute("SELECT outcome FROM desktop_email_triage WHERE email_id=?", (email.id,)).fetchone()
        if previous and (previous["outcome"] != "needs_assignment" or customer_id is None):
            return None
        if current["customer_id"] is None:
            matches = connection.execute("SELECT id FROM customers WHERE lower(email)=lower(?)", (current["from_email"],)).fetchall()
            verified_customer = matches[0]["id"] if len(matches) == 1 else None
            if verified_customer != customer_id:
                return None
            if customer_id is not None:
                connection.execute("UPDATE emails SET customer_id=? WHERE id=?", (customer_id, email.id))
        rule = connection.execute("SELECT r.*,s.name FROM desktop_assignment_rules r LEFT JOIN desktop_staff s ON s.id=r.staff_id AND s.active=1 WHERE r.category=?", (category,)).fetchone()
        staff_id = rule["staff_id"] if rule["name"] else None
        priority = "high" if priority == "high" or rule["priority"] == "high" else "normal"
        existing = connection.execute("SELECT id FROM draft_replies WHERE email_id=? ORDER BY id DESC LIMIT 1", (email.id,)).fetchone()
        draft_id = None
        outcome = "needs_attention"
        if customer_id is None:
            outcome, reason = "needs_assignment", "Sender has no unique matching customer. Link the message to a customer. " + reason
        elif existing:
            outcome, draft_id, reason = "existing_reply", existing["id"], "A reply already exists; automation did not replace it. " + reason
        elif result is not None and result.status == "pending" and config["auto_draft"]:
            faq = connection.execute("SELECT status,question,answer FROM faq_entries WHERE id=?", (result.faq.id,)).fetchone()
            if faq and faq["status"] == "approved" and faq["question"] == result.faq.question and faq["answer"] == result.faq.answer:
                cursor = connection.execute("INSERT INTO draft_replies(email_id,faq_id,body) VALUES(?,?,?)", (email.id, result.faq.id, result.body))
                draft_id, outcome = cursor.lastrowid, "review"
                reason += f" Approved answer #{result.faq.id} matched ({result.match.score:.2f}); local reply queued for review."
                _audit(connection, "draft_created", f"draft_id={draft_id}; email_id={email.id}")
            else:
                reason += " The saved answer changed during processing; prepare a reply manually."
        elif result is not None:
            reason += " " + (result.reason or "No safe suggestion was produced.")
        elif not config["auto_draft"]:
            reason += " Automatic reply suggestions are disabled."
        connection.execute("""INSERT INTO desktop_email_triage(email_id,category,priority,staff_id,outcome,reason,draft_id)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(email_id) DO UPDATE SET category=excluded.category,priority=excluded.priority,
            staff_id=excluded.staff_id,outcome=excluded.outcome,reason=excluded.reason,draft_id=excluded.draft_id,updated_at=CURRENT_TIMESTAMP""",
            (email.id, category, priority, staff_id, outcome, reason, draft_id))
        connection.execute("INSERT INTO desktop_automation_events(email_id,category,priority,staff_name,outcome,reason,draft_id) VALUES(?,?,?,?,?,?,?)",
            (email.id, category, priority, rule["name"], outcome, reason, draft_id))
        _audit(connection, "email_triaged", f"email_id={email.id}; category={category}; outcome={outcome}; staff_id={staff_id}")
        return {"email_id": email.id, "outcome": outcome, "draft_id": draft_id}


def inbox_triage(database_path):
    with closing(open_database(database_path)) as connection:
        return {row["email_id"]: dict(row) for row in connection.execute("SELECT t.*,s.name AS staff_name FROM desktop_email_triage t LEFT JOIN desktop_staff s ON s.id=t.staff_id")}


def history(database_path, limit=200):
    with closing(open_database(database_path)) as connection:
        return [dict(row) for row in connection.execute("""SELECT a.*,e.subject,e.from_email,e.customer_id,e.status AS email_status,d.status AS draft_status
            FROM desktop_automation_events a JOIN emails e ON e.id=a.email_id
            LEFT JOIN draft_replies d ON d.id=a.draft_id ORDER BY a.id DESC LIMIT ?""", (limit,))]
