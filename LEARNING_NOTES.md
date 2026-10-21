# Learning notes
(QATeacher appends a short "how this works" section after each milestone.)

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
