# Learning notes
(QATeacher appends a short "how this works" section after each milestone.)

## Gmail allowlist and compact navigation

The Gmail provider now imports only senders listed in
`MINI_CRM_GMAIL_ALLOWED_SENDERS`. The Inbox has a cleanup button that removes
older imported Gmail messages outside that allowlist after confirmation; each
cleanup is recorded in the audit log. Customer details, Inbox, and Files are
now separate tabs, so the main screen does not cram every panel into one tall
page.

The Inbox uses a compact message list and a larger reading preview. Select a
row to read the complete message below the list; long messages scroll inside
the preview instead of making every table row tall.

Gmail imports also use pagination, a maximum message limit, and a date filter.
The current `.env` defaults are 100 messages from the last 30 days. Set
`MINI_CRM_GMAIL_NEWER_THAN_DAYS=none` to disable the date filter, or lower
`MINI_CRM_GMAIL_MAX_MESSAGES` to import fewer messages.

Unmatched messages can now be assigned explicitly. Select a message in the
Unmatched emails tab, choose an existing customer, and click **Assign unmatched
email**. The app never guesses the customer, and the assignment is recorded in
the audit log.

## Unmatched Gmail messages

The Inbox panel now has a second table for imported messages whose sender does
not match a customer. This makes it possible to review Gmail messages before
creating the matching customer record. The original customer inbox still shows
only messages assigned to that customer.

After importing emails, look below the customer inbox for **Unmatched emails**.
Create a customer using the sender's exact email address, then select that
customer to see the message in the normal inbox. Unmatched messages are shown
for review only; the app does not guess which customer they belong to.

## Milestone 10: Personal-data masking

The LLM boundary now replaces payment-card-like values, checksum-valid IBANs,
and configured ID patterns with request-scoped deterministic placeholders.
Responses are restored only when every placeholder is known and unambiguous;
otherwise drafting escalates.  The cloud adapter and the provider decorator
mask both completion and embedding inputs, while the debug preview displays
masked text only and does not persist it.

Privacy tests cover recognition, provider boundaries, restoration failures, and
preview non-disclosure. Run the focused privacy tests, `pytest`, and
`python -m tests.run_evals` offline; no API key or external service is needed.

## M10 QA pass

### What we built

The app now hides card numbers, IBAN-like values, and configured ID numbers
before text reaches an external language model. It uses temporary labels, like
putting a cover over private text before showing a document to someone else.
Safe responses can put back only the labels that belong to that request.
Unclear or failed restoration stops drafting instead of guessing.

### How it works

1. The reply pipeline wraps the model provider in the masking boundary.
2. The boundary masks completion and embedding input before delegation.
3. The cloud provider rejects direct unmasked completion calls.
4. The response is restored only when every label is known and unambiguous.
5. The preview and provider audit keep masked text or metadata only.
6. The fake provider remains the offline default, and sending stays disabled.

### Key words

- **Masking:** Replacing private text with a temporary safe label.
- **Placeholder:** A label such as `[[PERSONAL_DATA_001]]` used during a request.
- **Restoration:** Putting the original value back after a safe response returns.
- **Fail closed:** Stop rather than create a draft when the safety check is unsure.
- **Boundary:** The one controlled doorway through which model requests pass.

### Try it yourself

1. Run `pytest` and confirm the 79 tests pass.
2. Run `python -m tests.run_evals` and confirm 64/64 offline examples pass.
3. On Windows, run `$env:QT_QPA_PLATFORM='offscreen'; python -m app.main`
   on a headless machine;
   the window starts without making a network call. Open the privacy preview
   in the app and check that a card-like value is shown only as a placeholder.

### What comes next

M12 may define a reviewed sending allowlist, but this prototype must not send
email before then. A real product would also need independent penetration
testing, stronger identifier detection, privacy-safe operational monitoring,
and a controlled secret store. The current checks are intentionally small and
offline.

## Milestone 1: The foundation

