"""Source-linked document extraction records and explicit review state."""
from contextlib import closing
import json
from .database import open_database


def settings(database_path):
    with closing(open_database(database_path)) as connection:
        return dict(connection.execute("SELECT * FROM desktop_document_settings WHERE id=1").fetchone())


def save_settings(database_path, model, workbook_path, auto_extract=False, *, provider="local", online_model="gpt-5.4-mini-2026-03-17",
                  online_endpoint="https://api.openai.com/v1/responses", api_key_env="OPENAI_API_KEY",
                  embedding_model="text-embedding-3-small", online_replies=False):
    from app.providers.document_model import LocalDocumentExtractor, DocumentError
    from app.providers.online_model import OnlineSettings
    if provider == "local":
        LocalDocumentExtractor(model)
    else:
        OnlineSettings(online_model, provider, online_endpoint, api_key_env, embedding_model)
    if online_replies and provider == "local":
        raise DocumentError("Choose an online provider to enable online FAQ suggestions.")
    if not str(workbook_path).lower().endswith(".xlsx"):
        raise DocumentError("Choose an Excel .xlsx workbook path.")
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("UPDATE desktop_document_settings SET model=?,workbook_path=?,auto_extract=?,provider=?,online_model=?,online_endpoint=?,api_key_env=?,embedding_model=?,online_replies=? WHERE id=1",
            (model, str(workbook_path), bool(auto_extract), provider, online_model, online_endpoint, api_key_env, embedding_model, bool(online_replies)))
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('owner','document_settings_changed',?)",
            (f"provider={provider}; auto_extract={bool(auto_extract)}; online_replies={bool(online_replies)}",))


def sources(database_path):
    with closing(open_database(database_path)) as connection:
        return [dict(row) for row in connection.execute("""SELECT f.*,c.name AS customer_name,c.email AS customer_email,
            x.source_hash,x.model,x.model_digest,x.pipeline,x.data_json,x.status AS extraction_status,x.error_code,x.reviewed_at,x.updated_at
            FROM files f JOIN customers c ON c.id=f.customer_id LEFT JOIN desktop_document_extractions x ON x.file_id=f.id ORDER BY f.id""")]


def cached(database_path, content_hash, digest, pipeline):
    with closing(open_database(database_path)) as connection:
        row = connection.execute("SELECT data_json FROM desktop_document_extractions WHERE source_hash=? AND model_digest=? AND pipeline=? AND status IN ('pending_review','reviewed') LIMIT 1",
            (content_hash, digest, pipeline)).fetchone()
        return json.loads(row["data_json"]) if row else None


def save_extraction(database_path, source, content_hash, model, digest, pipeline, data=None, *, error=None, unsupported=False):
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute("SELECT customer_id,path FROM files WHERE id=?", (source["id"],)).fetchone()
        if not current or current["path"] != source["path"] or current["customer_id"] != source["customer_id"]:
            return False
        status = ("unsupported" if unsupported else "error") if error else "pending_review"
        connection.execute("""INSERT INTO desktop_document_extractions(file_id,source_hash,model,model_digest,pipeline,data_json,status,error_code)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(file_id) DO UPDATE SET source_hash=excluded.source_hash,model=excluded.model,
            model_digest=excluded.model_digest,pipeline=excluded.pipeline,data_json=excluded.data_json,status=excluded.status,
            error_code=excluded.error_code,reviewed_at=NULL,updated_at=CURRENT_TIMESTAMP""",
            (source["id"], content_hash, model, digest, pipeline, json.dumps(data, ensure_ascii=False) if data else None, status, error))
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('document-extraction',?,?)",
            ("document_extraction_failed" if error else "document_extracted", f"file_id={source['id']}; status={status}"))
        return True


def review(database_path, file_id, source_hash, data, original_json, digest, approve=False):
    from app.documents import validate_saved_data
    validate_saved_data(data)
    with closing(open_database(database_path)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute("""UPDATE desktop_document_extractions SET data_json=?,status=?,reviewed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
            updated_at=CURRENT_TIMESTAMP WHERE file_id=? AND source_hash=? AND data_json=? AND model_digest=? AND status IN ('pending_review','reviewed')""",
            (json.dumps(data, ensure_ascii=False), "reviewed" if approve else "pending_review", bool(approve), file_id, source_hash, original_json, digest))
        if cursor.rowcount != 1:
            raise ValueError("This extraction changed. Reload it before saving your review.")
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('owner',?,?)", ("document_reviewed" if approve else "document_corrected", f"file_id={file_id}"))
