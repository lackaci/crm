# Mini CRM: Copilot agent workflow

## Recommended email workflow

Open **Open CRM.cmd → Setup: Gmail and AI**. Connect Gmail, save/test the
Vercel `nvidia/nemotron-3.5-lightning-free` email model, then **Start 5-minute checks**.
The app imports mail and prepares suggestions while open. In **Review replies**,
edit → **Approve reply** → **Send reply**. Sending always requires your click.
This free model is a non-sensitive trial with provider prompt/output logging;
do not use real confidential/customer data. There is no paid fallback or embedding call.
Document AI stays separate. See [setup and verification](docs/EMAIL_WORKFLOW.md).

## Simplified test app

Open **Start Test.cmd**. Its **Test setup** page groups AI, Gmail, sample emails,
customers and whole-workflow approval in one place. It starts offline in a
separate persistent workspace. Choose a workflow, approve it for the session,
and inspect the results without confirming every individual message.
See [test app setup and sending scope](docs/TEST_APP.md).

## Quick presentation demo

Double-click **Start Demo.cmd**, or run `python -m app.main --demo`.
It opens fictional customers, example emails and approved saved answers in a
temporary offline workspace. Start with **Inbox → Prepare reply → Review replies**.
See [the two-minute walkthrough](docs/DESKTOP_DEMO.md).

For the new single-business, multi-staff server API, see
[backend setup and API contracts](backend/README.md). The desktop setup below
continues to use its existing SQLite database.

## Gmail connection and reviewed sending

Double-click **Open CRM.cmd** for your saved workspace, then click **Gmail
connection** on **Setup**. You can import mail and enable sending to selected
recipients after approving each reply. **Send reply** handles Gmail draft creation internally.
See [Gmail setup and sending instructions](docs/GMAIL_DESKTOP.md).
Inbox **Import from Gmail** opens connection setup if needed, then starts
importing automatically. It uses incremental Gmail history, background progress and a
retry queue. Counted quick filters, indexed search and a side-by-side reader
make incoming and unassigned mail accessible across all customers.
**Start Demo.cmd** remains offline. This Gmail sending flow uses the desktop's
SQLite database; the separate server API remains read-only for Gmail.

## Inbox automation

Open **Automation** to enable scheduled Gmail checks, choose staff assignment
rules, and queue safe reply suggestions for review. **Process imported mail now**
works with existing mail, including the fictional demo. Automation history
explains the category, priority, assignee and outcome; sending still requires
your explicit review. See [automation setup and behavior](docs/INBOX_AUTOMATION.md).

## Read invoices, contracts and other files into Excel

Open **Open CRM.cmd → Files → Excel register** and choose **Online — Vercel AI Gateway** or **Online — OpenAI**
to test without a local model or GPU. Enter your API key in the masked field
and optionally enable online FAQ/reply suggestions too. See
[online AI setup](docs/ONLINE_AI.md). **Local — Ollama** remains optional;
only that option needs **Start Document Model.cmd**.
The selected vision model reads registered customer documents and consolidates
invoice fields, contract dates/parties, line items and other facts into one
Excel workbook. You can review/correct results and reuse unchanged extractions.
See [document model setup and Excel workflow](docs/DOCUMENT_EXTRACTION.md).
The offline demo includes fictional documents and simulated extraction.

## Setup (5 minutes)
1. Install VS Code + the GitHub Copilot and Copilot Chat extensions (sign in).
2. Create a folder `mini-crm`, copy ALL files from this zip into it, `git init`, open it in VS Code.
3. Open Copilot Chat (Ctrl+Alt+I / Cmd+Ctrl+I). At the bottom of the chat, open the
   agent dropdown. You should see: Lead, Architect, BackendDev, AIReplyDev, UIDev, QATeacher.
   (If not: Settings > search "chat.promptFiles" / "custom agents" and make sure they are enabled.
   Reload the window.)
4. Pick **Lead** and type:  /start-milestone 1

## How the flow works
- Lead plans and shows **handoff buttons** under its answer ("Start with Architect", etc.).
  Click one to pass the work to the next agent with a prepared prompt.
- Each worker agent, when done, offers a button to hand over to **QATeacher**.
- QATeacher tests, writes a plain-language note in LEARNING_NOTES.md, and hands back to Lead.
- You review, run the app, `git commit`, then start the next milestone.

## Run the app (after milestone 1)
    python -m venv .venv
    .venv\Scripts\activate        (Windows)   |   source .venv/bin/activate  (Mac/Linux)
    pip install PySide6 pytest
    python -m app.main

## M15 owner-only Gmail test import settings

Only the owner may create `.env` values for the dedicated test mailbox and run a
read-only Dry-run import. Use `MINI_CRM_MODE=Dry-run`,
`MINI_CRM_PROVIDER=gmail`, `MINI_CRM_TEST_MAILBOX`, and
`MINI_CRM_GMAIL_ALLOWED_SENDERS` (plus optional
`MINI_CRM_GMAIL_MAX_MESSAGES` and `MINI_CRM_GMAIL_NEWER_THAN_DAYS`).
The owner also supplies `MINI_CRM_GOOGLE_CREDENTIALS_FILE`,
`MINI_CRM_GOOGLE_KEYRING_SERVICE`, and `MINI_CRM_GOOGLE_KEYRING_USER` locally;
do not commit credentials, tokens, mailbox contents, or real personal data.
After import, run `python -m tools.export_for_golden` with the local database
path. It writes review-only `tests/golden/candidates.json` and never merges it
into `emails.json` or sends mail.

## Useful prompts
- /start-milestone 3      begin a milestone
- /explain-this           select code, then ask for a plain-language explanation
- Ask QATeacher anything: "why do we use an interface here?"

## Note on tool names
Tool names in the agent files (search, edit, runCommands...) can differ slightly between
VS Code versions. If an agent can't edit files or run commands, click the tools icon in
the chat box and enable them for that agent, or adjust the `tools:` line.