### What we built

We created the empty Mini CRM desktop app. It opens a window titled **Mini CRM**,
but it does not have customer screens yet. We also created the folders and
interfaces that later parts of the app will use. The project has offline fake
providers, so we can learn and test without sending email or calling an AI service.

### How it works

1. `python -m app.main` starts the Python program.
2. PySide6 creates the empty desktop window.
3. The provider interfaces describe what an email service and an AI service can do.
4. The fake providers supply safe offline behavior for development.
5. The SQLite schema describes where customers, emails, FAQs, drafts, files, and
   audit records will be stored.

```text
Start app -> PySide6 window
                |
                +-> later: database helpers and screens
                +-> later: reply rules and provider implementations
```

### Key words

- **Interface:** A small promise that says which operations a service must provide.
- **Provider:** The replaceable code that talks to an outside service.
- **Fake:** A safe substitute used for testing instead of a real service.
- **Schema:** The blueprint that describes the tables in a database.
- **Audit log:** A history of important actions in the app.

### Try it yourself

1. Activate the virtual environment and run `python -m app.main`.
2. Notice that the window title is **Mini CRM**, then close the window.
3. Open `app/db/schema.sql` and find the tables planned for customers and emails.

### What comes next

Milestone 2 will add customers to SQLite and show them in a simple table.
This prototype still has no real email connection, AI service, or send action.
Those features will be added only behind the safety rules and approval steps.

## Milestone 3: The offline inbox

### What we built

Mini CRM can now load five example messages from `sample_data/emails.json` without
an internet connection. It compares each sender address with customer addresses,
ignoring upper- and lower-case differences, and links matching messages to the
right customer. A message from an unknown sender is still saved, but it remains
unassigned instead of being guessed. The customer screen can show a selected
customer's subject, sender, and message body.

### How it works

1. Click **Import sample emails** in the customer's Inbox panel.
2. The mock email provider reads the local JSON file.
3. The database helper checks each sender address against existing customers.
4. Matching messages get a customer link; unmatched messages get no link.
5. The message ID prevents the same message from being stored twice.
6. Select a customer to refresh the table and read that customer's messages.

```text
sample_data/emails.json
          |
          v
offline email provider -> database helper -> SQLite emails table
                                      |
                                      v
                         selected customer inbox table
```

### Key words

- **Mock provider:** A pretend email service that reads local sample data.
- **Import:** Copying messages from the provider into the app database.
- **Match:** Comparing a sender email with a customer's email address.
- **Unassigned:** A saved message that has no known customer yet.
- **External ID:** A provider message ID used to avoid importing it twice.

### Try it yourself

1. Run `pytest -q` and see the email import and customer UI tests pass.
2. Run `python -m app.main`, add a customer using `anna@example.com`, select
   that customer, and click **Import sample emails**.
3. Look at the Inbox table: Anna's message shows its subject, sender, and body.
   Import again and notice that it does not create a second copy.

### What comes next

This is still a learning prototype: the inbox uses fixed sample JSON, not Gmail,
and it does not yet provide search, pagination, or a way to assign an unknown
sender. The next milestone will add FAQ matching and safe reply drafts. A real
product would also need stronger message identity rules, better error reporting,
and careful handling of large or changing inboxes.

## Milestone 4: Safe FAQ reply drafts

### What we built

Mini CRM can now compare an incoming message with approved FAQ questions. When
the match is strong enough, it asks the offline fake AI to rewrite only the
approved answer and saves the result as a pending draft. Messages about refunds,
legal threats, anger, money, or unclear text are escalated instead. The system
also rejects provider text that invents extra prose, so a draft cannot quietly
become a new answer. This is still a prototype: the fake AI is deterministic,
and the owner approval screen and real sending flow come in a later milestone.

### How it works

1. A message enters the reply pipeline.
2. Early safety checks look for risky, empty, punctuation-only, or number-only
   input. These messages stop before any provider call.
