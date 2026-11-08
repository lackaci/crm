-- Initial database structure for the Mini CRM.

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    external_id TEXT,
    provider TEXT NOT NULL DEFAULT 'mock',
    source_mailbox TEXT,
    remote_message_id TEXT,
    remote_thread_id TEXT,
    labels TEXT NOT NULL DEFAULT '[]',
    from_name TEXT NOT NULL,
    from_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    received_at TEXT,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'seen', 'done')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS faq_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'inactive')),
    approved_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS draft_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL REFERENCES emails(id),
    faq_id INTEGER REFERENCES faq_entries(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'escalated')),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    path TEXT NOT NULL,
    source_email_id INTEGER REFERENCES emails(id),
    source_attachment_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS desktop_mail_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    configuration TEXT NOT NULL,
    paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0,1))
);

CREATE TABLE IF NOT EXISTS desktop_outbox (
    id TEXT PRIMARY KEY,
    draft_id INTEGER NOT NULL UNIQUE REFERENCES draft_replies(id),
    email_id INTEGER NOT NULL REFERENCES emails(id),
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    rfc_message_id TEXT NOT NULL UNIQUE,
    thread_id TEXT,
    original_message_id TEXT,
    gmail_draft_id TEXT,
    gmail_message_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('creating','drafted','sending','sent','unknown','failed','cancelled')),
    operation TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(email_id, content_hash)
);

CREATE TABLE IF NOT EXISTS desktop_automation_settings (
    id INTEGER PRIMARY KEY CHECK (id=1),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    auto_draft INTEGER NOT NULL DEFAULT 1 CHECK (auto_draft IN (0,1)),
    poll_seconds INTEGER NOT NULL DEFAULT 120 CHECK (poll_seconds BETWEEN 30 AND 3600),
    revision INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO desktop_automation_settings(id) VALUES(1);

CREATE TABLE IF NOT EXISTS desktop_staff (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE TABLE IF NOT EXISTS desktop_assignment_rules (
    category TEXT PRIMARY KEY CHECK (category IN ('question','quote','complaint','billing','other')),
    staff_id INTEGER REFERENCES desktop_staff(id),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','high'))
);
INSERT OR IGNORE INTO desktop_assignment_rules(category,priority) VALUES
    ('question','normal'),('quote','normal'),('complaint','high'),('billing','high'),('other','normal');

CREATE TABLE IF NOT EXISTS desktop_email_triage (
    email_id INTEGER PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    staff_id INTEGER REFERENCES desktop_staff(id),
    outcome TEXT NOT NULL CHECK (outcome IN ('needs_assignment','needs_attention','review','existing_reply')),
    reason TEXT NOT NULL,
    draft_id INTEGER REFERENCES draft_replies(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS desktop_automation_events (
    id INTEGER PRIMARY KEY,
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    priority TEXT NOT NULL,
    staff_name TEXT,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    draft_id INTEGER REFERENCES draft_replies(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS desktop_document_settings (
    id INTEGER PRIMARY KEY CHECK(id=1),
    model TEXT NOT NULL DEFAULT 'qwen3-vl:2b-instruct',
    workbook_path TEXT,
    auto_extract INTEGER NOT NULL DEFAULT 0 CHECK(auto_extract IN (0,1)),
    provider TEXT NOT NULL DEFAULT 'openai',
    online_model TEXT NOT NULL DEFAULT 'gpt-5.4-mini-2026-03-17',
    online_endpoint TEXT NOT NULL DEFAULT 'https://api.openai.com/v1/responses',
    api_key_env TEXT NOT NULL DEFAULT 'OPENAI_API_KEY',
    embedding_model TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    online_replies INTEGER NOT NULL DEFAULT 0 CHECK(online_replies IN (0,1))
);
INSERT OR IGNORE INTO desktop_document_settings(id) VALUES(1);
CREATE TABLE IF NOT EXISTS desktop_document_extractions (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    source_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    model_digest TEXT NOT NULL,
    pipeline TEXT NOT NULL,
    data_json TEXT,
    status TEXT NOT NULL CHECK(status IN ('pending_review','reviewed','error','unsupported')),
    error_code TEXT,
    reviewed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_document_content ON desktop_document_extractions(source_hash,model_digest,pipeline);
