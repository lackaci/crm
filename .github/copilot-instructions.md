# Project: Mini CRM (learning prototype)

This is a **learning prototype**, not production software. Prefer simple, readable code
with comments over clever or optimized code. The owner is a beginner and wants to understand it.

## Stack
- Python 3.11+, PySide6 (desktop UI), SQLite (sqlite3 module, no ORM), pytest.
- No web frameworks. No extra dependencies without asking.

## Architecture rules
- All external things go behind interfaces in `app/providers/`:
  `LLMProvider` (cloud now, local model later) and `EmailProvider` (mock JSON now, Gmail later).
- Business code never imports a vendor SDK directly. Only `llm_cloud.py` may.
- A `FakeLLMProvider` must always exist so everything is testable offline.
- UI code (`app/ui/`) never writes SQL. It calls functions in `app/db/`.

## AI safety rules (most important)
- The model NEVER answers from its own knowledge. It may only rewrite an owner-approved FAQ answer.
- No FAQ match above the confidence threshold -> escalate to the owner, do not draft.
- Always escalate: refunds, legal threats, anger/complaints, money amounts, anything unclear.
- Nothing is ever "sent" without an explicit owner Approve click. Every action goes in the audit log.
- Secrets (API keys, tokens) never go in code or git. Use environment variables or `.env` (gitignored).

## Run modes and integration safety

Build and test in this order:
- **Mock:** use sample JSON, the fake LLM, and no external services. This is
  the default mode for learning and offline tests.
- **Dry-run:** read from the separate test mailbox and, only when the milestone
  allows it, call a real LLM. Create drafts only; never send.
- **Live:** real services may be used only after the later sending milestone,
  owner approval, and all required reviews. Sending stays disabled until then.

Never use real customer mail while developing. Use a separate test mailbox.
OAuth tokens belong in the OS keychain or another approved secret store, not in
the database, files, logs, examples, or git. Integrations must remain behind
provider interfaces.

## Definition of Done

A milestone is done only when:
- The relevant PLAN/spec was read and the work matches its acceptance criteria
  and out-of-scope list.
- Tests pass, and offline evals pass without regression when they exist.
- No secrets or real customer data are committed or printed.
- Required actions have audit-log evidence, including provider calls and sends
  in later integration milestones.
- `LEARNING_NOTES.md` explains what changed and how to try it.
- SecurityReviewer has passed before QATeacher, and the Lead has reported the
  exact files, test results, and open problems.

## Working style
- Small steps. Finish one milestone, then stop and wait for the owner.
- Explain what you did in 3-6 plain sentences at the end of each task.
- Read `PLAN.md` and `ARCHITECTURE.md` before starting work.