3. The app loads only approved FAQs and compares their meaning with the message.
4. A weak match is escalated instead of becoming a draft.
5. A strong match gives the provider only the approved answer to rewrite.
6. Strict checks reject an invented FAQ ID, empty reply, new numbers or links,
   or extra prose. Only a safe result is saved as `pending`.

```text
message
   |
   v
safety check -- risky/unclear --> escalation
   |
   v
approved FAQ match -- weak --> escalation
   |
   v
rewrite approved answer
   |
   v
strict validation -- invented text --> no draft
   |
   v
pending draft (not sent)
```

### Key words

- **Approved FAQ:** An answer the owner has allowed the system to use.
- **Match score:** A number showing how closely a message resembles an FAQ.
- **Threshold:** The minimum match score required before drafting.
- **Escalation:** A stop sign that sends the message to the owner instead.
- **Pending draft:** Text waiting for a later owner approval; it is not sent.

### Try it yourself

1. Run `pytest -q` and see the full offline safety suite pass.
2. Look at `tests/test_reply.py` and run the unclear-message test; punctuation
   and number-only messages make zero provider calls and create no draft.
3. Run the invented-prose test and see that an otherwise good answer is
   escalated when the provider appends an unrelated sentence.

### What comes next

Milestone 5 should add the approval queue with Approve, Edit, and Reject
buttons, plus audit entries for each owner action; approval must remain the
only path that could mark a reply as sent. A real product would also need
stronger multilingual safety checks, provider failure reporting, and security
testing around the cloud connector. The optional cloud connector currently uses
a placeholder authorization header, so it is not ready for real service use.

## Milestone 5: reviewing replies before sending

### What we built

We built a review queue for reply drafts. It shows drafts that are waiting for
the owner. The owner can edit a draft, reject it, or approve it. Only Approve
calls the email provider; the current provider prints a safe mock message
instead of sending a real email.

### How it works

```text
pending draft
     |
     v
owner reviews it
  |       |        |
 Edit   Reject   Approve
  |       |        |
save    rejected  mock provider
local   + audit   approved + audit
draft
```

1. The queue asks the database for drafts with `pending` status.
2. Selecting a draft shows the incoming email and proposed reply.
3. Edit changes only the saved draft text and records an audit entry.
4. Reject changes the status to `rejected` and never calls the provider.
5. Approve calls the injected provider first. A successful call changes the
   status to `approved`; a failed call is shown, recorded, and leaves it
   pending.

### Key words

- **Approval queue:** A waiting line of drafts for a person to review.
- **Provider:** A replaceable adapter for an outside service, such as email.
- **Audit log:** A history of important actions and who performed them.
- **Injected provider:** A provider handed to the code, making safe tests easy.
- **Pending:** Waiting for an owner decision; it has not been sent.

### Try it yourself

1. Run `pytest -q` and see the approval and safety tests pass offline.
2. Start the app with `python -m app.main`, open **Approval Queue**, and
   select a pending draft if the database has one.
3. Edit or reject a draft and inspect the `audit_log` table; approve a draft
   to see the mock email printed in the terminal.

### What comes next

This prototype still uses a mock provider, so approving a draft does not send
real mail. A real product would need a real provider, owner identity and
permissions, stronger retry rules, and protection against duplicate sends.
It would also need clearer success messages and more end-to-end UI tests.

## Milestone 6: storing customer files safely

### What we built

We added a small file area to the customer screen. An owner can choose a local
file and label it as an invoice, contract, or other file. The app copies the
file into a folder named for that customer, such as `customer_1`. It also
records the file and the action in the database, so the screen can show what
was stored.

### How it works

```text
owner selects customer
        |
        v
choose a local file + choose a type
        |
        v
check the file and customer
        |
        v
copy to customer_<id>/filename
        |
        v
save a database row + audit entry
```

