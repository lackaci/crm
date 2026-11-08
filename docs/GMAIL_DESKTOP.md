# Gmail in the desktop CRM

Use **Open CRM.cmd** for your saved desktop workspace. **Start Demo.cmd** stays
offline and cannot connect to Gmail or send messages. Install/update desktop
dependencies first if necessary:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

For the simpler separate test workspace with one setup page and session-wide
workflow approval, use **Start Test.cmd** and [the test app guide](TEST_APP.md).
For normal use, **Open CRM.cmd → Setup: Gmail and AI** now provides a five-minute
import schedule and the per-message **Approve reply → Send reply** flow.
See [the complete email workflow](EMAIL_WORKFLOW.md). The separate test app
retains session approval and is not the normal sending flow.

## Connect and import

1. In Google Cloud, enable the Gmail API, configure the OAuth consent screen,
   and create an OAuth client of type **Desktop app**. If the consent screen is
   in testing, add your Gmail account as a test user. Download the client JSON
   to a private location **outside this repository**.
2. Open CRM, click **Inbox -> Import from Gmail**, enter your Gmail address and choose
   that client JSON. Optionally restrict import to particular sender addresses
   and change the number of messages and lookback days.
3. Leave both write/send checkboxes off for a read-only import. Click
   **Connect / reconnect Gmail** and complete Google sign-in. The app checks
   that the authenticated account matches the address you entered.
4. If you started from **Import from Gmail**, Inbox opens and imports automatically
   after connection. On later visits, click **Import from Gmail** to fetch new
   mail. Unknown senders appear
   under **Needs assignment**; link them in the reading pane. The new workflow
   also permits a clarification/manual reply without linking first.

## Import from Gmail, progress and the incoming queue

The regular Inbox always shows a primary **Import from Gmail** button, even
before Gmail is configured. With no connection it opens account setup; after
successful connection, it returns to Inbox and starts importing automatically.
With Gmail configured it immediately starts an incremental refresh. The offline
demo instead shows **Import example emails** and cannot open Google sign-in.

The Inbox displays the active sender restriction, lookback days and per-import
limit. **Change import options** opens those settings directly. To include all
senders, clear **Import senders**, choose the desired lookback, and **Connect /
reconnect Gmail** to apply and import. Changing scope starts a fresh discovery
for that configuration, while preserving and deduplicating stored messages.
Restrictions are never silently removed by clicking Import.

Inbox opens to **All customers**, newest messages first. Each entry shows the
sender, subject, preview, arrival date and status. Use **All**, **Unread**,
**Needs assignment**, **Needs attention** or **Reply pending**; each shows a
message count. The sidebar keeps the total unread count visible across filters.
Unread means locally unopened in this CRM; opening a message does not change
its Gmail labels or request additional Google permissions.

Search covers sender, subject and body across stored messages, including records
outside the current page. It waits briefly while you type and uses a SQLite
full-text index for queries of at least three characters; shorter queries use a
literal substring scan. Results are displayed 100 per page with Previous/Next.
Selecting a customer scopes the list; choose **All customers** to return to the
shared queue. Needs assignment always covers unlinked messages across customers.
Filter counts follow the current search and scope; the unread total stays global.

Selecting a message opens the reading pane beside the list and marks it read
locally. It shows the customer, assigned staff label, status and any attention
reason. For an unknown sender, choose an existing customer and **Link to
customer** directly in this pane. **Prepare reply** becomes **Review reply**
when a pending suggestion already exists. Opening a message in Unread leaves
it visible until the next list refresh so it can still be read.

**Import from Gmail** runs in a background worker. It shows discovery/connection status,
then progress such as **Importing... 12/40**, and finishes with **Imported 7 new,
5 already existed, 0 failed**. Manual import returns to All at
the first page and clears search/quick filters, even when no new mail is found;
scheduled checks preserve your current view and retain the
selected message when it remains on that result page.

When there is no new matching mail, the result explains that explicitly and
points to import options. A completed import with zero new messages is different
from a failed connection; the error message is retained if the connection failed.

The first refresh discovers messages within the configured sender restrictions
and lookback window. Sent mail and drafts are excluded; spam/trash are not
imported. Subsequent refreshes use a saved Gmail history ID. Existing message
IDs are checked before downloading the full message or its attachments. If
Gmail expires the history ID, the CRM rechecks the configured lookback and
skips records already stored. It does not delete older CRM messages.

Discovery pages and unfinished IDs are saved in SQLite. The configured message
limit bounds downloads per refresh; extra IDs remain queued for the next
refresh/check. Each message commits separately. One malformed message cannot
roll back the successful ones, and failed downloads stay queued for retry.
Authorization, quota or network failures stop the download batch early. Errors
suggest reconnecting, checking internet access or waiting for quota recovery.
Saved messages remain visible even after a partial failure. Refresh uses cached
authorization only; sign-in is an explicit action in **Gmail connection**.

In the legacy FAQ-copy flow, reply progress and errors appear beside the reply button. For an unlinked
sender the action is labeled **Link customer first**, and clicking explains
the linking step. Missing/unmatched approved answers show a direct **Open saved
answers** shortcut. A successful **Prepare reply** opens **Review replies**;
an authentication/model failure leaves an explanation and permits retry.

