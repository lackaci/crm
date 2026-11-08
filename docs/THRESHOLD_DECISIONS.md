# Reply matching threshold decision

Date: 2026-10-31

M15 swept the offline golden set with `FakeLLMProvider` at thresholds 0.60,
0.65, 0.70, 0.75, 0.80, 0.85, and 0.90. Every threshold produced the same
clean result: `WRONG_DRAFT = 0`, `WRONG_FAQ = 0`, and `MISSED_FAQ = 0`.

The configured default is therefore **0.60**, the lowest tested threshold that
keeps both safety-critical metrics at zero. Explicit thresholds remain
supported for evaluation and tuning without changing the configured default.
