#!/usr/bin/env python3
"""Rank CAR-bench train tasks by cross-trial consistency.

Reads a multi-trial evaluator output JSON and, per task, counts how many of the
N trials passed (reward == 1.0). Tasks are then bucketed:

  - deterministic-pass : passed every trial            (N/N)
  - flaky              : passed some but not all trials (1..N-1 of N)
  - deterministic-fail : failed every trial            (0/N)

The flaky bucket is single-trial noise: do NOT write task-specific workarounds
for those without more trials. The deterministic-fail bucket is the real fix
backlog. Pass^N (deployment metric) == deterministic-pass count.

Usage:
  uv run python scripts/rank_consistency.py <output.json> [--split base] [--md out.md]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def _natural_key(task_id: str):
    match = re.search(r"(\d+)$", task_id)
    return (task_id.rsplit("_", 1)[0], int(match.group(1)) if match else 0)


def collect(output_json: dict) -> dict[str, dict[str, dict[int, float]]]:
    """Return {split: {task_id: {trial: reward}}} using the richest result entry."""

    results = output_json.get("results") or []
    # The last result entry carries every split that was evaluated.
    best = max(
        results,
        key=lambda r: len((r or {}).get("detailed_results_by_split") or {}),
        default={},
    )
    detailed = best.get("detailed_results_by_split") or {}
    out: dict[str, dict[str, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    for split, records in detailed.items():
        for record in records or []:
            task_id = record.get("task_id")
            if not task_id:
                continue
            trial = int(record.get("trial") or 0)
            out[split][task_id][trial] = float(record.get("reward") or 0.0)
    return out


def classify(per_trial: dict[int, float]) -> tuple[int, int]:
    passes = sum(1 for reward in per_trial.values() if reward >= 1.0)
    total = len(per_trial)
    return passes, total


def build_report(data: dict, only_split: str | None) -> str:
    lines: list[str] = []
    splits = [s for s in data if (only_split is None or s == only_split)]
    grand = {"pass": 0, "flaky": 0, "fail": 0, "tasks": 0}
    for split in sorted(splits):
        tasks = data[split]
        buckets = {"pass": [], "flaky": [], "fail": []}
        for task_id in sorted(tasks, key=_natural_key):
            passes, total = classify(tasks[task_id])
            if total == 0:
                continue
            if passes == total:
                buckets["pass"].append((task_id, passes, total))
            elif passes == 0:
                buckets["fail"].append((task_id, passes, total))
            else:
                buckets["flaky"].append((task_id, passes, total))
        n = sum(len(v) for v in buckets.values())
        grand["pass"] += len(buckets["pass"])
        grand["flaky"] += len(buckets["flaky"])
        grand["fail"] += len(buckets["fail"])
        grand["tasks"] += n
        lines.append(f"## {split}  ({n} tasks)")
        lines.append("")
        lines.append(
            f"- Pass^N (all trials): **{len(buckets['pass'])}/{n}**"
            f"  ·  flaky: **{len(buckets['flaky'])}**"
            f"  ·  deterministic-fail: **{len(buckets['fail'])}**"
        )
        lines.append("")
        if buckets["fail"]:
            ids = ", ".join(f"`{t}`" for t, _, _ in buckets["fail"])
            lines.append(f"**Deterministic-fail (0/N) — real backlog:** {ids}")
            lines.append("")
        if buckets["flaky"]:
            ids = ", ".join(f"`{t}` ({p}/{tot})" for t, p, tot in buckets["flaky"])
            lines.append(f"**Flaky (noise — needs more trials, not workarounds):** {ids}")
            lines.append("")
    header = [
        "# Cross-trial consistency ranking",
        "",
        f"Totals across reported splits: {grand['tasks']} tasks · "
        f"Pass^N {grand['pass']} · flaky {grand['flaky']} · "
        f"deterministic-fail {grand['fail']}.",
        "",
    ]
    return "\n".join(header + lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_json", help="Evaluator output JSON from a multi-trial run")
    parser.add_argument("--split", default=None, help="Restrict to one split")
    parser.add_argument("--md", default=None, help="Write the markdown report to this path")
    args = parser.parse_args()

    data = collect(json.loads(Path(args.output_json).read_text()))
    report = build_report(data, args.split)
    print(report)
    if args.md:
        Path(args.md).write_text(report)
        print(f"\nWrote {args.md}")


if __name__ == "__main__":
    main()
