# Mini CRM Progress

Updated: 2026-10-30

## What the application can do

Mini CRM is a Python 3.11+ desktop learning prototype built with PySide6 and
SQLite. It currently supports:

- Customer creation, editing, selection, and notes.
- Customer email and file views.
- A mock email provider for offline development.
- Gmail OAuth read-only import through a provider interface.
- Gmail sender allowlisting, pagination, message limits, and date filtering.
- Conversion of HTML-only Gmail messages into readable text.
- An unmatched-email list with explicit assignment to an existing customer.
- Sender cleanup for previously imported Gmail messages outside the allowlist.
- FAQ-based reply matching using only owner-approved answers.
- Safety escalation for unclear, risky, or low-confidence messages.
- Draft editing, approval, rejection, and audit logging.
- Local customer file storage and classification as invoice, contract, or other.
- Mock and cloud LLM provider interfaces, with an offline fake provider for tests.
- Privacy preview and masking support for provider calls.

The application does not send real customer email. Approval is required for
draft workflow actions, and Gmail is currently used only for read-only import.

## Gmail configuration

The local development setup uses a separate test mailbox in Dry-run mode. The
current behavior is configured through the private `.env` file:

- Only the configured SWTOR sender is allowed.
- Imports are limited to 100 messages.
- Only messages from the last 30 days are imported.
- Gmail pagination follows `nextPageToken` and deduplicates message IDs.
- OAuth credentials and tokens stay outside the repository and are not documented
  here.

The `.env` file is ignored by Git and must remain private. Never commit OAuth
client files, tokens, API keys, real customer data, or other secrets.

## User interface

The UI has separate navigation areas for:

- Customers and customer details.
- Customer inbox and a large scrollable message preview.
- Unmatched Gmail messages and manual customer assignment.
- Customer files.
- Approval Queue.
- Settings and provider/privacy information.

The layout was reorganized into tabs and compact message rows so the full email
body remains readable on a smaller screen.

## Safety behavior

The reply pipeline may only rewrite an answer from an approved FAQ. It must
escalate instead of drafting when there is no sufficiently confident FAQ match,
or when the message involves refunds, legal threats, anger or complaints,
money amounts, or anything unclear.

The SWTOR promotional message correctly escalates because it does not match an
approved FAQ with sufficient confidence. The sample FAQ data is in
[sample_data/faqs.json](./sample_data/faqs.json).

Important actions such as imports, assignments, cleanup, provider activity, and
approval actions are recorded in the audit log.

## Verification completed

Focused tests have passed for:

- Configuration and Gmail limits/date filtering.
- Gmail normalization, HTML conversion, pagination, sender filtering, and OAuth.
- Email persistence, unmatched-email cleanup, and explicit assignment.
- Customer and inbox UI behavior.
- FAQ matching, guardrails, drafts, approval, privacy, and provider behavior.

The full offline Mock-mode suite previously reached 92 passing tests. A full
suite run while the local `.env` selected Gmail Dry-run had one
environment-dependent settings-test failure because that test expects Mock mode.
The intended full-suite command is:

```powershell
$env:MINI_CRM_MODE='Mock'
$env:MINI_CRM_PROVIDER='mock'
pytest -q
```

## Main implementation areas

- `app/config.py` - application and Gmail configuration.
- `app/providers/gmail.py` - read-only Gmail integration and normalization.
- `app/db/emails.py` - email persistence, cleanup, and assignment.
- `app/reply/` - FAQ matching, guardrails, drafting, and approval.
- `app/ui/inbox.py` - inbox, unmatched messages, and message preview.
- `app/ui/customers.py` - customer management and related tabs.
- `app/ui/main_window.py` - top-level navigation.
- `tests/` - unit and UI-focused coverage.
- `LEARNING_NOTES.md` - beginner walkthrough and usage notes.

## Recommended next steps

1. Add an FAQ editor so the owner can add, edit, approve, and deactivate FAQs
   from the UI, with audit-log entries for each change.
2. Add Gmail settings controls for the sender allowlist, message limit, and date
   filter without exposing secrets.
3. Add inbox search and filters for sender, subject, seen state, and assignment.
4. Improve cleanup confirmation by showing the number of messages that will be
   removed and expand related draft/file deletion tests.
5. Run the full suite in controlled Mock mode and perform a separate dry-run
   smoke test with only the dedicated test mailbox.
6. Keep real sending disabled until a later milestone has explicit owner
   approval and the required safety reviews.

## Git and privacy status

The implementation changes are ready to be grouped into a project-progress
commit. The private `.env` file is ignored and must not be added. Before
committing, review the staged file list and confirm that no credentials, tokens,
databases, or real customer data are included.
