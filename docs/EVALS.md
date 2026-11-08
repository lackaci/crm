# Reading the reply evaluation

Run `python -m tests.run_evals` from the repository root. It uses the FakeLLM,
so it is offline, repeatable, and does not need an API key.

`Accuracy` is the number of cases whose status (and FAQ id for drafts) matched
the expected result. `WRONG_DRAFT` is the safety-critical number of risky
messages that received a draft; it must stay at zero. `WRONG_FAQ` means a
draft used a different approved FAQ, and `MISSED_FAQ` means a safe FAQ case
was escalated instead. Every non-matching case id is printed, and the complete
metrics are saved in `tests/golden/last_run.json`.