1. The customer row is selected.
2. The owner chooses a file and one of the three allowed types.
3. The storage code checks that the source exists and is a real file.
4. The file is copied without replacing an existing file with the same name.
5. The database stores the file details and an audit entry.
6. The customer panel reloads and displays the stored file.

### Key words

- **Classification:** The label describing a file: invoice, contract, or other.
- **Customer folder:** A folder named `customer_<id>` that keeps files separated.
- **Duplicate:** A second file with the same name for one customer.
- **Audit entry:** A short history record saying what action happened.
- **Migration:** A safe database update that adds missing tables without deleting data.

### Try it yourself

1. Run `pytest -q` and see the file, customer, inbox, and approval tests pass.
2. Start the app with `python -m app.main`, select a customer, and use
   **Choose file**, then select a classification and click **Store file**.
3. Store the same filename twice and notice the warning; the first copy is
   kept and is not silently replaced.

### What comes next

This prototype copies files on the local computer and trusts the local
filesystem. A real product would need file-size limits, stronger permissions,
virus scanning, safe handling of unusual filenames, and a way to open or
download files. It would also need clearer success feedback and more complete
end-to-end UI testing.

## Milestone 7: final teacher check

### What we built

The complete offline learning prototype was checked from start to finish. It
can keep customers, import sample emails, use approved FAQ answers, and wait
for an owner to review a reply. It can also reject or approve a draft and store
a labeled customer file in a `customer_<id>` folder. Unsafe messages stop and
go to the owner instead of becoming drafts.

### Setup and run

From the project folder, run:

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

Run the tests with:

```text
pytest -q
```

The final check reported **45 passed in 3.18 seconds**. An offscreen startup
check ran `python -m app.main`; the process stayed alive for five seconds,
which confirms that the Qt event loop started without a startup error.

### Beginner walkthrough

1. Add and select a customer such as Anna Kovacs.
2. Import the five messages in `sample_data/emails.json`.
3. Select a message and watch its status move from **New** to **Seen** to
   **Done**.
4. Use the sample FAQ loader. It stores four approved FAQ answers.
5. Select the safe invoice message from Peter Szabo and create a draft. The
   result is **pending**, not sent.
6. Edit that draft, reject it, and confirm it leaves the pending queue.
7. Create the safe draft again and approve it. The recording mock provider
   received exactly one send call, and approval changed the draft to
   **approved**.
8. Store a sample invoice as **invoice**. The database record points inside
   `customer_<id>`, and the audit log records the action.
9. The refund/legal-threat sample escalated with no draft. It was never sent.
   Audit rows were created for the important import, FAQ, draft, review, and
   file actions.

### Key safety rules checked

- The fake model only rewrites an approved FAQ answer. It does not answer from
  its own knowledge.
- A weak FAQ match, a refund, a legal threat, anger, money details, or unclear
  text escalates instead of creating a draft.
- A draft stays pending until the owner clicks **Approve**.
- Approve is the only mock-send path. Edit and Reject do not call the email
  provider.
- SQL is kept in `app/db/`, and the user interface calls database functions.
  Providers are behind `LLMProvider` and `EmailProvider` interfaces.
- No broad `except Exception` or bare `except` was found in the application
  code reviewed. No secrets were added to the project.

### Prototype limitations and open problems

This is still a small offline teaching prototype. The email provider prints
instead of sending real mail, and the cloud model adapter is not wired into
the screen. The safety checks are simple rules and should not be trusted for
real customer communication. A real product would need user accounts and
permissions, stronger security and multilingual safety testing, duplicate-send
protection, file-size and malware checks, backups, and fuller UI end-to-end
tests. The database and local customer folders also need a proper migration and
backup plan before real use.

### What comes next

The owner can now run the app, try the walkthrough, and commit this final
milestone. The planned future work is a real Gmail provider, a local model
such as Ollama, and more CRM features such as tasks and a sales pipeline.
## PLAN2 Phase 1

### What we built

