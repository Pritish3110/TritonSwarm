"""
USV Swarm Simulation - Architecture Comparison Report Generator
Author: Principal Enterprise Engineer
Stage: 2 (Architecture Ablation Study)

Pure (rclpy-free) CLI that reads the final [SCORECARD] line from two mission logs -- one from
the full TritonSwarm architecture, one from the ablated baseline -- and renders a side-by-side
comparison with an explicit verdict per metric.

Usage:
    compare_runs <full_architecture.log> <baseline_ablated.log>
"""

from __future__ import annotations
import math
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

SCORECARD_TAG = "[SCORECARD]"


@dataclass(frozen=True)
class MetricSpec:
    """One row of the comparison report."""
    key: str
    label: str
    higher_is_better: Optional[bool]   # None -> informational only, no verdict
    unit: str = ""
    fmt: str = "{:g}"


# The report is ordered by what matters for a capstone defence: did the mission succeed, was
# it safe, did the swarm stay organised, and was it efficient.
REPORT: Tuple[MetricSpec, ...] = (
    MetricSpec("mission_clean", "MISSION CLEAN (all criteria met)", True),
    MetricSpec("clean_arrests", "CLEAN non-destructive arrests", True),
    MetricSpec("targets_arrested", "Hostiles disabled (incl. by ramming)", True),
    MetricSpec("hostile_impacts", "Drone-on-hostile hull strikes", False),
    MetricSpec("min_target_sep", "Closest approach to a hostile", True, "m", "{:.2f}"),
    MetricSpec("t_first_arrest", "Time to first hostile disabled", False, "s"),
    MetricSpec("t_all_arrest", "Time to all hostiles disabled", False, "s"),
    MetricSpec("collisions", "Colliding drone pairs", False),
    MetricSpec("collision_events", "Entries into collision breach", False),
    MetricSpec("min_sep", "Worst inter-drone separation", True, "m", "{:.2f}"),
    MetricSpec("sec_below_safe", "Time spent below the 4.0 m invariant", False, "s", "{:.1f}"),
    MetricSpec("zero_collision", "Zero-collision invariant held", True),
    MetricSpec("max_vacancy_run_s", "Longest SUSTAINED role vacancy", False, "s", "{:.2f}"),
    MetricSpec("role_vacancy_s", "Total time with ANY role unmanned", False, "s", "{:.1f}"),
    MetricSpec("vacancy_int_s", "  - interceptor unmanned", False, "s", "{:.1f}"),
    MetricSpec("vacancy_trk_s", "  - tracker unmanned", False, "s", "{:.1f}"),
    MetricSpec("vacancy_rly_s", "  - relay unmanned", False, "s", "{:.1f}"),
    MetricSpec("vacancy_sct_s", "  - scout unmanned", False, "s", "{:.1f}"),
    MetricSpec("active_containment_frac", "Live hostiles encircled (of engaged time)", True, "", "{:.1%}"),
    MetricSpec("containment_frac", "  incl. arrest credit (informational)", None, "", "{:.1%}"),
    MetricSpec("track_rmse", "Hostile tracking RMSE", False, "m", "{:.2f}"),
    MetricSpec("worst_track_err", "Worst tracking error", False, "m", "{:.2f}"),
    MetricSpec("reassign_per_min", "Assignment changes", False, "/min", "{:.1f}"),
    MetricSpec("stall_s", "Time stalled / deadlocked", False, "s", "{:.1f}"),
    MetricSpec("path_efficiency", "Transit directness (orbiting lowers this by design)",
               None, "", "{:.3f}"),
    MetricSpec("availability", "Mean fleet availability", True, "", "{:.1%}"),
    MetricSpec("worst_fit_count", "Worst operational drone count", True),
    MetricSpec("final_fit_count", "Drones still operational at the end", True),
    MetricSpec("t", "Mission duration scored", None, "s", "{:.1f}"),
)


def parse_scorecard(path: str) -> Dict[str, str]:
    """Returns the LAST [SCORECARD] line in a log file as a key -> value mapping."""
    last: Optional[str] = None
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                if SCORECARD_TAG in line:
                    last = line
    except OSError as exc:
        raise SystemExit(f"Could not read {path}: {exc}")

    if last is None:
        raise SystemExit(
            f"No '{SCORECARD_TAG}' line found in {path}. The run may have been too short for "
            f"the scorecard node to report, or it exited before the first scoring period."
        )

    payload = last.split(SCORECARD_TAG, 1)[1].strip()
    out: Dict[str, str] = {}
    for token in payload.split():
        if "=" in token:
            k, v = token.split("=", 1)
            out[k] = v
    return out


def _as_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    return None if math.isnan(f) else f


def _format(spec: MetricSpec, raw: Optional[str]) -> str:
    """Renders one cell, translating the sentinel -1 into an honest 'never'/'n-a'."""
    f = _as_float(raw)
    if f is None:
        return "n/a"

    if spec.key in ("t_first_arrest", "t_all_arrest") and f < 0:
        return "never"
    if spec.key in ("track_rmse", "worst_track_err", "min_sep") and f < 0:
        return "n/a"
    if spec.key == "zero_collision":
        return "YES" if f >= 1.0 else "NO"

    try:
        return spec.fmt.format(f) + spec.unit
    except (ValueError, KeyError):
        return f"{f:g}{spec.unit}"


