"""Five-minute desktop workflow: fake Gmail and fake HTTP only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, RunMode
from app.db.database import initialize_database, open_database
from app.db.drafts import create_draft_reply, get_draft, list_drafts
from app.db.emails import get_email, import_emails
from app.db.faqs import create_faq
from app.db import email_workflow as store
from app.email_sync import synchronize_email
from app.providers.gmail import GmailProvider
from app.providers.online_model import OnlineSetupError
from app.reply import email_assistant as assistant, review, delivery
from app.reply.approval import edit_draft
from app.ui.main_window import MainWindow
from tests.test_desktop_delivery import FakeGmail


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / 'email.sqlite'
    initialize_database(path)
    service = FakeGmail()
    class Mail:
        def fetch_new_emails(self):
            return [{'provider': 'gmail', 'remote_message_id': 'm1', 'remote_thread_id': 't1', 'source_mailbox': service.account,
                'from_name': 'Customer', 'from_email': 'customer@example.invalid', 'subject': 'Opening hours',
                'body': 'What are your opening hours? Email customer@example.invalid'}]
    import_emails(path, Mail())
    create_faq(path, 'What are your opening hours?', 'We are open weekdays from 9 to 5.', approved=True)
    config = AppConfig(RunMode.LIVE, 'gmail', test_mailbox=service.account, gmail_write_enabled=True,
                       enable_sending=True, send_allowlist=('customer@example.invalid',))
    provider = GmailProvider(service=service, expected_mailbox=service.account, write_enabled=True)
    return SimpleNamespace(path=path, config=config, provider=provider, service=service, email_id=1)


class Client:
    def __init__(self, callback=None):
        self.calls, self.callback = [], callback
    def complete_json(self, instructions, content, schema):
        self.calls.append((instructions, content, schema))
        if self.callback:
            return self.callback()
        return {'reply': 'Our opening hours are weekdays from 9 to 5. Thank you for asking!', 'source_ids': [1], 'needs_info': False}


def configured(workspace):
    store.save_ai(workspace.path, store.FREE_MODEL, 'AI_GATEWAY_API_KEY')


def generated(workspace, client=None):
    configured(workspace)
    store.enqueue(workspace.path)
    return assistant.run_jobs(workspace.path, client=client or Client())


def test_migration_keeps_existing_data_and_disabled_defaults(workspace):
    initialize_database(workspace.path)
    assert get_email(workspace.path, 1).subject == 'Opening hours'
    assert store.settings(workspace.path)['enabled'] == 0
    assert store.settings(workspace.path)['interval_seconds'] == 300


def test_model_upgrade_preserves_workspace_and_requires_new_test(workspace):
    draft_id = assistant.manual_reply(workspace.path, 1)
    store.update(workspace.path, model='reflection/beam-free', configured=1, enabled=1,
                 tested_profile='old-profile', key_env='EXISTING_GATEWAY_KEY')
    initialize_database(workspace.path)
    saved = store.settings(workspace.path)
    assert saved['model'] == 'nvidia/nemotron-3.5-lightning-free'
    assert saved['configured'] == saved['enabled'] == saved['ai_paused'] == 1
    assert saved['tested_profile'] is None and saved['key_env'] == 'EXISTING_GATEWAY_KEY'
    assert 'trial' in saved['ai_error']
    assert get_draft(workspace.path, draft_id).status == 'pending'
    assistant.test_connection(workspace.path, Client())
    initialize_database(workspace.path)
    assert store.settings(workspace.path)['ai_paused'] == 0
    assert store.settings(workspace.path)['tested_profile'] == store.profile(saved)


def test_model_upgrade_preserves_custom_model_choice(workspace):
    store.update(workspace.path, model='custom/chosen-model', free_only=0)
    initialize_database(workspace.path)
    assert store.settings(workspace.path)['model'] == 'custom/chosen-model'


def test_poll_300_seconds_sleep_resume_and_multiple_instances(workspace):
    store.set_running(workspace.path, True, now=1000)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: store.claim_poll(workspace.path, 1000), range(2)))
    assert sum(claimed) == 1
    assert not store.claim_poll(workspace.path, 1299)
    assert store.claim_poll(workspace.path, 1300)
    assert store.claim_poll(workspace.path, 10000)
    assert not store.claim_poll(workspace.path, 10000)
    assert store.settings(workspace.path)['next_check'] == 10300
    store.set_running(workspace.path, False)
    assert not store.claim_poll(workspace.path, 20000)


def test_database_lease_owner_and_expiry(workspace):
    first = store.acquire(workspace.path, 'import', now=100, ttl=50)
    assert first and store.acquire(workspace.path, 'import', now=110) is None
    second = store.acquire(workspace.path, 'import', now=151)
    assert second and second != first
    store.release(workspace.path, 'import', first)
    assert store.acquire(workspace.path, 'import', now=152) is None


def test_queue_survives_restart_and_recovers_expired_generation(workspace):
    configured(workspace)
    store.enqueue(workspace.path)
    store.enqueue(workspace.path)
    with open_database(workspace.path) as c:
        assert c.execute('SELECT COUNT(*) FROM desktop_draft_jobs').fetchone()[0] == 1
        c.execute("UPDATE desktop_draft_jobs SET status='generating',owner='dead-process',lease_until=0")
    initialize_database(workspace.path)
    result = assistant.run_jobs(workspace.path, client=Client())
    assert result.drafts == 1 and result.failed == 0
    assert get_draft(workspace.path, result.draft_id).body.startswith('Our opening hours')
    store.enqueue(workspace.path)
    assert assistant.run_jobs(workspace.path, client=Client()).drafts == 0
    assert len(list_drafts(workspace.path)) == 1


def test_paid_model_refused_in_free_only_mode(workspace):
    with pytest.raises(ValueError, match='Free-only'):
        store.save_ai(workspace.path, 'reflection/beam', 'AI_GATEWAY_API_KEY')
    assert store.settings(workspace.path)['configured'] == 0


def test_unavailable_model_pauses_ai_but_imports_continue(workspace, monkeypatch):
    configured(workspace)
    def unavailable():
        raise OnlineSetupError('Online rate limit or quota reached. Check your provider account before retrying.')
    client = Client(unavailable)
    store.enqueue(workspace.path)
    result = assistant.run_jobs(workspace.path, client=client)
    assert result.failed == 1 and store.settings(workspace.path)['ai_paused']
    class Incoming:
        expected_mailbox = workspace.service.account
        def fetch_new_emails(self):
            return [{'provider': 'gmail', 'remote_message_id': 'm2', 'source_mailbox': self.expected_mailbox,
                     'from_name': 'Second', 'from_email': 'second@example.invalid', 'subject': 'Hours?', 'body': 'Opening hours?'}]
    imported = synchronize_email(workspace.path, Incoming())
    assert imported.imported == 1 and not imported.automation_error
    assert imported.automation.error
    assert workspace.service.send_calls == workspace.service.create_calls == 0


def test_ai_context_masks_supported_patterns_and_uses_approved_facts(workspace):
    client = Client()
    result = generated(workspace, client)
    assert result.drafts == 1
    instructions, content, schema = client.calls[0]
    assert 'customer@example.invalid' not in content
    assert 'PERSONAL_DATA_' in content and 'approved_facts' in content
    assert 'untrusted' in instructions and schema == assistant.SCHEMA
    assert workspace.service.send_calls == 0


def test_free_http_payload_uses_only_text_model_no_embedding_or_fallback(workspace, monkeypatch):
    import app.providers.online_model as online
    configured(workspace)
    calls = []
    real_client = httpx.Client
    def route(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(Client().complete_json('', '', {}))}}]})
    def http_client(**options):
        options['transport'].close()
        options['transport'] = httpx.MockTransport(route)
        return real_client(**options)
    monkeypatch.setattr(online, 'api_key', lambda settings: 'fake-unit-key')
    monkeypatch.setattr(httpx, 'Client', http_client)
    store.enqueue(workspace.path)
    assert assistant.run_jobs(workspace.path).drafts == 1
    assert len(calls) == 1 and calls[0].url.path == '/v1/chat/completions'
    payload = json.loads(calls[0].content)
    assert payload['model'] == 'nvidia/nemotron-3.5-lightning-free'
    assert payload['max_completion_tokens'] == 1200
    assert 'tools' not in payload and 'fallbacks' not in payload
    assert 'fake-unit-key' not in workspace.path.read_bytes().decode('utf-8', errors='ignore')


def test_free_http_quota_failure_has_no_automatic_fallback(workspace, monkeypatch):
    import app.providers.online_model as online
    configured(workspace)
    calls = []
    real_client = httpx.Client
    def http_client(**options):
        options['transport'].close()
        def route(request):
            calls.append(request)
            return httpx.Response(429, text='SECRET RESPONSE MUST NOT APPEAR')
        options['transport'] = httpx.MockTransport(route)
        return real_client(**options)
    monkeypatch.setattr(online, 'api_key', lambda settings: 'fake-unit-key')
    monkeypatch.setattr(httpx, 'Client', http_client)
    store.enqueue(workspace.path)
    result = assistant.run_jobs(workspace.path)
    assert result.failed == 1 and len(calls) == 1
    assert 'SECRET' not in result.error and store.settings(workspace.path)['ai_paused']


@pytest.mark.parametrize('subject', ['Refund complaint', 'Unrelated question about custom equipment'])
def test_risky_or_unknown_messages_offer_editable_clarification(workspace, subject):
    with open_database(workspace.path) as c:
        c.execute('UPDATE emails SET subject=?,body=? WHERE id=1', (subject, subject))
    client = Client()
    result = generated(workspace, client)
    assert result.attention == 1 and not client.calls
    edit_draft(workspace.path, result.draft_id, 'A human-written reply')
    assert get_draft(workspace.path, result.draft_id).body == 'A human-written reply'


def test_unsupported_numeric_facts_become_clarification(workspace):
    result = generated(workspace, Client(lambda: {'reply': 'We close at 77.', 'source_ids': [1], 'needs_info': False}))
    assert result.attention == 1
    assert '77' not in get_draft(workspace.path, result.draft_id).body


def test_pending_edited_reply_is_never_overwritten(workspace):
    result = generated(workspace)
    edit_draft(workspace.path, result.draft_id, 'My careful reply')
    store.enqueue(workspace.path, force=True)
    client = Client()
    assistant.run_jobs(workspace.path, client=client)
    assert not client.calls and get_draft(workspace.path, result.draft_id).body == 'My careful reply'


def test_explicit_regeneration_revokes_approval_and_preserves_concurrent_edit(workspace):
    result = generated(workspace)
    review.approve(workspace.path, result.draft_id, workspace.config, get_draft(workspace.path, result.draft_id).body)
    identifier, previous = assistant.regenerate(workspace.path, result.draft_id)
    with pytest.raises(ValueError, match='Approve'):
        review.require_approved(workspace.path, result.draft_id, workspace.config)
    def concurrent_edit():
        edit_draft(workspace.path, result.draft_id, 'Human edit made during generation')
        return Client().complete_json('', '', {})
    failed = assistant.run_jobs(workspace.path, client=Client(concurrent_edit), email_id=identifier, regenerate_body=previous)
    assert failed.failed == 1
    assert get_draft(workspace.path, result.draft_id).body == 'Human edit made during generation'


def test_send_blocked_before_approval_then_one_click_creates_and_sends(workspace):
    identifier = assistant.manual_reply(workspace.path, 1)
    with pytest.raises(ValueError, match='Approve'):
        review.send_approved(workspace.path, identifier, 'unapproved', workspace.config, workspace.provider)
    assert workspace.service.create_calls == 0
    preview = review.approve(workspace.path, identifier, workspace.config, get_draft(workspace.path, identifier).body)
    assert get_email(workspace.path, 1).status != 'done'
    result = review.send_approved(workspace.path, identifier, preview['content_hash'], workspace.config, workspace.provider)
    assert result['status'] == 'sent'
    assert workspace.service.create_calls == workspace.service.send_calls == 1
    with pytest.raises(ValueError):
        review.send_approved(workspace.path, identifier, preview['content_hash'], workspace.config, workspace.provider)
    assert workspace.service.send_calls == 1


@pytest.mark.parametrize('mutation', ['body', 'subject', 'from_email', 'source_mailbox', 'remote_thread_id'])
def test_exact_approval_invalidated_even_when_change_is_reverted(workspace, mutation):
    identifier = assistant.manual_reply(workspace.path, 1)
    preview = review.approve(workspace.path, identifier, workspace.config, get_draft(workspace.path, identifier).body)
    table, field_id = ('draft_replies', identifier) if mutation == 'body' else ('emails', 1)
    with open_database(workspace.path) as c:
        original = c.execute(f'SELECT {mutation} FROM {table} WHERE id=?', (field_id,)).fetchone()[0]
        c.execute(f'UPDATE {table} SET {mutation}=? WHERE id=?', ('changed', field_id))
        c.execute(f'UPDATE {table} SET {mutation}=? WHERE id=?', (original, field_id))
    with pytest.raises(ValueError, match='Approve'):
        review.require_approved(workspace.path, identifier, workspace.config, preview['content_hash'])


def test_uncertain_send_stays_locked_after_new_approval_and_restart(workspace):
    identifier = assistant.manual_reply(workspace.path, 1)
    body = get_draft(workspace.path, identifier).body
    preview = review.approve(workspace.path, identifier, workspace.config, body)
    workspace.service.uncertain_send = True
    with pytest.raises(Exception, match='uncertain'):
        review.send_approved(workspace.path, identifier, preview['content_hash'], workspace.config, workspace.provider)
    assert delivery.outbox_for_draft(workspace.path, identifier)['status'] == 'unknown'
    initialize_database(workspace.path)
    review.approve(workspace.path, identifier, workspace.config, body)
    with pytest.raises(ValueError, match='delivery attempt'):
        review.send_approved(workspace.path, identifier, preview['content_hash'], workspace.config, workspace.provider)
    assert workspace.service.send_calls == 1


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def wait_task(qapp, getter):
    deadline = time.monotonic() + 10
    while getter() is not None and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert getter() is None


def test_gui_setup_approve_edit_reapprove_and_single_send(workspace, qapp, monkeypatch):
    identifier = assistant.manual_reply(workspace.path, 1)
    window = MainWindow(workspace.path, config=workspace.config, email_provider=workspace.provider)
    window.show()
    window.navigate(5)
    assert window.email_setup.model_input.text() == store.FREE_MODEL
    assert window.settings_widget.gmail_widget.isVisible()
    window.navigate(2)
    widget = window.approval_widget
    widget.select_draft(identifier)
    assert not widget.send_reply_button.isEnabled()
    QTest.mouseClick(widget.review_approve_button, Qt.MouseButton.LeftButton)
    assert widget.send_reply_button.isEnabled()
    widget.body_editor.insertPlainText('Edited ')
    assert not widget.send_reply_button.isEnabled()
    widget.body_editor.undo()
    assert not widget.send_reply_button.isEnabled()
    QTest.mouseClick(widget.review_approve_button, Qt.MouseButton.LeftButton)
    assert widget.send_reply_button.isEnabled()
    monkeypatch.setattr(widget, '_confirmed', lambda *args: pytest.fail('No second confirmation is needed'))
    QTest.mouseClick(widget.send_reply_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(widget.send_reply_button, Qt.MouseButton.LeftButton)
    wait_task(qapp, lambda: widget.delivery_task)
    assert workspace.service.send_calls == workspace.service.create_calls == 1
    assert 'sent once' in widget.status_label.text()
    window.close()


def test_gui_start_checks_immediately_pause_and_due_check(workspace, qapp, monkeypatch):
    configured(workspace)
    assistant.test_connection(workspace.path, Client())
    window = MainWindow(workspace.path, config=workspace.config, email_provider=workspace.provider)
    checks = []
    monkeypatch.setattr(window.inbox_widget, 'start_import', lambda automatic=False: checks.append(automatic))
    window.navigate(5)
    window.email_setup.start_button.click()
    assert checks == [True]
    assert window.automation_timer.interval() == 1000  # lightweight due checks, not Gmail calls
    window._scheduled_import()
    assert checks == [True]
    store.update(workspace.path, next_check=time.time()-1)
    window._scheduled_import()
    assert checks == [True, True]
    window.email_setup.pause_button.click()
    window._scheduled_import()
    assert checks == [True, True] and not store.settings(workspace.path)['enabled']
    window.close()


@pytest.mark.parametrize('scheduled', [False, True])
def test_gui_import_queues_then_separate_worker_prepares_reply(workspace, qapp, monkeypatch, scheduled):
    from app.gmail_import import ImportResult
    configured(workspace)
    monkeypatch.setattr(assistant, 'EmailClient', lambda settings: Client())
    monkeypatch.setattr(workspace.provider, 'import_incrementally', lambda *args, **kwargs: ImportResult(existing=1))
    window = MainWindow(workspace.path, config=workspace.config, email_provider=workspace.provider)
    if scheduled:
        store.set_running(workspace.path, True)
        store.claim_poll(workspace.path)
    window.inbox_widget.start_import(automatic=scheduled)
    wait_task(qapp, lambda: window.inbox_widget.import_task)
    window._workflow_heartbeat()
    wait_task(qapp, lambda: window.email_draft_task)
    assert len(list_drafts(workspace.path)) == 1
    assert store.job_counts(workspace.path) == {'ready': 1}
    window.email_setup.refresh()
    assert 'Ready for review 1' in window.email_setup.status.text()
    assert workspace.service.send_calls == workspace.service.create_calls == 0
    assert window.pages.currentWidget() is window.inbox_widget  # no focus stealing
    window.close()


def test_slow_ai_does_not_block_due_gmail_check(workspace, qapp, monkeypatch):
    configured(workspace)
    window = MainWindow(workspace.path, config=workspace.config, email_provider=workspace.provider)
    store.set_running(workspace.path, True)
    # An AI worker is independent from the importer, including at the due boundary.
    window.email_draft_task = object()
    checked = []
    monkeypatch.setattr(window.inbox_widget, 'start_import', lambda automatic=False: checked.append(automatic))
    window._scheduled_import()
    assert checked == [True]
    window.email_draft_task = None
    window.close()


def test_regeneration_replaces_only_requested_email(workspace):
    first = generated(workspace).draft_id
    class Second:
        def fetch_new_emails(self):
            return [{'id': 'second', 'from_name': 'Second', 'from_email': 'second@example.invalid',
                     'subject': 'Opening hours', 'body': 'Opening hours?'}]
    import_emails(workspace.path, Second())
    store.enqueue(workspace.path, email_id=2)
    second = assistant.run_jobs(workspace.path, client=Client(), email_id=2).draft_id
    identifier, previous = assistant.regenerate(workspace.path, second)
    result = assistant.run_jobs(workspace.path, client=Client(lambda: {'reply': 'We open weekdays from 9 to 5.', 'source_ids': [1], 'needs_info': False}),
                                email_id=identifier, regenerate_body=previous)
    assert result.draft_id == second and get_draft(workspace.path, first).body != get_draft(workspace.path, second).body
