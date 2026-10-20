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
