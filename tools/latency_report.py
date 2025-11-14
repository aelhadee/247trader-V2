#!/usr/bin/env python3
"""Utility to visualize trading-loop stage latencies from the audit log."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List


def _percentile(samples: List[float], pct: float) -> float:
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    # statistics.quantiles returns exclusive quantiles; fall back to manual selection for robustness
    k = max(0, min(len(samples) - 1, int(round(pct / 100.0 * (len(samples) - 1)))))
    return sorted(samples)[k]


def _summarize(values: Iterable[float]) -> Dict[str, float]:
    samples = list(values)
    if not samples:
        return {"count": 0, "avg": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(samples),
        "avg": statistics.fmean(samples),
        "p95": _percentile(samples, 95),
        "max": max(samples),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-file",
        default="logs/247trader-v2_audit.jsonl",
        help="Path to audit JSONL file (defaults to logs/247trader-v2_audit.jsonl)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Show the slowest N stages by average duration",
    )
    args = parser.parse_args(argv)

    audit_path = Path(args.audit_file)
    if not audit_path.exists():
        print(f"Audit file not found: {audit_path}", file=sys.stderr)
        return 1

    stage_samples: Dict[str, List[float]] = defaultdict(list)
    total_durations: List[float] = []

    with audit_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            stage_latencies = entry.get("stage_latencies") or {}
            for stage, duration in stage_latencies.items():
                try:
                    stage_samples[stage].append(float(duration))
                except (TypeError, ValueError):
                    continue

            total_duration = entry.get("latency_seconds") or entry.get("cycle_duration")
            if total_duration is not None:
                try:
                    total_durations.append(float(total_duration))
                except (TypeError, ValueError):
                    pass
            elif stage_latencies:
                try:
                    total_durations.append(
                        sum(float(v) for v in stage_latencies.values() if isinstance(v, (int, float)))
                    )
                except (TypeError, ValueError):
                    pass

    if not stage_samples:
        print("No stage latency data found. Ensure stage_latencies is enabled in the audit log.", file=sys.stderr)
        return 1

    stage_summary = {
        stage: _summarize(values)
        for stage, values in stage_samples.items()
    }

    print("=== Stage Latency Summary ===")
    for stage, stats in sorted(
        stage_summary.items(), key=lambda item: item[1]["avg"], reverse=True
    )[: args.top]:
        print(
            f"{stage:24s} avg={stats['avg']:.3f}s p95={stats['p95']:.3f}s "
            f"max={stats['max']:.3f}s samples={int(stats['count'])}"
        )

    if total_durations:
        totals = _summarize(total_durations)
        print("\n=== Total Cycle Duration ===")
        print(
            f"avg={totals['avg']:.3f}s p95={totals['p95']:.3f}s "
            f"max={totals['max']:.3f}s samples={int(totals['count'])}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
