# Showing Mini CRM to a friend

Double-click **Start Demo.cmd** in the repository folder. It uses the existing
project virtual environment and opens a fresh, offline desktop workspace.
Alternatively:

```powershell
.venv/Scripts/python.exe -m app.main --demo
```

The demo has fictional customers, five example messages and four approved
saved answers. It does not open `mini_crm.db`, connect to Gmail, or use your
configured live provider. Its database and file directory are temporary;
closing the demo discards its changes. Start it again for a fresh walkthrough.

## A two-minute walkthrough

1. **Inbox** opens with Anna's opening-hours email already visible.
2. Click **Prepare reply**. The app opens **Review replies** with that new reply
   selected. The original email, saved answer and editable reply appear together.
3. Edit the reply, then **Save changes** or **Copy reply**. Sending is disabled.
4. Open **Saved answers** to show where the approved response came from. The
   optional **Try a sample message** tool explains which answer would match.
5. Return to **Inbox**, choose Maria in the customer picker, select her refund
   message and click **Prepare reply**. The inline status explains that this
   message needs personal attention instead of creating an unsafe answer.
6. To show assignment, open **Needs assignment**, select a message, choose a
   customer and click **Link to customer**. The app opens the linked message
   under that customer automatically.

## Where things are now

| Menu | What it does |
| --- | --- |
| Inbox | Read/search messages, import mail, prepare replies, assign unknown senders |
| Customers | Search customers and add/edit their details |
| Review replies | Review, edit, copy or discard replies; regular CRM also supports Gmail draft/send |
| Saved answers | Add, edit, approve and deactivate FAQ answers |
| Files | View/store files for the customer selected above |
| Settings | Connect Gmail, control draft/send permissions and pause mail activity |
| Automation | Schedule triage, choose staff rules and inspect action history |

Inbox and Files share the same customer picker. Selecting a customer in the
Customers screen updates that picker too. Switching screens or marking a
message seen preserves the selected customer and message. Customer edits keep
the saved customer selected. Keyboard shortcuts **Ctrl+1** through **Ctrl+7**
open the seven screens.

To demonstrate automation, open **Automation**, check **Enable inbox automation**,
save settings and click **Process imported mail now**. Inspect the category,
assignment and explanation in its history, then open a pending reply for review.
The demo only processes its fictional mail and never polls Gmail. See
[automation details](INBOX_AUTOMATION.md).

Cleanup is under **More** in the Gmail inbox. Assignment controls appear only
in **Needs assignment**. Loading example answers belongs to **Saved answers**
and is available in Mock mode. Technical privacy tools are collapsed by default.

For ordinary use, open **Open CRM.cmd** or run `python -m app.main`. That opens
the configured desktop workspace and preserves its data. The **Gmail connection**
button opens account setup and the reviewed sending settings; see
[Gmail setup](GMAIL_DESKTOP.md). The demo keeps these controls disabled.
This redesign changes the PySide6
desktop client; the PostgreSQL/API server and local vector-search setup remain
documented separately in `backend/README.md` and `docs/SEMANTIC_SEARCH.md`.

## Visual previews

- [Inbox](previews/inbox.png)
- [Review replies](previews/review.png)
- [Customers](previews/customers.png)
- [Inbox at 900 × 620](previews/inbox-small.png)

These images contain only the fictional demo workspace. To regenerate them:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv/Scripts/python.exe -m tools.preview_desktop
```

Journey checks cover direct reply review, preserved selection, assignment,
search, risky-message escalation, disabled sending, demo cleanup and attachment
storage isolation. Previews were inspected at 1100 × 760 and 900 × 620.
