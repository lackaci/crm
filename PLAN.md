# PLAN

Status legend: [ ] todo, [~] in progress, [x] done

## Milestones
- [~] 1. Skeleton: folder structure, interfaces, empty window opens, schema.sql
- [ ] 2. Customers: add / edit / list in SQLite + UI table
- [ ] 3. Mock inbox: load sample_data/emails.json, show emails per customer
- [ ] 4. FAQ + reply pipeline: match -> draft -> guardrails (+ tests, FakeLLM)
- [ ] 5. Approval queue UI: Approve / Edit / Reject, audit log (approve = mark "sent")
- [ ] 6. Files: add a file, classify (invoice/contract/other), store under customer folder
- [ ] 7. Teacher pass: full walkthrough in LEARNING_NOTES.md

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