Phase 1 gives the project a safer way to plan future work. There are now separate
agents for integration work and security review, plus a clearer Lead workflow.
Milestones M08 through M14 each have a small written specification. We also
added 64 made-up email examples so reply behavior can be checked offline without
using real customer messages.

### How it works

1. The Lead reads the milestone specification and gives the work to the right
   specialist.
2. SecurityReviewer checks for secrets, privacy problems, unsafe permissions,
   and accidental sending.
3. QATeacher runs `pytest -q` and `python -m tests.run_evals`.
4. The evaluator uses a temporary database and a fake LLM, so it does not need a
   network connection or an API key.
5. It prints only totals and counts: accuracy, wrong drafts, missed escalations,
   and errors. It does not print email text or personal data.

The current evaluation output was:

```text
Total cases: 64
Exact accuracy: 64/64 (100.0%)
Wrong-draft count: 0
Missed-escalation count: 0
Errors: 0
```

### Key words

- **Agent**: A named helper with a clear job, such as integration work or
  security review.
- **Specification**: A short plan that says what a milestone should do and
  what it must not do.
- **Golden set**: Known examples with expected results, used like an answer key.
- **Evaluator**: A program that compares the real results with that answer key.
- **Offline**: Running without a network, live service, or real customer data.

### Try it yourself

1. Run `pytest -q` and check that all 53 tests pass.
2. Run `python -m tests.run_evals` and compare the five output lines with the
   results above.
3. Open `specs/m08.md` through `specs/m14.md` and compare their safety and
   out-of-scope sections.

### What comes next

Phase 1 does not add a settings screen, cloud API calls, Gmail access, masking,
attachment processing, or sending. Those are future milestones and must be
implemented one small step at a time, with the required security review and
owner approval. A real product would also need stronger privacy testing,
provider testing, audit-log checks, and careful handling of production secrets.

## Milestone 08: Safe settings for future integrations

### What we built

We added a small Settings screen for choosing how the app will work later.
The app starts in **Mock** mode, which uses pretend data and needs no secret or
network connection. **Dry-run** and **Live** are named modes for future work,
but this milestone still blocks sending. Gmail and Google Workspace are future
targets, not connected services in this prototype.

### How it works

1. The app reads setup names such as `MINI_CRM_MODE`,
   `MINI_CRM_PROVIDER`, `MINI_CRM_TEST_MAILBOX`, `MINI_CRM_ENDPOINT`,
   `MINI_CRM_MODEL`, and `MINI_CRM_API_KEY_ENV`.
2. If the mode is missing, the loader chooses Mock and the provider is `mock`.
3. Dry-run and Live require the test mailbox, endpoint, model, and API-key
   environment-variable name. Missing settings are rejected without showing a
   secret value.
4. The Settings screen lets you view and change the mode and provider names.
   It does not create a network client or make an API call.
5. The approval button stays disabled in M08, even if the approval screen is
   created directly by another part of the program.

### Key words

- **Mock**: A safe pretend mode for learning and tests.
- **Dry-run**: A future mode that may read a test mailbox and create drafts,
  but must not send.
- **Live**: A future real-service mode; it is not enabled for sending here.
- **Provider**: The named service or fake component that would do integration
  work later.
- **Environment variable**: A setup value kept outside the source code, often
  used to name where a secret can be found.

### Try it yourself

1. Run `pytest -q` to check the application tests, then run
   `python -m tests.run_evals` to check the 64 offline examples.
2. Run `python -m app.main` with `QT_QPA_PLATFORM=offscreen` in a test
   environment, or open the app normally and click the **Settings** tab.
3. In Settings, change the provider to `fake`, click **Apply settings**, and
   observe the status message. Try Dry-run without setup values and see that
   it is rejected.

### What comes next

The next integration work could define a Gmail or Google Workspace provider
behind the existing provider interface and use a separate test mailbox. A
real product would need OAuth in an approved secret store, permission review,
network and retry tests, audit records, privacy protection, and owner approval
before any sending is enabled. This prototype does none of those things and
does not make external API calls.

