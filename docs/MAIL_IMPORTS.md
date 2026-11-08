# Durable read-only Gmail imports

This milestone connects Gmail to the server database through a persistent job
queue. It does not use the desktop `.env`, open a login window, download
attachments, call an LLM, create remote Gmail drafts, or send email.

## Operator setup

1. Back up the server database, then run `python -m alembic upgrade head`.
   Migration `0002` adds the queue/mailbox tables and starts integrations paused.
   Existing server contacts and conversations remain in place.
2. Configure a dedicated test mailbox and owner-approved sender allowlist.
   Obtain authorized-user credentials through your own Google OAuth setup,
   requesting only `https://www.googleapis.com/auth/gmail.readonly`. Keep the
   authorized-user JSON outside this repository. A client-secret-only JSON is
   not sufficient: the file must contain `client_id`, `client_secret`,
   `refresh_token`, and `scopes` declaring exactly the read-only scope.
3. Generate a Fernet key using your secret-management tooling. Supply it as
   `BACKEND_MAILBOX_KEY` to the provisioning command and worker process. Keep
   the key separate from database backups and back it up securely. The API
   does not need it. There is no default key or plaintext fallback.
4. As an authenticated backend admin, `POST /api/v1/mailboxes` with, for example:

   ```json
   {
     "address": "dedicated-test@example.com",
     "allowed_senders": ["approved-sender@example.com"],
     "batch_size": 25,
     "lookback_days": 30,
     "sync_interval_seconds": 300,
     "active": true
   }
   ```

5. While integrations are paused, provision credentials locally:

   ```powershell
   .venv/Scripts/python.exe -m backend.manage connect-gmail --subject "admin-oidc-subject" --mailbox-id "mailbox-uuid" --credential-file "C:/Private/authorized-user.json"
   ```

   This command encrypts credentials in PostgreSQL and records an audit event.
   It makes no Gmail request, prints no credentials, and refuses files inside
   the checkout. Treat the original file as a secret; this command neither
   creates nor deletes it. Use operator database credentials with access to
   `mailbox_credentials`; the restricted API role cannot provision tokens.

6. Start the worker with the same server database settings and key:

   ```powershell
   .venv/Scripts/python.exe -m backend.worker
   ```

   `--once` performs at most one page/job attempt, useful for manual smoke tests.
   The worker fails startup if the encryption key is missing or invalid.
   For Compose, set `BACKEND_MAILBOX_KEY` and use
   `docker compose --env-file .backend.env -f compose.backend.yaml --profile imports up --build -d`.
   The key is passed only to the worker, not the API. The local Compose
   database owner remains a bootstrap convenience; use restricted credentials
   and a separate worker database URL in your production deployment.

7. Read `GET /api/v1/integrations/control`, then resume with
   `PUT /api/v1/integrations/control` and
   `{"paused": false, "expected_version": <current-version>}`. Resuming allows
   the worker to schedule due configured mailboxes and call the real provider.
   This is an owner action for the dedicated test mailbox; no real mailbox
   has been accessed as part of the automated tests.

## Operational API

All routes below require authentication. Operational controls are admin-only;
incoming email is ordinary shared business data under the existing staff policy.

| Route | Behavior |
| --- | --- |
| `GET/PUT /api/v1/integrations/control` | Inspect/pause/resume; writes require the current version |
| `POST/GET /api/v1/mailboxes` | Create/list mailbox configuration; never returns tokens |
| `GET/PUT /api/v1/mailboxes/{id}` | Inspect/replace settings; PUT needs `expected_version` and paused integrations |
| `POST /api/v1/mailboxes/{id}/sync` | Queue an immediate sync; repeated requests return the active job |
| `GET /api/v1/import-jobs` | Inspect bounded job lists; filter by `mailbox_id` and `status` |
| `GET /api/v1/import-jobs/{id}` | Counts, attempts, lease expiry and safe error code; never the lease token |
| `POST /api/v1/import-jobs/{id}/retry` | Retry a failed job after the operator resolves its cause |
| `GET /api/v1/incoming-emails` | Filter by `status=unmatched` or `imported`, and mailbox; bodies omitted |
| `GET /api/v1/incoming-emails/{id}` | Read the stored plain-text message and metadata |
| `POST /api/v1/incoming-emails/{id}/assign` | Agent/admin assigns to an existing `contact_id`, with `expected_version` |