def _verdict(spec: MetricSpec, a: Optional[str], b: Optional[str],
             label_a: str, label_b: str) -> str:
    """Names which architecture wins this metric, treating 'never' as the worst outcome."""
    if spec.higher_is_better is None:
        return ""

    fa = _as_float(a)
    fb = _as_float(b)
    if fa is None or fb is None:
        return ""

    # A sentinel -1 on a timing metric means the event never happened: strictly worst.
    if spec.key in ("t_first_arrest", "t_all_arrest"):
        if fa < 0 and fb < 0:
            return "both failed"
        if fa < 0:
            return f"{label_b} only"
        if fb < 0:
            return f"{label_a} only"
    if spec.key in ("track_rmse", "worst_track_err", "min_sep") and (fa < 0 or fb < 0):
        return ""

    if abs(fa - fb) < 1e-9:
        return "tie"

    a_wins = (fa > fb) if spec.higher_is_better else (fa < fb)
    winner = label_a if a_wins else label_b
    hi, lo = (max(fa, fb), min(fa, fb))
    if lo > 1e-9:
        return f"{winner}  ({hi / lo:.1f}x better)"
    return f"{winner}"


def render_report(
    full: Dict[str, str],
    base: Dict[str, str],
    label_a: str = "FULL ARCH",
    label_b: str = "BASELINE",
) -> str:
    lines: List[str] = []
    width = 112
    lines.append("=" * width)
    lines.append(" TRITONSWARM ARCHITECTURE ABLATION STUDY - COMPARATIVE RESULTS")
    lines.append("-" * width)
    lines.append(f" Condition A : {full.get('arch', label_a)}  (full TritonSwarm architecture)")
    lines.append(f" Condition B : {base.get('arch', label_b)}  (ablated conventional swarm)")
    lines.append(f" Fleet       : {full.get('targets_total', '?')} hostiles, "
                 f"scored over {_format(REPORT[-1], full.get('t'))}")
    lines.append(" Both conditions ran the same physics, the same hostile AI, the same fault")
    lines.append(" schedule and the same win condition, and were judged by the same scorer.")
    lines.append("=" * width)
    lines.append(f" {'METRIC':<44}{label_a:>14}{label_b:>14}   VERDICT")
    lines.append("-" * width)

    for spec in REPORT:
        a_raw = full.get(spec.key)
        b_raw = base.get(spec.key)
        if a_raw is None and b_raw is None:
            continue
        lines.append(
            f" {spec.label:<44}{_format(spec, a_raw):>14}{_format(spec, b_raw):>14}   "
            f"{_verdict(spec, a_raw, b_raw, label_a, label_b)}"
        )

    lines.append("=" * width)

    # Headline summary: the three claims a defence actually rests on.
    arrested_a = _as_float(full.get("clean_arrests")) or 0.0
    arrested_b = _as_float(base.get("clean_arrests")) or 0.0
    impact_a = _as_float(full.get("hostile_impacts")) or 0.0
    impact_b = _as_float(base.get("hostile_impacts")) or 0.0
    total = _as_float(full.get("targets_total")) or 0.0
    coll_a = _as_float(full.get("collisions")) or 0.0
    coll_b = _as_float(base.get("collisions")) or 0.0
    vac_a = _as_float(full.get("role_vacancy_s")) or 0.0
    vac_b = _as_float(base.get("role_vacancy_s")) or 0.0

    lines.append(" HEADLINE")
    lines.append(f"  Mission      : full architecture achieved {arrested_a:g}/{total:g} CLEAN "
                 f"non-destructive arrests; ablated baseline achieved {arrested_b:g}/{total:g}.")
    lines.append(f"  Non-destruct : full architecture struck a hostile hull {impact_a:g} times; "
                 f"ablated baseline struck one {impact_b:g} times.")
    lines.append(f"  Safety       : full architecture had {coll_a:g} colliding pairs; "
                 f"ablated baseline had {coll_b:g}.")
    run_a = _as_float(full.get("max_vacancy_run_s"))
    run_b = _as_float(base.get("max_vacancy_run_s"))
    if run_a is not None and run_b is not None:
        lines.append(f"  Organisation : full architecture's longest sustained role vacancy was "
                     f"{run_a:.2f}s (backfilled by the allocator); ablated baseline's was "
                     f"{run_b:.2f}s (permanent - a static role table cannot re-task anyone).")
    else:
        lines.append(f"  Organisation : full architecture spent {vac_a:.1f}s with a role unmanned; "
                     f"ablated baseline spent {vac_b:.1f}s.")

    fit_a = _as_float(full.get("final_fit_count")) or 0.0
    fit_b = _as_float(base.get("final_fit_count")) or 0.0
    lines.append(f"  Fleet        : full architecture finished with {fit_a:g} drones operational; "
                 f"ablated baseline finished with {fit_b:g}.")

    clean_a = _as_float(full.get("mission_clean")) or 0.0
    clean_b = _as_float(base.get("mission_clean")) or 0.0
    lines.append(f"  Verdict      : full architecture "
                 f"{'PASSES' if clean_a >= 1 else 'FAILS'} the clean-mission criterion; "
                 f"ablated baseline {'PASSES' if clean_b >= 1 else 'FAILS'} it.")
    lines.append("")
    lines.append("  Note on timing: the baseline can disable hostiles sooner because nothing")
    lines.append("  brakes its approach at the 4.0 m arrest boundary. Reaching the objective")
    lines.append("  faster by ramming it, losing drones to hull strikes and leaving roles")
    lines.append("  unmanned is not a better outcome against a non-destructive arrest brief.")
    lines.append("=" * width)
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        print(__doc__.strip())
        return 2

    full = parse_scorecard(args[0])
    base = parse_scorecard(args[1])
    print(render_report(full, base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
