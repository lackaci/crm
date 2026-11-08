# Desktop inbox investigation and verification

Work is limited to `app/`, desktop tests, preview tooling and documentation.
The separate `backend/` application is unchanged.

## Investigation before changes

Both regular and demo entry points were launched with short-lived Qt event
loops and network access blocked. This exercised startup without authenticating
to a real mailbox or sending customer content to an online model. Gmail's API
path was traced in code and then exercised through injected offline services.

The path was `GmailProvider.fetch_new_emails` -> `import_emails` -> SQLite ->
`CustomerInboxWidget`. Manual and scheduled Gmail imports already used a
`BackgroundTask`; the UI thread was not the main network bottleneck.

Concrete causes found before editing:

1. Every refresh listed the same relative lookback and fetched full messages
   and attachments before checking whether SQLite already contained their IDs.
2. There was no saved Gmail history cursor, page position or unfinished queue.
3. Fetching built one complete list; saving used one batch transaction. One
   fetch/normalization/storage error could prevent the batch from being saved.
4. Status showed only import start and new count. Duplicate/failure counts,
   per-message progress and actionable error categories were absent.
5. The default view required a customer selection; unassigned mail lived in a
   second tab, with no shared incoming queue or quick status filters.
6. Lists sorted oldest first, omitted dates and pending/attention badges, and
   hid previews. Search scanned the loaded widget cells on each keystroke.
7. Opening unmatched messages did not mark them seen. There was no visible
   global unread count, and customer/date/filter queries lacked suitable indexes.

## Implemented steps

The import action is now explicitly labeled **Import from Gmail**. It is
visible before connection and opens setup when needed; successful connection
returns to Inbox and starts import automatically. In Demo it remains the offline
**Import example emails** action. Reply progress/errors are shown beside the
button, unknown senders show **Link customer first**, and missing knowledge
offers **Open saved answers**. Actual mouse clicks are covered by tests.

Import: one shared read-only synchronization path uses Gmail history IDs,
persisted discovery pages and a retry queue. It commits each message separately,
skips duplicate payload downloads, retains capped backlogs, handles expired
history with a bounded-lookback recheck and reports progress/results/errors.
Authorization/quota/network failures stop further downloads in that batch.
Gmail sign-in remains in the explicit connection flow. No send scopes changed.

Inbox: All customers opens by default. Counted quick filters and debounced,
indexed search query SQLite rather than the visible rows. Compact entries keep
sender, subject, preview, arrival date and status visible beside the reading
pane at both desktop sizes. Results have 100-message pagination. Open messages
become read locally; the sidebar retains a global unread count. The reader
shows customer/staff context and direct linking or reply preparation/review.

Storage: additive migrations preserve content, IDs and read/done state, backfill
normalized timestamps/risk flags and create maintained FTS5 trigram indexes.
Sync state is scoped to the verified account and sender/lookback configuration.
Read state is local; no Gmail modify permission or label mutation is introduced.

## Manual checks

1. Run `Start Demo.cmd`. Confirm all five fictional messages are reachable in
   All, newest first. Click Unread and open one: the unread count falls while
   the reader stays open. Choose another filter to refresh that result list.
2. Choose Needs assignment, select an unknown sender, choose an existing
   customer and Link to customer. Confirm the customer appears in the reader
   and Prepare reply becomes available. No customer has to be hunted in a
   separate menu before the message can be opened.
3. Choose Needs attention and open the refund example. Check the reason and
   that preparing a reply still requests personal attention. Use Anna's
   opening-hours example to prepare a normal reply, then verify Reply pending
   and Review replies can reach it. No demo email can be sent.
4. Open the regular CRM and connect a test Gmail account with write/send
   permissions off. Click Refresh. Type in search or switch screens during
   download: the interface should respond. Watch discovery, Importing... N/M,
   the final new/existing/failed counts and any retained queue count.
5. Refresh again: old messages must not be duplicated. Have a new test email
   arrive, Refresh again, and find it through All/Unread or sender/body search.
   Search uses all matching stored messages, not just the current page.
6. To check backlog handling, lower the import message limit and use a test
   mailbox containing more eligible messages than that limit. Repeated refreshes
   should drain the queue without dropping older discoveries.
7. Disconnect internet access and Refresh: the message should suggest checking
   the connection and previously stored mail must remain readable. Reconnect
   Gmail explicitly if authorization expires. Per-message malformed/quota and
   history-expiry scenarios are covered by offline automated services.
8. Enable Automation and save an interval. Leave a message selected and wait
   for a scheduled check. Its cursor, duplicate handling and progress should
   match manual Refresh, while preserving the selected reader/filter. Close
   the app to stop scheduled checks. Automation must never send mail.
9. Prepare/edit a reply, create/send a Gmail draft only through the existing
   explicit permissions and confirmations using your test recipient. Verify
   the Files -> Excel register workflow still works independently.

The normal and 900x620 demo layouts are in `docs/previews/inbox.png` and
`docs/previews/inbox-small.png`. No real Gmail calls or paid AI requests are
needed for the automated checks.

## Final verification

The full suite reports **383 passed, 16 skipped**, with process exit code **0**.
Additional checks verify that zero-new manual imports clear hidden search and
filters, scheduled imports preserve them, and sender/date restrictions are
visible with a direct options link without changing saved restrictions.
The skipped checks need the separately configured PostgreSQL test database.
An existing FastAPI/Starlette deprecation warning remains unrelated to the
desktop change. Regular and demo startup passed again with networking blocked;
the fictional layouts were rendered at 1100x760 and 900x620.

Acceptance checks cover initial/incremental import, duplicate skipping before
payload download, pagination and interrupted discovery, expired history,
partial message failures and retry, capped backlogs, account/pause boundaries,
quota/network/authentication errors, incoming-only scope, migrations, dates,
filters, maintained search indexes, 4,000-message search/pagination, read state,
direct linking and responsive background refresh. Existing reply, reviewed
delivery, automation and document tests also passed in the full suite.

The old post-success exit error was reproduced with Python's crash handler:
pytest returned result 0, then native code aborted during interpreter shutdown.
A diagnostic run found 23 remaining main windows, 405 popup frames and other
GUI objects, with zero running workers. Closing/deleting those objects and
draining deferred Qt deletions produced a clean process exit. The test session
now performs that cleanup while Qt is alive, without overriding pytest's exit
status or forcing the process to exit. The ordinary suite command subsequently
completed with exit code 0.

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv/Scripts/python.exe -X faulthandler -m pytest -q
```

Gmail history behavior follows the official
[synchronization guide](https://developers.google.com/workspace/gmail/api/guides/sync)
and [history API](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list).
