"""Sweep reply-match thresholds using the deterministic offline golden eval.

This script deliberately calls the evaluator's Mock/FakeLLM path.  It does
not load cloud credentials, change the configured environment threshold, or
write the evaluator's ``last_run.json`` report.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``python scripts/threshold_sweep.py`` work from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.run_evals import evaluate  # noqa: E402


THRESHOLDS = tuple(0.60 + index * 0.05 for index in range(7))


def run_sweep() -> list[tuple[float, dict[str, object]]]:
    """Return metrics for every required threshold without changing config."""
    results: list[tuple[float, dict[str, object]]] = []
    for threshold in THRESHOLDS:
        # Explicit threshold keeps each run independent of the environment.
        metrics = evaluate("mock", threshold=threshold)
        if metrics["errors"]:
            raise RuntimeError(
                f"offline evaluation failed at threshold {threshold:.2f}"
            )
        results.append((threshold, metrics))
    return results


def main() -> int:
    try:
        results = run_sweep()
    except Exception as exc:
        print(f"Threshold sweep failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    for threshold, metrics in results:
        print(
            f"{threshold:.2f} "
            f"WRONG_DRAFT: {metrics['wrong_draft']} "
            f"WRONG_FAQ: {metrics['wrong_faq']} "
            f"MISSED_FAQ: {metrics['missed_faq']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