## Milestone 09: Cloud provider safety boundary

The cloud adapter remains behind `LLMProvider` and keeps bounded retries,
structured-response validation, and metadata-only audit records. Before M10,
customer-bearing matching and drafting fail closed and escalate instead of
calling the cloud opener; `FakeLLMProvider` remains the offline path. Cloud
endpoints must be absolute HTTPS URLs, and the pipeline converts the cloud
embedding limitation into a normal escalation rather than exposing a traceback.

The focused checks are `python -m pytest tests/test_llm_cloud.py
tests/test_reply.py tests/test_config.py`; the complete offline checks are
`python -m pytest` and `python -m tests.run_evals`.

## M09 QA check: results and lesson

### What we built

The M09 cloud adapter is an optional door to a cloud model. The normal door is
still the offline fake provider. The adapter only accepts HTTPS endpoints,
checks structured replies, retries a small number of times, and writes
metadata without storing the prompt or customer text. The offline golden set
still produced 64 correct results with no wrong drafts or missed escalations.

### How it works

1. The app uses `FakeLLMProvider` unless a cloud provider is deliberately configured.
2. The cloud adapter refuses customer fields, missing keys, unsafe URLs, and bad replies.
3. A request can retry only within its small configured limit.
4. One privacy-safe audit record is written for each eligible cloud call.
5. Sending stays behind the disabled UI gate for the normal app path.

### Key words

- **Adapter:** A small wrapper that gives one service the same shape as another.
- **Structured output:** A reply with a required JSON shape instead of free text.
- **Fail closed:** Stop safely when a check fails, rather than guessing.
- **Audit record:** A small history entry proving what happened without copying private text.

### Try it yourself

- Run `pytest` to run the 71 offline tests.
- Run `python -m tests.run_evals` to check all 64 golden examples.
- Run `python -c "import app.main; import app.providers.llm_cloud"` to check imports without a network call.

### What comes next

The QA review found one follow-up: the UI and configuration disable sending by
default, but the lower-level `approve_draft` service can still call an email
provider when called directly. Before M12, the sending service should be
disabled at that lower boundary too, and its tests should describe that rule.

## M09 follow-up QA: sending gate closed

### What we built

The service layer now blocks sending by default, not just the buttons on the
screen. A direct call to approve a draft cannot contact an email provider
unless sending is explicitly enabled. That explicit option is not exposed by
the current application configuration or UI, so sending remains unavailable
before M12. Cloud use is still optional, offline tests use the fake provider,
and no customer text or secret is stored in cloud audit details.

### How it works

1. The owner reviews a pending draft and clicks Approve in the app.
2. The service checks its sending gate before calling an email provider.
3. The default is blocked and a safe audit entry records the blocked attempt.
4. Configuration and UI keep sending disabled until the later sending milestone.

### Key words

- **Service layer:** The rule-enforcing middle part of the app, below the screen.
- **Sending gate:** A safety check that must pass before an email provider is called.
- **Opt-in:** Off by default; a deliberate future setting is needed to turn it on.
- **Audit entry:** A small record of an action, without copying private message data.

### Try it yourself

- Run `pytest` and confirm the 72 tests pass.
- Run `python -m tests.run_evals` and confirm all 64 offline examples pass.
- Run `python -m app.main` in the normal desktop environment, then check that
  the approval control cannot enable sending.

### What comes next

M10 should add personal-data masking before any real cloud request. A real
product would still need a separate test mailbox, stronger delivery
idempotency, operational monitoring, and a reviewed M12 allowlist before any
send capability is released.
# M11 Gmail read-only

- Added an injectable Gmail read-only provider. OAuth client configuration comes
  from `.env`, while OAuth tokens are serialized only into the OS keychain.
- Gmail messages are normalized before import; SQLite keeps provider/message and
  thread IDs, and attachment bytes are written to the customer file store with
  source-email links.
