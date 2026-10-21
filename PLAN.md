# PLAN

Status legend: [ ] todo, [~] in progress, [x] done

## Milestones
- [x] 1. Skeleton: folder structure, interfaces, empty window opens, schema.sql
- [x] 2. Customers: add / edit / list in SQLite + UI table
- [x] 3. Mock inbox: load sample_data/emails.json, show emails per customer
- [x] 4. FAQ + reply pipeline: match -> draft -> guardrails (+ tests, FakeLLM)
- [x] 5. Approval queue UI: Approve / Edit / Reject, audit log (approve = mark "sent")
- [x] 6. Files: add a file, classify (invoice/contract/other), store under customer folder
- [x] 7. Teacher pass: full walkthrough in LEARNING_NOTES.md

## Later (not now)
Real Gmail, local model (Ollama), auto-send per category, tasks/pipeline, installer.

## Rules
After each milestone: QATeacher verifies, owner runs the app, owner commits.

## Milestone 1 breakdown
1. **Architect** creates the agreed `app/` and `tests/` folder structure.
2. **Architect** adds the `LLMProvider` and `EmailProvider` interfaces.
3. **Architect** adds deterministic offline/mock providers for later testing.
4. **Architect** writes the initial SQLite schema and database-related documentation.
5. **Architect** adds the empty PySide6 entry point, requirements, and gitignore.
6. **QATeacher** runs the available checks, verifies the skeleton against the project rules,
   and appends beginner-friendly notes to `LEARNING_NOTES.md`.

Milestone 1 is done when the empty app opens as **Mini CRM**, the required foundation files
exist, and QATeacher has verified the result. After that, the owner runs the app and commits
the milestone before starting Milestone 2.

## Milestone 3 breakdown
1. **BackendDev** adds email database models and helpers for importing and listing emails by customer.
2. **BackendDev** adds tests for email persistence, customer matching, and duplicate-safe imports.
3. **UIDev** adds the inbox/customer email view and wires it to database helpers and the mock provider.
4. **QATeacher** runs the checks, verifies the inbox flow, and appends beginner-friendly notes to `LEARNING_NOTES.md`.

Milestone 3 is done when sample emails can be imported offline, are associated with customers,
and the application shows a selected customer's messages without SQL in the UI.

## Milestone 4 breakdown
1. **BackendDev** adds FAQ and draft persistence helpers, including approved-answer storage and draft status updates.
2. **AIReplyDev** implements deterministic FAQ matching, safety guardrails, and FAQ-only reply drafting through `LLMProvider`.
3. **QATeacher** runs focused and complete tests, verifies every escalation rule, and appends beginner-friendly notes to `LEARNING_NOTES.md`.

Milestone 4 is done when only approved FAQ answers can become drafts, unsafe or unclear
messages are escalated, the FakeLLM works offline, and the behavior is covered by tests.

## Milestone 5 breakdown
1. **BackendDev** adds approval actions and a safe mock send operation with audit logging.
2. **UIDev** adds an approval queue showing pending drafts with Edit, Approve, and Reject actions.
3. **QATeacher** runs the complete suite, verifies that approval is required before mock sending, and appends beginner-friendly notes to `LEARNING_NOTES.md`.

Milestone 5 is done when pending drafts can be reviewed, edited, approved, or rejected,
approval is the only action that marks a draft as sent through the mock provider, and every
queue action is recorded in the audit log.

## Milestone 6 breakdown
1. **BackendDev** adds safe file storage, classification, and database helpers for customer files.
2. **UIDev** adds a customer file panel for choosing, classifying, and storing files without SQL in the UI.
3. **QATeacher** runs the complete suite, verifies customer-folder storage and classification, and appends beginner-friendly notes to `LEARNING_NOTES.md`.

Milestone 6 is done when an owner can select a customer, add a file, classify it as
invoice, contract, or other, and find it stored under that customer's folder.

## Milestone 7 breakdown
1. **QATeacher** runs the complete test suite and application smoke check.
2. **QATeacher** verifies the end-to-end beginner walkthrough: customer, inbox, statuses, safe drafts, approval, and files.
3. **QATeacher** reviews architecture and safety rules, then appends the final walkthrough and open limitations to `LEARNING_NOTES.md`.

Milestone 7 is done when the full offline prototype is verified, the walkthrough is
documented, and the owner has clear commands for running and testing the application.
