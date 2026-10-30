# Mini CRM: Workflow Improvement and API Integration Plan

Right now the team can build a prototype, but nothing connects to the outside world and nothing proves the reply logic is safe. This plan fixes both. It has three parts: upgrade the team, add integrations in safe stages, and add a test set so you can trust the AI.

## Part 1: Upgrade the agent workflow

| Change | Why |
| --- | --- |
| **New agent: IntegrationDev** owns `app/providers/` (Gmail, cloud LLM, OAuth, retries, rate limits) | API work is its own skill, and it keeps AIReplyDev focused on safety logic |
| **New agent: SecurityReviewer** is read-only and checks secrets, OAuth scopes, personal-data masking, and logs | A CRM handles customer data, so a second pair of eyes is needed before anything touches real APIs |
| **A spec file per milestone** (`specs/m08.md`) with goal, files, acceptance criteria, and "out of scope" | Subagents only see their prompt, so clear specs stop them guessing |
| **A golden test set** (`tests/golden/emails.json`, 50 to 100 emails with the expected result: draft, escalate, which FAQ) | This is how you measure hallucination and wrong escalations instead of hoping |
| **An eval script** (`python -m tests.run_evals`) printing accuracy, wrong-draft count, and missed escalations | QATeacher runs it after every change to the reply logic |
| **A Definition of Done** in `copilot-instructions.md`: tests pass, evals don't regress, no secrets, audit log written, notes updated | The Lead checks this before marking a milestone done |
| **One git branch per milestone**, merged only after QA | Easy rollback when an integration misbehaves |

The Lead's loop becomes: spec, build (subagents), verify, SecurityReviewer, QATeacher, report, stop.

## Part 2: Integrations, in safe stages

First, add **three run modes** to the app, selectable in Settings:

1. **Mock** uses sample JSON and the Fake LLM. This is what you have now.
2. **Dry-run** reads real email and calls the real LLM, but only creates drafts (Gmail Drafts or the in-app queue). It sends nothing.
3. **Live** is the same, but sending is allowed after the owner clicks Approve.

Everything is built in Mock, then Dry-run, and only then Live.

| # | Milestone | What gets built | Done when |
| --- | --- | --- | --- |
| 8 | **Settings and secrets** | `.env` plus the OS keychain (`keyring`), a Settings screen with the mode switch and provider choice, config loader | No key ever appears in code, logs, or git |
| 9 | **Cloud LLM provider** | Real `LLMProvider` with timeouts, retries, structured JSON output, token and cost logging in the audit log, versioned prompts in files | Reply pipeline passes the golden set using the real API |
| 10 | **Personal-data masking** | Strip card numbers, IBANs, ID numbers and similar before any API call, restore afterwards; a "what we send" preview in debug mode | Tests prove masked data never leaves the machine |
| 11 | **Gmail read-only** | OAuth desktop login, sync inbox, threads, and attachments into the file store, using the read-only scope | Real emails appear per customer; no write permission requested yet |
| 12 | **Drafts, then sending** | First create Gmail drafts from approved replies; then add send, but only after the Approve click, with duplicate-send protection and an audit entry per send | Test sends go only to your own address (a hard-coded allowlist while testing) |
| 13 | **Attachment intelligence** | Extract text (PDF text, Tesseract OCR locally), LLM suggests type and fields, owner confirms | Invoices and contracts land in the right customer folder |
| 14 | **Hardening** | Kill switch ("pause all AI"), rate limits, error screens in plain language, backup/export, GDPR delete-customer function | You would be comfortable showing it to a pilot user |

### Later, not now

- Per-category auto-send after the owner has approved around 30 replies of that type unchanged.
- Calendar, accounting or invoicing APIs, and payments.
- The local-model swap, which only means a new `LLMProvider` and re-running the golden set.

## Decisions worth making early

- **Embeddings:** not every LLM vendor offers an embeddings API. Use a small local embedding model (for example via `fastembed`) from the start. It is free, private, and avoids a second vendor.
- **Gmail permissions:** Gmail's send and read scopes are classed as sensitive or restricted. For your own account or a small test group this is fine. Shipping to the public later requires Google's app verification, which can include a security review. Check Google's current requirements before promising a launch date. Microsoft Graph for Outlook is the alternative.
- **Polling vs. push:** start with polling every few minutes. Push notifications need a server, which doesn't fit a simple desktop app.
- **Privacy terms:** before using real customer emails with a cloud API, confirm the provider's data-retention and training terms, and sign a data processing agreement if you are covered by GDPR.
- **Cost:** log tokens per email from the first API call. Draft only emails that pass the cheap rule-based escalation check first.

## Safety rules for the integration phase