- Synchronization is idempotent and reports plain-language failures. Sending
  remains hard-disabled; tests and development use mocks only.
- During M11, both mock and Gmail provider send methods fail closed at the
  provider boundary. Sync audit records contain only provider, operation,
  count, outcome, and an optional error category; exception messages and
  message content are not shown to the UI or stored in these records.
- `MINI_CRM_GOOGLE_CREDENTIALS_FILE` must point to an existing readable OAuth
  client file outside this repository. Keep the file in a protected external
  location and keep OAuth tokens in the OS keychain.

## M11 QA pass

### What we built

The M11 checks confirm that the app can read a separate Gmail test mailbox
without asking for permission to send mail. The offline Mock provider imports
messages, threads, and attachments, and repeated imports do not make copies.
The checks also confirm that errors shown to the user are simple and do not
include tokens or message contents. The desktop startup check found one
prototype limitation: the checked-in older `mini_crm.db` cannot be opened by
the current schema initializer.

### How it works

1. Settings load names such as a mailbox, model, or API-key environment
   variable; secret values are not loaded into the app configuration.
2. Gmail authenticates with a read-only scope and stores OAuth tokens in the
   operating system keychain.
3. The mailbox identity is checked before Gmail lists messages.
4. The provider returns read-only message data to the import layer.
5. SQLite uses stable remote IDs to avoid duplicates and links attachments to
   both the customer and source email.
6. Mock mode reads local sample data and never uses the network.

### Key words

- **Read-only scope:** A permission that allows viewing mail but not changing
  or sending it.
- **Keychain:** The operating system's protected place for login tokens.
- **Idempotent:** Safe to run twice because the second run does not duplicate
  records.
- **Provider:** A small adapter that keeps a service such as Gmail away from
  the rest of the app.
- **Sanitized error:** A safe, general message that hides technical and secret
  details.

### Try it yourself

1. Run `pytest -q` to run the 90 offline tests.
2. Run `python -m tests.run_evals` to run the 64-case offline evaluation.
3. Run `python -m app.main` with the current checked-in database. It currently
   stops with a database-schema error; this is recorded as an open problem,
   not hidden by the QA pass.

### What comes next

The database initializer needs a small migration-order fix so an older
database can start safely. After that fix, rerun the full tests, offline
evaluation, and desktop startup check. A real product would also need a
careful migration system, real mailbox integration testing with a dedicated
test account, and operational monitoring.

## M11 QA rerun: migration-order fix confirmed

### What we built

The database startup fix now lets the app open an older database safely. The
offline Mock mailbox sync was checked against a copy of the existing database.
Running the sync twice did not add duplicate messages or files. The safety
checks still confirm read-only Gmail access, protected token storage, safe
errors, and no sending.

### How it works

1. Copy the existing database so the real local file is not changed.
2. Start the database initializer; it adds older columns before creating indexes.
3. Run the Mock sync twice and compare the stored records.
4. Start the desktop app in offscreen mode and check that it stays running.
5. Run the safety tests and offline evaluation without contacting real services.

### Key words

- **Migration:** A small database change that helps an older file use newer code.
- **Idempotent:** Safe to repeat without creating another copy of the same item.
- **Offscreen:** A mode that starts the desktop app without showing a window.
- **Read-only:** A permission that allows viewing mail but not changing or sending it.
- **Keyring:** The operating system's protected place for OAuth tokens.

### Try it yourself

1. Run `pytest -q` and check that all 91 tests pass.
2. Run `python -m tests.run_evals` and check for 64/64 exact accuracy.
3. Run `QT_QPA_PLATFORM=offscreen python -m app.main`; the prototype stays open
   until you stop it because it is a desktop application.

### What comes next

M11 now has a passing local QA rerun. A real product would still need a
carefully managed migration history, a dedicated test mailbox, and operational
monitoring before any live integration. Sending remains disabled for this
prototype.