Automation's scheduled check uses this same cursor, queue and import worker.
Both remain read-only unless you separately enable reviewed Gmail operations.
The added SQLite fields, indexes, search triggers and sync tables are additive;
existing records and read/done statuses are preserved during migration.

Tokens are stored in your OS keyring, not in SQLite or a token file. Only
connection metadata, such as the account, client file path, import settings and
recipient allowlist, is saved in SQLite. These saved connection settings take
precedence over environment settings on the next normal launch. The app does
not edit `.env`.

## Approve a reply and send it

1. In **Gmail connection**, check **Allow creating Gmail drafts**. Reconnect to
   grant the additional permission. With live sending unchecked, the app can
   import and create unsent Gmail drafts but cannot send them.
2. To send from the CRM, also check **Enable live sending after I review and
   confirm each reply**. Enter the permitted destination addresses in **Send
   only to**, separated by commas, then reconnect/apply. An empty list in the
   form allows only your own Gmail address.
3. Configure/test the independent Vercel email model in **Setup**, then start
   five-minute checks. Imported messages get suggestions in a durable worker
   queue. You can also use **Prepare reply** or **Write reply / review existing**.
   Missing facts and risky requests offer an editable clarification.
4. In **Review replies**, read the source facts and edit. Click **Approve reply**
   to save and approve that exact content for the displayed recipient/account.
   Editing or regeneration removes approval; approval alone never sends.
5. Click **Send reply**. The worker creates the Gmail draft internally and sends
   the exact reviewed content, with no second content dialog. It marks the original email
   done when Gmail returns a message ID. Gmail accepting the message does not
   guarantee delivery to the recipient.

Confirmed sends remove the original email from Inbox and its filter counts.
Use **Replied** in the main menu for searchable, persistent history with the
original message and exact sent text. Existing confirmed sends appear there
automatically. Unsent drafts and failed/uncertain attempts remain active.

A staged draft is locked for editing. **Cancel Gmail draft** deletes that
remote draft and unlocks the local reply. Changes made to the Gmail draft
outside the CRM are replaced with the exact locally approved content on send.
Replies currently contain plain text, one recipient and no outgoing attachments,
Cc or Bcc. The old unrestricted `send_email` interface remains disabled.

The draft permission is Google's `gmail.compose` scope, which also permits
sending at the Google account level. Import-only mode without draft permission
requests only `gmail.readonly`. Dry-run with compose consent prevents sending
in the CRM's delivery service; Google does not offer a draft-only OAuth scope.
See Google's [draft guide](https://developers.google.com/workspace/gmail/api/guides/drafts)
and [draft send reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/send).

## Pause and uncertain results

**Pause mail imports and delivery** blocks new imports, draft operations and
sends. It cannot recall an operation Gmail has already accepted; pause is
acknowledged only once its local state has been saved.

Each draft/send approval records an intent before contacting Gmail. Duplicate
clicks are blocked, and mutation requests have no automatic retries or redirects.
If the connection fails after Gmail may have accepted an operation, the app locks
that attempt instead of trying again. Use **Check Gmail status**: it searches
for that reply's stable Message-ID and updates the local record when found.
Search can take time to reflect a new message. An absent result leaves the
attempt locked; it is not proof that the message was never sent. A crash during
an operation uses the same status-check recovery path after reopening the CRM.

Older Gmail imports without an account binding must be reimported from the
connected account before sending. Matching records are adopted without
duplicating them; records from different accounts stay separate.

## Scope and verification

This flow belongs to the **SQLite/PySide6 desktop app**. The separate multi-staff
PostgreSQL API and its Gmail import worker remain read-only; the desktop is not
a staff-authorized sending client for that server.

Fictional previews: [Gmail settings](previews/gmail-settings.png),
[reply review](previews/gmail-review.png), and
[review in a smaller window](previews/gmail-review-small.png).

The automated checks use fictional addresses, fake Gmail services and an
in-memory HTTP transport. They cover account/recipient boundaries, exact MIME
content, confirmation, scope upgrades, concurrent clicks, crash/timeout
reconciliation and demo isolation. No real OAuth account was connected and no
real email was sent during implementation. Complete a manual end-to-end check
with your own test mailbox after supplying your local Google OAuth client.

Latest validation: **419 passed, 16 PostgreSQL checks skipped**, process exit
code **0**. Tests include incremental refresh, duplicates, partial failures,
retry queues, filters/search, unread state and existing reviewed delivery.
Mouse-click checks also cover the explicit Gmail import button, setup followed
by automatic import, reply review navigation, missing answers and model retry.
The earlier post-success native Qt abort was resolved through explicit test
widget cleanup. Fictional previews were inspected at 1100x760 and 900x620.
The new workflow also has [Setup](previews/email-setup.png),
[Gmail controls](previews/email-setup-gmail.png), [reply approval](previews/email-approval.png)
and [small-window approval](previews/email-approval-small.png) previews.
See [investigation and manual verification](INBOX_REFRESH.md).