- Develop against a **separate test mailbox**, never real customers.
- Sending stays disabled until milestone 12, and even then it is allowlisted to your own address until the golden set passes.
- Every API call and every send is written to the audit log with timestamp, model, prompt version, and FAQ id.
- OAuth tokens go in the keychain only, never in the database or files.

## What to change in the repo

1. Add `integration-dev.agent.md` and `security-reviewer.agent.md`.
2. Update `lead.agent.md` with the spec, verify, security, and QA loop.
3. Extend `copilot-instructions.md` with the three modes, the Definition of Done, and the rules above.
4. Add `specs/m08.md` to `m14.md`, the golden test set, the eval script, and the updated `PLAN.md`.

## Open question

Do your target users use Gmail (Google Workspace) or Outlook/Microsoft 365? The answer decides the email integration in milestones 11 and 12.

## PLAN2 Phase 1: workflow and offline evaluation foundation [x]

Status legend: [ ] todo, [~] in progress, [x] done

1. [x] Add IntegrationDev and SecurityReviewer agent definitions.
2. [x] Update the Lead workflow and repository Definition of Done for specs, security, evals, and notes.
3. [x] Add milestone specs for M08-M14 with scope, acceptance criteria, and out-of-scope boundaries.
4. [x] Add an offline golden email dataset with expected draft/escalation outcomes.
5. [x] Add `tests.run_evals` to report accuracy, wrong drafts, and missed escalations.
6. [x] Run the complete test suite and offline evaluations.
7. [x] Have QATeacher review the foundation and document it in `LEARNING_NOTES.md`.

Phase 1 is done when the new workflow files exist, the golden evaluation runs offline,
the existing test suite remains green, and the safety/integration work has not started.

## M08: Settings, secrets, and modes [x]

1. [x] Add a configuration loader with Mock, Dry-run, and Live mode validation.
2. [x] Add provider-selection configuration without implementing Gmail, OAuth, or cloud calls.
3. [x] Add a settings screen that displays and changes mode/provider, with sending disabled.
4. [x] Add `.env.example`/setup documentation containing names only, never secret values.
5. [x] Add focused tests for defaults, invalid modes, missing non-Mock settings, and UI state.
6. [x] Run tests and the offline evaluator, then complete SecurityReviewer and QATeacher review.

Acceptance: Mock works without secrets or network access; invalid non-Mock configuration is
rejected; settings are visible and editable; all send controls remain disabled in M08.
Out of scope: Gmail, OAuth, cloud LLM calls, keyring integration, sending, and new dependencies.

## M09: Cloud LLM provider [x]

1. [x] Define the cloud-provider request/response boundary and versioned prompt files.
2. [x] Implement bounded timeout/retry handling behind `LLMProvider`, without vendor SDK imports.
3. [x] Validate structured JSON responses and fail closed on malformed output.
4. [x] Add privacy-preserving provider-call audit records for model, prompt version, FAQ id, tokens, and cost when supplied.
5. [x] Add transport/provider tests using fakes only; do not call a real API.
6. [x] Run the full suite and golden evaluator, then complete SecurityReviewer and QATeacher review.

Acceptance: FakeLLM remains offline; the cloud adapter is opt-in, bounded, and testable;
structured responses are validated; provider calls have audit metadata; sending remains disabled.
Out of scope: Gmail/OAuth, personal-data masking, real customer data, attachments, and sending.

## M10: Personal-data masking [x]

1. [x] Define the masking boundary and placeholder format.
2. [x] Mask sensitive values before every external model call.
3. [x] Restore only unambiguous placeholders in safe responses.
4. [x] Escalate ambiguous or failed restoration without creating a draft.
5. [x] Add a debug-only masked preview without storing raw values.
6. [x] Add focused privacy tests and rerun the golden evaluator.

Acceptance: recognized sensitive values never reach an external provider; safe
responses restore only matching placeholders; ambiguous restoration escalates;
the preview and audit records never contain raw sensitive values.
Out of scope: Gmail/OAuth, attachment extraction, provider-specific masking,
automatic sending, and real customer API calls.

## M11: Gmail read-only [x]

1. [x] Add `.env` loading and Gmail read-only configuration placeholders.
2. [x] Implement OAuth desktop login with OS keychain token storage.
3. [x] Add a Gmail provider behind `EmailProvider` with read-only scopes only.
4. [x] Import messages, threads, and attachments without duplicate records.
5. [x] Connect imported data to customers and the Inbox/file views.
6. [x] Add offline provider tests and a clear sync-error path.
7. [x] Run SecurityReviewer and QATeacher before marking M11 complete.

Acceptance: Mock remains offline; Gmail requests read-only permission; OAuth
tokens never enter SQLite, `.env`, logs, or Git; sync is idempotent and
attachment links are preserved; sending remains disabled.
Out of scope: Gmail drafts, sending, OCR, push notifications, and real
customer mail.