Mailbox addresses cannot be changed. Changing filters/settings or replacing
credentials requires a pause, cancels active jobs, and fences old workers.
Changing the initial lookback explicitly starts a new backfill boundary.
Existing imported records are never silently deleted by configuration changes.

## Recovery and synchronization contracts

- PostgreSQL stores the jobs; no in-memory queue or additional broker is needed.
  Workers schedule due mailboxes and claim jobs using row locks and
  `SKIP LOCKED`. A partial unique index permits one active job per mailbox.
- Jobs have 120-second leases, renewed around provider requests, and a fresh
  ownership token on each claim. Crashed jobs can be reclaimed. Stale workers
  cannot make another guarded request or commit a page.
- Each HTTP request, including OAuth refresh, checks the global pause state,
  mailbox configuration version and active administrator before starting.
  Shared control locks span requests; pausing takes an exclusive lock. In a
  healthy database this waits for current requests before acknowledging pause.
  It does not cancel an HTTP request already issued if a network/database
  failure interrupts the lock connection.
- Provider calls have connect/read timeouts, a streaming deadline, response
  limits, and no redirects or environment proxy discovery. Oversized pages or
  messages fail explicitly instead of silently truncating customer text.
- Transient failures retry with exponential backoff starting at five seconds,
  respecting bounded `Retry-After`. Five failures exhaust the retry budget.
  Credential, identity, permission and oversized-data errors require operator
  intervention. Terminal failures stop automatic scheduling for that mailbox.
- Messages, unmatched receipts, conversations, audit entries and page cursors
  commit in one transaction. A crash before commit replays the page. A crash
  after commit continues from its saved cursor. Uniqueness constraints prevent
  duplicate receipts and messages across replays.
- The first scan captures the mailbox history ID **before** listing messages.
  After paginated backfill, history synchronization catches messages arriving
  during the scan. History IDs advance only after the final page is committed.
- Expired history or pagination resets to a full rescan from the original
  backfill date, preserving the original import boundary. Full-scan queries
  use a fixed timestamp rather than a moving `newer_than` date. The design
  follows Google's [sync guide](https://developers.google.com/workspace/gmail/api/guides/sync)
  and [history API](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list).
- Only allowlisted inbound senders are persisted; sent/draft messages and
  messages predating the backfill boundary are skipped. The authenticated
  Gmail profile must match the configured mailbox before messages are listed.
- Exact known-contact matches create/reuse a conversation by mailbox, Gmail
  thread and contact. Other allowlisted senders remain unmatched until explicit
  assignment. Different contacts in a shared Gmail thread get separate CRM
  conversations. Inactive contact owners do not block ingestion.
- Polling is read-only and captures added messages. It does not mirror Gmail
  label changes/deletions, register push watches, or implement public webhooks.
  Attachment storage and AI processing remain later milestones.

## Secrets, permissions and rotation

Credentials are Fernet-authenticated ciphertext. Access tokens exist only in
worker memory. Refresh-token rotation, when returned by Google, is persisted
encrypted while the job is still fenced. Mailbox configuration, jobs and audit
events expose no token/ciphertext. HTTP errors are reduced to fixed codes;
message content and raw provider exception text do not enter logs or audits.

Apply `backend/runtime-grants.sql` with separately provisioned `crm_runtime`
and `crm_worker` roles. The API can inspect credential presence but cannot
select ciphertext. The worker can read/update credentials but cannot change
the pause flag or update/delete audit events. The operator provisioning role
requires additional credential INSERT access. Neither role receives DELETE.

To replace/revoke credentials, pause first, revoke the old Google grant as
appropriate, and provision new read-only credentials through the operator
command. To change the encryption key, pause all workers and re-provision every
mailbox with the new key before resuming; automatic dual-key rotation is not
implemented. A lost key requires reauthorization, so keep a protected key backup.

## Verification

```powershell
.venv/Scripts/python.exe -m pytest tests/test_backend.py tests/test_imports.py tests/test_backend_gmail.py -q
.venv/Scripts/python.exe -m tools.test_backend_postgres
```

Offline tests cover replay, backoff, pagination, history expiry, pause behavior,
wrong identities, scope checks, HTML conversion, ciphertext/API secrecy, and
manual assignment. PostgreSQL tests exercise real concurrency, lock ordering,
page rollback, restricted database roles, and stale-worker rejection. Both use
fake provider responses and no real Gmail account. Real test-mailbox smoke
testing requires the owner's local OAuth setup and explicit resume action.
