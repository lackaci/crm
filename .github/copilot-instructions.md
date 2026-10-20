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

## Working style
- Small steps. Finish one milestone, then stop and wait for the owner.
- Explain what you did in 3-6 plain sentences at the end of each task.
- Read `PLAN.md` and `ARCHITECTURE.md` before starting work.
