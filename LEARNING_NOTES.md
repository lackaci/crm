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
