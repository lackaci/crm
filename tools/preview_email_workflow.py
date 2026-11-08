"""Render only fictional isolated data; never connect to Gmail or online AI."""
import gc
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from app.config import AppConfig, RunMode
from app.db.database import open_database
from app.db import email_workflow as store
from app.demo import create_demo_workspace
from app.providers.gmail import GmailProvider
from app.reply.email_assistant import run_jobs
from app.reply.review import snapshot
from app.ui.main_window import MainWindow


def main():
    application = QApplication.instance() or QApplication([])
    font = Path('C:/Windows/Fonts/segoeui.ttf')
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    output = Path(__file__).resolve().parents[1] / 'docs' / 'previews'
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='crm-email-preview-') as temporary:
        path, storage = create_demo_workspace(Path(temporary))
        with closing(open_database(path)) as c, c:
            c.execute("UPDATE emails SET provider='gmail',source_mailbox='fictional@example.invalid',remote_message_id='sample-'||id,remote_thread_id='thread-'||id")
        store.save_ai(path, store.FREE_MODEL, 'AI_GATEWAY_API_KEY')
        store.enqueue(path, email_id=1)
        result = run_jobs(path, offline=True, email_id=1)
        config = AppConfig(RunMode.LIVE, 'gmail', test_mailbox='fictional@example.invalid', gmail_write_enabled=True,
                           enable_sending=True, send_allowlist=('anna@example.com',))
        # Inert service: previewing cannot sign in or send even accidentally.
        window = MainWindow(path, storage_root=storage, config=config,
            email_provider=GmailProvider(service=object(), expected_mailbox=config.test_mailbox, write_enabled=True))
        window.mode_label.setText('Fictional preview — offline')
        window.resize(1100, 800)
        window.show()
        window.navigate(5)
        application.processEvents()
        assert window.grab().save(str(output / 'email-setup.png'))
        window.settings_widget.scroll.verticalScrollBar().setValue(window.settings_widget.scroll.verticalScrollBar().maximum())
        application.processEvents()
        assert window.grab().save(str(output / 'email-setup-gmail.png'))
        window.navigate(2)
        window.approval_widget.select_draft(result.draft_id)
        window.approval_widget.approve_reply()  # Local approval only.
        application.processEvents()
        assert window.grab().save(str(output / 'email-approval.png'))
        window.resize(900, 620)
        application.processEvents()
        assert window.grab().save(str(output / 'email-approval-small.png'))
        window.navigate(0)
        window.resize(1100, 800)
        application.processEvents()
        assert window.grab().save(str(output / 'email-inbox.png'))
        # Simulated confirmed outcome in this disposable database only.
        sent = snapshot(path, result.draft_id, config)
        with closing(open_database(path)) as c, c:
            c.execute("INSERT INTO desktop_outbox(id,draft_id,email_id,sender,recipient,subject,body,content_hash,rfc_message_id,status,operation) VALUES(?,?,?,?,?,?,?,?,?,'sent','send')",
                ('fictional-sent', result.draft_id, sent['email_id'], sent['sender'], sent['recipient'], sent['subject'], sent['body'], sent['content_hash'], '<fictional@example.invalid>'))
            c.execute("UPDATE draft_replies SET status='approved' WHERE id=?", (result.draft_id,))
            c.execute("UPDATE emails SET status='done' WHERE id=?", (sent['email_id'],))
        window.approval_widget.refresh()
        window.navigate(7)
        application.processEvents()
        assert window.grab().save(str(output / 'email-replied.png'))
        window.resize(900, 620)
        application.processEvents()
        assert window.grab().save(str(output / 'email-replied-small.png'))
        window.resize(1100, 800)
        window.navigate(0)
        application.processEvents()
        assert window.grab().save(str(output / 'email-inbox-after-send.png'))
        window.close()
        window.deleteLater()
        application.processEvents()
        del window
        gc.collect()
    print('Saved isolated email workflow previews; no network calls made.')


if __name__ == '__main__':
    main()
