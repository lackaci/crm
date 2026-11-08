# Automatic inbox triage

For the recommended five-minute Gmail → AI → Approve → Send flow, use
**Setup: Gmail and AI**, described in [Email workflow](EMAIL_WORKFLOW.md).
Selecting it gives that workflow control of scheduling. These advanced routing
rules remain available; post-import routing keeps category/staff assignments,
while reply generation uses the independent email model instead of the legacy
FAQ-copy pipeline. The timer and AI never send. Pausing the new workflow does
not silently reactivate this page's older polling interval.

Open **Open CRM.cmd**, connect Gmail, then open **Automation** in the sidebar.
For a fictional walkthrough, use **Start Demo.cmd** instead: the same triage
tools work on demo mail, and scheduled Gmail access remains disabled.

## Start using it

1. Open **Staff and assignment rules**. Add staff names and choose who handles
   questions, quote requests, complaints, billing and other messages. Choose
   normal or high priority for each category. Leave a category unassigned if
   it should stay in the shared queue.
2. Check **Enable inbox automation**. Leave **Prepare safe replies from approved
   saved answers** checked if you want suggestions. Choose the Gmail check
   interval (30–3600 seconds; default 120), then **Save automation settings**.
3. Click **Process imported mail now** to triage the messages already in SQLite.
   This button does not import mail. For an immediate Gmail fetch, use
   **Inbox → Import from Gmail**. Enabled automation also runs after that import.
4. Select an action in the history to read its explanation. **Open message**
   takes you to the message/customer; **Review reply** opens its pending reply.
   The inbox also shows the category, priority and assigned staff name after
   triage. You can search that text using the inbox search field.

The latest 200 actions are displayed, with older history retained in SQLite.
Rule changes affect unprocessed messages; they do not silently reassign
existing work. Staff names here are desktop routing labels, not login accounts
or mappings to the separate multi-staff API.

## What runs automatically

While the desktop app is open and automation is enabled, a timer checks Gmail
at the saved interval. It uses your existing Gmail account, sender restrictions,
message limit and lookback window. Background checks can refresh a cached OAuth
token but never open Google sign-in. If the connection fails, the inbox shows
an inline message; reconnect through **Gmail connection**. The next scheduled
check retries the read. Closing the app stops the schedule.

Manual Refresh and scheduled checks use the same background import service,
saved Gmail history cursor and durable message queue. Messages already stored
are skipped before payload download; one bad message does not discard good
imports. The Inbox shows progress, new/existing/failed counts, and any remaining
queue. History expiry triggers a lookback recheck with duplicate skipping.
Scheduled refresh preserves the active Inbox filters and retains the selected
message when it remains on that result page.

Each imported, unfinished message is processed in a batch (up to 50 per run):

* Match an existing customer by email, ignoring letter case. An unknown or
  ambiguous sender stays under **Needs assignment**; no customer is invented.
  After you link the message or add the matching customer, a later run can
  finish its triage without reimporting it.
* Categorize the subject/body using fixed keyword rules: complaint first, then
  quote, billing, question, or other. Urgent wording and escalation triggers
  raise priority to high. Category rules supply the staff assignment.
* Check the existing reply guardrails. Refunds, legal threats, complaints,
  money amounts and unclear messages require attention rather than a new
  automated reply.
* For eligible known customers, use the existing approved-FAQ matcher and
  validated reply pipeline. A sufficient match creates a **local pending
  reply**. A missing/uncertain match or failed suggestion requires attention.
  Existing replies, including manually edited or rejected ones, are preserved.

The desktop uses its deterministic offline FAQ provider by default. If online
FAQ suggestions are enabled in Files -> Excel register, automation uses that
selected online provider with the existing masking and answer validation.
It does not use the separate API's pgvector index. The rule categories are
simple English keyword matches, not a semantic
classifier; review them as suggestions rather than guaranteed interpretations.

## Controls and recovery

**Pause automation** saves an immediate stop for new triage and scheduled
checks. **Gmail connection → Pause mail imports and delivery** additionally
blocks manual mail imports and delivery. Neither control recalls a read or send
already accepted remotely. An import that was already running may finish saving
its messages, but paused automation cannot commit a newly generated reply.

Triage rechecks the message, pause/settings revision, existing replies and FAQ
approval before saving. Its reply, outcome and history commit together. Two
automation runs cannot create duplicate replies, and interrupted/rolled-back
work remains eligible on the next run. Once a message has a completed triage
record, ordinary runs do not repeat it. Messages requiring attention are handled
manually through Inbox/Review replies; changing rules or saved answers does not
regenerate them automatically. Each run processes at most 50 eligible messages;
use the button again or wait for the next scheduled check for a larger backlog.

Automation cannot create a remote Gmail draft or send a message. Those actions
remain in **Review replies**, with the existing account/recipient checks and
explicit confirmations. See [Gmail sending](GMAIL_DESKTOP.md).

This feature belongs to the SQLite/PySide6 desktop app. It does not install a
server daemon, start a task when the desktop is closed, create staff login
accounts or change the separate PostgreSQL/API worker.

## Offline verification and previews

Tests use fictional mail, isolated SQLite databases and network-blocked fake
providers. Checks cover category rules, unknown/ambiguous customer matching,
safe FAQ suggestions, risk escalation, repeat imports, concurrent workers,
transaction rollback, stale FAQ changes, pause races, non-interactive OAuth,
and the route from staff rules to reply review.

The latest offline suite passed with **383 tests** and process exit code **0**;
**16 PostgreSQL checks** were
skipped because no test server was configured. All **69 golden reply cases**
passed with zero wrong drafts or wrong FAQ selections. Background refreshes
also preserve an unsaved reply, cursor position and the selected history event.
No real Gmail account or customer mail was accessed during this implementation.
See [the inbox investigation and manual checks](INBOX_REFRESH.md) for incremental
refresh coverage and the resolved native Qt test-shutdown issue.

Previews: [Automation](previews/automation.png),
[smaller window](previews/automation-small.png),
[assignment rules](previews/automation-rules.png).

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv/Scripts/python.exe -m pytest tests/test_automation.py -q
.venv/Scripts/python.exe -m tools.preview_desktop
```
