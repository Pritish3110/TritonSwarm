"""
USV Swarm Simulation - Comparative Evaluation Test Suite
Author: Principal Enterprise Engineer
Stage: 2 Verification Suite (Architecture Ablation Study)

Validates the impartial scorer and the comparison report generator:
1. Containment geometry is judged identically and purely geometrically for both stacks.
2. Role manning counts only vessels fit to hold the role they are holding.
3. Every failure mode the study claims to measure is actually measured: collisions, role
   vacancies, containment failure, tracking error, assignment thrash, stalls, path waste.
4. The comparison report parses scorecards and awards verdicts in the correct direction,
   including the "never happened" sentinel cases.
"""

import math
import pathlib
import sys

import pytest

_WS_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _pkg in ("usv_comparative_eval",):
    _path = str(_WS_ROOT / "src" / _pkg)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from usv_comparative_eval.compare_runs import (  # noqa: E402
    REPORT,
    parse_scorecard,
    render_report,
)
from usv_comparative_eval.failure_metrics import (  # noqa: E402
    CANONICAL_ROLES,
    D_HULL_STRIKE,
    D_SAFE,
    MAX_ACCEPTABLE_VACANCY_RUN,
    ROLE_FITNESS_FLOOR,
    AgentSample,
    SwarmScorecard,
    TargetSample,
    evaluate_containment,
    manned_roles,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ring(n, radius=20.0, center=(0.0, 0.0), role="tracker", health=1.0, target_id=0):
    """n vessels evenly spaced on a ring: textbook encirclement."""
    return [
        AgentSample(
            agent_id=i,
            x=center[0] + radius * math.cos(2.0 * math.pi * i / n),
            y=center[1] + radius * math.sin(2.0 * math.pi * i / n),
            speed=1.4, health=health, role=role, target_id=target_id,
        )
        for i in range(n)
    ]


def _clump(n, radius=20.0, arc=0.4, center=(0.0, 0.0)):
    """n vessels bunched onto one narrow arc: the baseline's tail-chase signature."""
    return [
        AgentSample(
            agent_id=i,
            x=center[0] + radius * math.cos(arc * i / max(1, n - 1)),
            y=center[1] + radius * math.sin(arc * i / max(1, n - 1)),
            speed=1.4, health=1.0, role="tracker", target_id=0,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. Containment geometry
# ---------------------------------------------------------------------------
def test_even_ring_is_judged_contained():
    verdict = evaluate_containment(TargetSample(0, 0.0, 0.0), _ring(6))
    assert verdict.participants == 6
    assert verdict.criterion_1 and verdict.criterion_2
    assert verdict.contained


def test_clumped_pursuit_is_judged_not_contained():
    """A fleet arriving as a clump on the pursuit bearing must fail encirclement."""
    verdict = evaluate_containment(TargetSample(0, 0.0, 0.0), _clump(6))
    assert verdict.participants == 6
    assert not verdict.criterion_2, "A 0.4 rad arc leaves a huge escape sector"
    assert not verdict.contained
    assert verdict.max_gap > math.pi


def test_arrested_hull_counts_as_contained():
    """The criteria prove escape is impossible; a hull at a standstill cannot escape."""
    lone_guard = [AgentSample(0, 15.0, 0.0, role="tracker")]
    moving = evaluate_containment(TargetSample(0, 0.0, 0.0, speed=1.5), lone_guard)
    arrested = evaluate_containment(
        TargetSample(0, 0.0, 0.0, speed=0.0, neutralized=True), lone_guard
    )
    assert not moving.contained
    assert arrested.contained


def test_containment_ignores_declared_assignment_and_judges_geometry():
    """
    The scorer must not penalise the baseline for bad bookkeeping. Vessels inside the ring
    band count toward containment whatever target_id they claim to be chasing.
    """
    agents = _ring(5)
    for a in agents:
        a.target_id = 7          # nonsense assignment
    verdict = evaluate_containment(TargetSample(0, 0.0, 0.0), agents)
    assert verdict.participants == 5
    assert verdict.contained


def test_support_roles_and_unfit_vessels_do_not_count_toward_containment():
    agents = _ring(6, role="scout")
    assert evaluate_containment(TargetSample(0, 0.0, 0.0), agents).participants == 0

    crippled = _ring(6, health=0.10)
    assert evaluate_containment(TargetSample(0, 0.0, 0.0), crippled).participants == 0

    # Outside the ring band, a tracker is not holding the ring.
    far = _ring(6, radius=55.0)
    assert evaluate_containment(TargetSample(0, 0.0, 0.0), far).participants == 0


# ---------------------------------------------------------------------------
# 2. Role manning
# ---------------------------------------------------------------------------
def test_manned_roles_counts_only_fit_holders():
    agents = [
        AgentSample(0, 0.0, 0.0, role="interceptor", health=1.00),
        AgentSample(1, 0.0, 0.0, role="interceptor", health=0.50),   # below the 0.70 floor
        AgentSample(2, 0.0, 0.0, role="tracker", health=0.60),
        AgentSample(3, 0.0, 0.0, role="relay", health=0.40),
        AgentSample(4, 0.0, 0.0, role="scout", health=0.10),         # below the 0.25 floor
    ]
    counts = manned_roles(agents)
    assert counts["interceptor"] == 1
    assert counts["tracker"] == 1
    assert counts["relay"] == 1
    assert counts["scout"] == 0


def test_fitness_floors_match_the_production_allocator():
    """The scorer must judge manning on the same standard the allocator enforces."""
    assert ROLE_FITNESS_FLOOR == {
        "interceptor": 0.70, "tracker": 0.55, "relay": 0.35, "scout": 0.25,
    }


# ---------------------------------------------------------------------------
# 3. Failure-mode accumulation
# ---------------------------------------------------------------------------
def _score(agents_seq, targets, ticks=10, dt=0.1, beliefs=None, num_agents=None, num_targets=1):
    card = SwarmScorecard("TEST", num_agents or len(agents_seq), num_targets)
    for _ in range(ticks):
        card.update(dt, agents_seq, targets, beliefs=beliefs)
    return card


def test_collision_detection_counts_distinct_pairs_and_dwell_time():
    agents = [
        AgentSample(0, 0.0, 0.0, role="tracker"),
        AgentSample(1, 2.5, 0.0, role="tracker"),    # 2.5 m: a hull strike
        AgentSample(2, 60.0, 0.0, role="tracker"),   # clear
    ]
    card = _score(agents, [TargetSample(0, 0.0, 0.0)], ticks=20)

    assert len(card.collision_pairs) == 1
    assert card.collision_events == 1, "A sustained breach is one entry, not one per tick"
    assert card.min_separation == pytest.approx(2.5)
    assert card.seconds_below_safe == pytest.approx(2.0)
    assert not card.zero_collision_invariant


def test_clean_formation_reports_the_invariant_held():
    card = _score(_ring(6), [TargetSample(0, 0.0, 0.0)])
    assert card.zero_collision_invariant
    assert len(card.collision_pairs) == 0
    assert card.min_separation > D_SAFE


def test_role_vacancy_is_accumulated_per_role():
    """The headline ablation metric: a role with no fit holder accrues vacancy seconds."""
    agents = [
        AgentSample(0, 0.0, 0.0, role="interceptor", health=1.0),
        AgentSample(1, 30.0, 0.0, role="tracker", health=1.0),
        # no relay and no scout is manned at all
    ]
    card = _score(agents, [TargetSample(0, 0.0, 0.0)], ticks=50)

    assert card.role_vacancy_seconds["relay"] == pytest.approx(5.0)
    assert card.role_vacancy_seconds["scout"] == pytest.approx(5.0)
    assert card.role_vacancy_seconds["interceptor"] == pytest.approx(0.0)
    assert card.any_vacancy_seconds == pytest.approx(5.0)


def test_fully_manned_fleet_accrues_no_vacancy():
    agents = [
        AgentSample(0, 0.0, 0.0, role="interceptor", health=1.0),
        AgentSample(1, 20.0, 0.0, role="tracker", health=1.0),
        AgentSample(2, 40.0, 0.0, role="relay", health=1.0),
        AgentSample(3, 60.0, 0.0, role="scout", health=1.0),
    ]
    card = _score(agents, [TargetSample(0, 0.0, 0.0)], ticks=30)
    assert card.any_vacancy_seconds == pytest.approx(0.0)
    for role in CANONICAL_ROLES:
        assert card.role_vacancy_seconds[role] == pytest.approx(0.0)


def test_containment_fraction_separates_encirclement_from_pursuit():
    encircling = _score(_ring(6), [TargetSample(0, 0.0, 0.0)], ticks=20)
    chasing = _score(_clump(6), [TargetSample(0, 0.0, 0.0)], ticks=20)

    assert encircling.containment_fraction == pytest.approx(1.0)
    assert chasing.containment_fraction == pytest.approx(0.0)


def test_track_rmse_separates_filtered_from_raw_sensing():
    """A fused estimate must score better than a raw noisy measurement."""
    target = [TargetSample(0, 100.0, 0.0)]
    agents = _ring(3)

    good = _score(agents, target, ticks=10,
                  beliefs={(0, 0): (100.3, 0.2), (1, 0): (99.8, -0.1)})
    bad = _score(agents, target, ticks=10,
                 beliefs={(0, 0): (106.0, 4.0), (1, 0): (94.0, -5.0)})

    assert good.track_rmse < 1.0
    assert bad.track_rmse > 5.0
    assert bad.worst_track_error > good.worst_track_error


def test_track_rmse_is_not_a_number_without_any_published_belief():
    card = _score(_ring(3), [TargetSample(0, 0.0, 0.0)], beliefs=None)
    assert math.isnan(card.track_rmse)
    assert card.as_dict()["track_rmse"] == -1.0, "The sentinel must read as n/a downstream"


def test_assignment_thrash_is_counted():
    card = SwarmScorecard("TEST", 1, 2)
    targets = [TargetSample(0, 0.0, 0.0), TargetSample(1, 50.0, 0.0)]
    # The vessel flip-flops between hostiles, as greedy nearest-target selection does on noise.
    for k in range(10):
        card.update(0.1, [AgentSample(0, 25.0, 0.0, role="tracker", target_id=k % 2)], targets)

    assert card.target_changes == 9
    assert card.reassignments_per_minute > 0.0


def test_stable_assignment_reports_no_thrash():
    card = SwarmScorecard("TEST", 1, 2)
    targets = [TargetSample(0, 0.0, 0.0), TargetSample(1, 50.0, 0.0)]
    for _ in range(10):
        card.update(0.1, [AgentSample(0, 25.0, 0.0, role="tracker", target_id=0)], targets)
    assert card.target_changes == 0
    assert card.role_changes == 0


def test_stall_is_detected_only_when_fit_immobile_and_far_from_the_objective():
    target = [TargetSample(0, 0.0, 0.0)]

    stalled = _score([AgentSample(0, 80.0, 0.0, speed=0.02, role="tracker")], target, ticks=30)
    assert stalled.stall_seconds == pytest.approx(3.0)

    # On the containment ring: holding position is correct behaviour, not a stall.
    on_station = _score([AgentSample(0, 20.0, 0.0, speed=0.02, role="tracker")], target, ticks=30)
    assert on_station.stall_seconds == pytest.approx(0.0)

    # A wreck is not stalled, it is destroyed.
    wreck = _score([AgentSample(0, 80.0, 0.0, speed=0.0, role="tracker", health=0.0)],
                   target, ticks=30)
    assert wreck.stall_seconds == pytest.approx(0.0)


def test_legitimate_stationkeeping_is_never_scored_as_a_stall():
    """
    Every doctrinal station must sit inside the stall exclusion, or the scorer penalises the
    architecture for holding the formation it is supposed to hold. This caught the strike
    element being flagged as deadlocked while correctly staging at 22 m awaiting containment.
    """
    target = [TargetSample(0, 0.0, 0.0, speed=1.4)]
    for radius, role, what in ((4.8, "interceptor", "capture corridor"),
                               (15.5, "tracker", "collapsed pincer ring"),
                               (20.0, "tracker", "containment ring"),
                               (22.0, "interceptor", "strike staging station"),
                               (30.0, "relay", "mesh backbone"),
                               (45.0, "scout", "wide-area picket")):
        card = _score([AgentSample(0, radius, 0.0, speed=0.02, role=role)], target, ticks=30)
        assert card.stall_seconds == pytest.approx(0.0), (
            f"A drone holding the {what} at {radius} m was scored as stalled"
        )


def test_path_efficiency_penalises_thrashing():
    """A straight run scores near 1.0; an oscillating drone burns distance for nothing."""
    straight = SwarmScorecard("TEST", 1, 1)
    for k in range(50):
        straight.update(0.1, [AgentSample(0, float(k), 0.0, role="tracker")],
                        [TargetSample(0, 200.0, 0.0)])

    oscillating = SwarmScorecard("TEST", 1, 1)
    for k in range(50):
        oscillating.update(0.1, [AgentSample(0, float(k % 2), 0.0, role="tracker")],
                           [TargetSample(0, 200.0, 0.0)])

    assert straight.path_efficiency > 0.95
    assert oscillating.path_efficiency < 0.2


def test_availability_tracks_cumulative_attrition():
    target = [TargetSample(0, 0.0, 0.0)]
    healthy = [AgentSample(i, 30.0 + 10.0 * i, 0.0, role="tracker", health=1.0) for i in range(4)]
    halved = [
        AgentSample(0, 30.0, 0.0, role="tracker", health=1.0),
        AgentSample(1, 40.0, 0.0, role="tracker", health=1.0),
        AgentSample(2, 50.0, 0.0, role="tracker", health=0.0),
        AgentSample(3, 60.0, 0.0, role="tracker", health=0.0),
    ]

    assert _score(healthy, target, ticks=10, num_agents=4).availability == pytest.approx(1.0)
    degraded = _score(halved, target, ticks=10, num_agents=4)
    assert degraded.availability == pytest.approx(0.5)
    assert degraded.worst_fit_count == 2


def test_arrest_timing_is_recorded():
    card = SwarmScorecard("TEST", 3, 2)
    agents = _ring(3)
    for k in range(40):
        t0 = TargetSample(0, 0.0, 0.0, neutralized=(k >= 10))
        t1 = TargetSample(1, 80.0, 0.0, neutralized=(k >= 30))
        card.update(0.1, agents, [t0, t1])

    assert card.peak_arrested == 2
    assert card.time_to_first_arrest == pytest.approx(1.1, abs=0.15)
    assert card.time_to_all_arrest == pytest.approx(3.1, abs=0.15)


def test_scorecard_dict_and_line_expose_every_reported_metric():
    card = _score(_ring(6), [TargetSample(0, 0.0, 0.0)], ticks=20)
    d = card.as_dict()
    line = card.scorecard_line()

    assert line.startswith("[SCORECARD] ")
    for spec in REPORT:
        assert spec.key in d, f"Report asks for '{spec.key}' which the scorecard never emits"
        assert f"{spec.key}=" in line, f"'{spec.key}' missing from the parseable line"
    # The line must round-trip through the report parser.
    assert card.render() .count("COMPARATIVE SCORECARD") == 1


# ---------------------------------------------------------------------------
# 4. Comparison report generator
# ---------------------------------------------------------------------------
def _write_log(tmp_path, name, card_line, extra_lines=2):
    p = tmp_path / name
    body = "\n".join(f"[INFO] some unrelated log line {k}" for k in range(extra_lines))
    p.write_text(body + "\n" + card_line + "\n")
    return str(p)


def test_parse_scorecard_takes_the_last_sample():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "run.log"
        p.write_text(
            "[SCORECARD] arch=X t=10.0 targets_arrested=0\n"
            "[INFO] noise\n"
            "[SCORECARD] arch=X t=20.0 targets_arrested=2\n"
        )
        parsed = parse_scorecard(str(p))
    assert parsed["t"] == "20.0"
    assert parsed["targets_arrested"] == "2"


def test_parse_scorecard_fails_loudly_when_the_run_was_too_short(tmp_path):
    p = tmp_path / "empty.log"
    p.write_text("[INFO] nothing useful here\n")
    with pytest.raises(SystemExit):
        parse_scorecard(str(p))


def test_report_awards_verdicts_in_the_correct_direction(tmp_path):
    full = parse_scorecard(_write_log(
        tmp_path, "full.log",
        "[SCORECARD] arch=FULL_ARCHITECTURE t=170.0 targets_total=3 targets_arrested=3 "
        "t_first_arrest=42.0 t_all_arrest=150.0 collisions=0 collision_events=0 min_sep=5.06 "
        "sec_below_safe=0.0 zero_collision=1 role_vacancy_s=0.0 vacancy_int_s=0.0 "
        "vacancy_trk_s=0.0 vacancy_rly_s=0.0 vacancy_sct_s=0.0 containment_frac=0.82 "
        "active_containment_frac=0.79 mission_clean=1 final_fit_count=12 "
        "track_rmse=0.61 worst_track_err=2.1 reassign_per_min=3.2 availability=0.95 "
        "worst_fit_count=9 stall_s=0.0 path_efficiency=0.71"))
    base = parse_scorecard(_write_log(
        tmp_path, "base.log",
        "[SCORECARD] arch=BASELINE_ABLATED t=170.0 targets_total=3 targets_arrested=0 "
        "t_first_arrest=-1.0 t_all_arrest=-1.0 collisions=5 collision_events=7 min_sep=1.20 "
        "sec_below_safe=14.0 zero_collision=0 role_vacancy_s=96.0 vacancy_int_s=12.0 "
        "vacancy_trk_s=0.0 vacancy_rly_s=96.0 vacancy_sct_s=80.0 containment_frac=0.0 "
        "active_containment_frac=0.01 mission_clean=0 final_fit_count=2 "
        "track_rmse=3.44 worst_track_err=18.7 reassign_per_min=210.0 availability=0.41 "
        "worst_fit_count=2 stall_s=37.0 path_efficiency=0.22"))

    report = render_report(full, base)

    assert "TRITONSWARM ARCHITECTURE ABLATION STUDY" in report
    assert "never" in report, "A mission that never succeeded must be reported as 'never'"
    # Fewer collisions is better, so the full architecture must win that row.
    collision_row = next(l for l in report.splitlines() if "Colliding drone pairs" in l)
    assert "FULL ARCH" in collision_row
    # Higher containment is better, so the full architecture must win that row too.
    containment_row = next(l for l in report.splitlines() if "of engaged time" in l)
    assert "FULL ARCH" in containment_row
    verdict_row = next(l for l in report.splitlines() if "MISSION CLEAN" in l)
    assert "FULL ARCH" in verdict_row
    # Worst separation: larger is better.
    sep_row = next(l for l in report.splitlines() if "Worst inter-drone separation" in l)
    assert "FULL ARCH" in sep_row
    assert "HEADLINE" in report


def test_report_handles_a_baseline_that_wins_a_metric(tmp_path):
    """The report must be a fair instrument, not a rigged one."""
    full = parse_scorecard(_write_log(
        tmp_path, "f2.log",
        "[SCORECARD] arch=FULL t=100.0 targets_total=1 targets_arrested=1 reassign_per_min=50.0"))
    base = parse_scorecard(_write_log(
        tmp_path, "b2.log",
        "[SCORECARD] arch=BASE t=100.0 targets_total=1 targets_arrested=1 reassign_per_min=5.0"))

    report = render_report(full, base)
    row = next(l for l in report.splitlines() if "Assignment changes" in l)
    assert "BASELINE" in row, "Fewer reassignments is better; the baseline must win this row"
    arrested_row = next(l for l in report.splitlines() if "Hostiles disabled" in l)
    assert "tie" in arrested_row


# ---------------------------------------------------------------------------
# 5. Non-destructive arrest: a ramming "capture" is a mission failure
# ---------------------------------------------------------------------------
def test_hostile_hull_strike_is_detected_and_attributed():
    """A drone inside the hull-strike radius of a hostile has rammed it."""
    agents = [
        AgentSample(0, 2.0, 0.0, role="interceptor"),   # 2.0 m: hull contact
        AgentSample(1, 20.0, 0.0, role="tracker"),      # clear
    ]
    card = _score(agents, [TargetSample(0, 0.0, 0.0)], ticks=20)

    assert card.hostile_impacts == 1
    assert card.per_target_impacts[0] == 1
    assert card.min_target_separation == pytest.approx(2.0)


def test_sustained_contact_counts_once_per_drone_hostile_pair():
    agents = [AgentSample(0, 2.0, 0.0, role="interceptor")]
    card = _score(agents, [TargetSample(0, 0.0, 0.0)], ticks=50)
    assert card.hostile_impacts == 1, "A sustained contact is one impact, not one per tick"


def test_a_rammed_hostile_is_disabled_but_not_a_clean_arrest():
    """
    The decisive metric of the study. Both stacks can stop a hostile; only one can stop it
    without hitting it. A ramming capture must not be credited as a clean arrest.
    """
    rammer = [AgentSample(0, 2.0, 0.0, role="interceptor")]
    clean = [AgentSample(0, 5.0, 0.0, role="interceptor")]

    rammed = _score(rammer, [TargetSample(0, 0.0, 0.0, neutralized=True)], ticks=20)
    captured = _score(clean, [TargetSample(0, 0.0, 0.0, neutralized=True)], ticks=20)

    assert rammed.peak_arrested == 1
    assert rammed.peak_clean_arrests == 0, "A rammed hostile was not cleanly arrested"
    assert rammed.hostile_impacts == 1

    assert captured.peak_arrested == 1
    assert captured.peak_clean_arrests == 1
    assert captured.hostile_impacts == 0


def test_capture_corridor_standoff_clears_the_hull_strike_radius():
    """
    The production CBF brakes an interceptor at 4.0 m from its assigned hostile, which must
    sit outside the 3.0 m hull-strike radius or a compliant arrest would still count as a ram.
    """
    assert D_HULL_STRIKE < 4.0, "The arrest brake boundary must clear the hull-strike radius"
    card = _score([AgentSample(0, 4.0, 0.0, role="interceptor")],
                  [TargetSample(0, 0.0, 0.0, neutralized=True)], ticks=20)
    assert card.hostile_impacts == 0
    assert card.peak_clean_arrests == 1


def test_unfit_drifting_wreck_does_not_register_a_hull_strike():
    """A dead hull drifting onto a hostile is not the swarm ramming it."""
    card = _score([AgentSample(0, 1.0, 0.0, role="tracker", health=0.0)],
                  [TargetSample(0, 0.0, 0.0)], ticks=20)
    assert card.hostile_impacts == 0


def test_report_exposes_clean_arrests_and_hull_strikes(tmp_path):
    full = parse_scorecard(_write_log(
        tmp_path, "f3.log",
        "[SCORECARD] arch=FULL t=170.0 targets_total=3 targets_arrested=3 clean_arrests=3 "
        "hostile_impacts=0 min_target_sep=4.05 collisions=0"))
    base = parse_scorecard(_write_log(
        tmp_path, "b3.log",
        "[SCORECARD] arch=BASE t=170.0 targets_total=3 targets_arrested=3 clean_arrests=0 "
        "hostile_impacts=6 min_target_sep=0.80 collisions=6"))

    report = render_report(full, base)
    clean_row = next(l for l in report.splitlines() if "CLEAN non-destructive" in l)
    assert "FULL ARCH" in clean_row
    strike_row = next(l for l in report.splitlines() if "hull strikes" in l.lower())
    assert "FULL ARCH" in strike_row
    assert "struck a hostile hull 0 times" in report


# ---------------------------------------------------------------------------
# 6. Metric fairness: containment credit and the composite verdict
# ---------------------------------------------------------------------------
def test_active_containment_excludes_arrest_credit():
    """
    Raw containment time credits an arrested hull as contained, which rewards whoever
    disabled a hostile earliest rather than whoever encircled better. The ACTIVE figure must
    measure only the time a live hostile existed.
    """
    card = SwarmScorecard("TEST", 6, 1)
    clumped = _clump(6)                    # never encircles
    # 2 s with a live hostile that is never contained, then 8 s arrested.
    for _ in range(20):
        card.update(0.1, clumped, [TargetSample(0, 0.0, 0.0, neutralized=False)])
    for _ in range(80):
        card.update(0.1, clumped, [TargetSample(0, 0.0, 0.0, neutralized=True)])

    # Raw figure is inflated by the arrest credit...
    assert card.containment_fraction == pytest.approx(0.8, abs=0.02)
    # ...but the honest encirclement figure is zero: it never actually encircled anything.
    assert card.active_containment_fraction == pytest.approx(0.0)
    assert card.live_hostile_seconds == pytest.approx(2.0)


def test_active_containment_credits_genuine_encirclement():
    card = SwarmScorecard("TEST", 6, 1)
    for _ in range(50):
        card.update(0.1, _ring(6), [TargetSample(0, 0.0, 0.0, neutralized=False)])
    assert card.active_containment_fraction == pytest.approx(1.0)


def test_mission_clean_requires_every_criterion():
    """A mission that disables the hostiles while wrecking the fleet is not a success."""
    target_live = [TargetSample(0, 0.0, 0.0, neutralized=True)]

    # All criteria met: cleanly arrested, no collisions, all roles manned.
    good = SwarmScorecard("TEST", 4, 1)
    manned = [
        AgentSample(0, 18.0, 0.0, role="interceptor", health=1.0),
        AgentSample(1, 0.0, 18.0, role="tracker", health=1.0),
        AgentSample(2, -30.0, 0.0, role="relay", health=1.0),
        AgentSample(3, 0.0, -45.0, role="scout", health=1.0),
    ]
    for _ in range(20):
        good.update(0.1, manned, target_live)
    assert good.mission_clean

    # Same arrest, but a colliding pair: not a clean mission.
    collided = SwarmScorecard("TEST", 4, 1)
    crashed = list(manned)
    crashed[1] = AgentSample(1, 20.0, 0.0, role="tracker", health=1.0)  # 2 m from USV-0
    for _ in range(20):
        collided.update(0.1, crashed, target_live)
    assert not collided.mission_clean

    # Same arrest, but the relay and scout are unmanned: not a clean mission.
    vacant = SwarmScorecard("TEST", 2, 1)
    for _ in range(20):
        vacant.update(0.1, manned[:2], target_live)
    assert not vacant.mission_clean

    # Same arrest, achieved by ramming: not a clean mission.
    rammed = SwarmScorecard("TEST", 4, 1)
    ramming = list(manned)
    ramming[0] = AgentSample(0, 2.0, 0.0, role="interceptor", health=1.0)
    for _ in range(20):
        rammed.update(0.1, ramming, target_live)
    assert not rammed.mission_clean


def test_final_fit_count_reflects_the_surviving_fleet():
    target = [TargetSample(0, 0.0, 0.0)]
    card = SwarmScorecard("TEST", 4, 1)
    agents = [AgentSample(i, 30.0 + 12.0 * i, 0.0, role="tracker", health=1.0) for i in range(4)]
    for _ in range(10):
        card.update(0.1, agents, target)
    assert card.final_fit_count == 4

    for a in agents[2:]:
        a.health = 0.0
    for _ in range(10):
        card.update(0.1, agents, target)
    assert card.final_fit_count == 2
    assert card.worst_fit_count == 2


def test_informational_rows_carry_no_verdict(tmp_path):
    """Rows that orbital containment would unfairly lose must not be scored."""
    full = parse_scorecard(_write_log(
        tmp_path, "f4.log",
        "[SCORECARD] arch=FULL t=100.0 targets_total=1 path_efficiency=0.557 "
        "containment_frac=0.43 active_containment_frac=0.81"))
    base = parse_scorecard(_write_log(
        tmp_path, "b4.log",
        "[SCORECARD] arch=BASE t=100.0 targets_total=1 path_efficiency=0.844 "
        "containment_frac=0.64 active_containment_frac=0.02"))

    report = render_report(full, base)
    directness = next(l for l in report.splitlines() if "Transit directness" in l)
    assert "BASELINE" not in directness and "FULL ARCH" not in directness.split("design)")[1]
    arrest_credit = next(l for l in report.splitlines() if "arrest credit" in l)
    assert "BASELINE" not in arrest_credit
    # The honest encirclement row must still be scored, and the full stack must win it.
    engaged = next(l for l in report.splitlines() if "of engaged time" in l)
    assert "FULL ARCH" in engaged


def test_active_containment_is_measured_per_hostile_not_as_a_conjunction():
    """
    Requiring every hostile to be encircled simultaneously needs 3+ trackers each, which a
    12-drone fleet cannot supply against 3 hostiles. The metric must therefore be per-hostile,
    or it reads 0% for every architecture and discriminates nothing.
    """
    card = SwarmScorecard("TEST", 6, 2)
    # H0 is properly encircled; H1 is 300 m away with nobody near it.
    ringed = _ring(6, center=(0.0, 0.0))
    targets = [TargetSample(0, 0.0, 0.0), TargetSample(1, 300.0, 0.0)]
    for _ in range(100):
        card.update(0.1, ringed, targets)

    # One of two live hostiles encircled for the whole run -> 50%, not 0%.
    assert card.active_containment_fraction == pytest.approx(0.5, abs=0.01)
    assert card.per_target_containment[0] == pytest.approx(10.0, abs=0.1)
    assert card.per_target_containment[1] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 7. Sustained vs accumulated vacancy, and interceptor containment credit
# ---------------------------------------------------------------------------
def test_interceptor_in_the_capture_corridor_counts_toward_containment():
    """
    An interceptor pinning a hostile sits inside the tracker ring band by design. Applying
    the tracker's lower bound to it would exclude the one vessel actually holding the hostile,
    which is why the full architecture scored ~0% encirclement before this was fixed.
    """
    agents = [
        AgentSample(0, 4.5, 0.0, role="interceptor"),            # in the capture corridor
        AgentSample(1, 20.0 * math.cos(2.0), 20.0 * math.sin(2.0), role="tracker"),
        AgentSample(2, 20.0 * math.cos(4.0), 20.0 * math.sin(4.0), role="tracker"),
    ]
    verdict = evaluate_containment(TargetSample(0, 0.0, 0.0), agents)
    assert verdict.participants == 3, "The interceptor must be counted"
    assert verdict.criterion_1 and verdict.criterion_2
    assert verdict.contained

    # A TRACKER that close is overlaying the hull and must still be excluded.
    too_close = [AgentSample(0, 4.5, 0.0, role="tracker")] + agents[1:]
    assert evaluate_containment(TargetSample(0, 0.0, 0.0), too_close).participants == 2


def test_sustained_vacancy_distinguishes_a_backfill_from_a_permanent_loss():
    """
    Total accumulated vacancy conflates "re-manned in 0.4 s, ten times over" with "unmanned
    for the rest of the mission". The no-downtime claim is about SUSTAINED vacancy.
    """
    target = [TargetSample(0, 0.0, 0.0)]

    def full_fleet(with_relay):
        out = [
            AgentSample(0, 10.0, 0.0, role="interceptor", health=1.0),
            AgentSample(1, 20.0, 0.0, role="tracker", health=1.0),
            AgentSample(2, 50.0, 0.0, role="scout", health=1.0),
        ]
        out.append(AgentSample(3, 30.0, 0.0, role="relay" if with_relay else "tracker",
                               health=1.0))
        return out

    # Architecture A: the relay role blinks out for 0.3 s at a time, then is backfilled.
    backfilled = SwarmScorecard("BACKFILL", 4, 1)
    for k in range(200):
        backfilled.update(0.1, full_fleet(with_relay=(k % 10) >= 3), target)

    # Architecture B: the relay role is lost once and never comes back.
    permanent = SwarmScorecard("PERMANENT", 4, 1)
    for k in range(200):
        permanent.update(0.1, full_fleet(with_relay=k < 20), target)

    # Both accumulate substantial total vacancy...
    assert backfilled.any_vacancy_seconds > 5.0
    assert permanent.any_vacancy_seconds > 5.0
    # ...but only one of them ever left the role down for long.
    assert backfilled.max_any_vacancy_run == pytest.approx(0.3, abs=0.05)
    assert permanent.max_any_vacancy_run == pytest.approx(18.0, abs=0.2)
    assert backfilled.max_vacancy_run["relay"] == pytest.approx(0.3, abs=0.05)


def test_mission_clean_tolerates_a_sub_watchdog_backfill_gap():
    """
    The allocator runs at 10 Hz behind a 1 Hz watchdog, so a sub-second gap between a role
    holder being destroyed and its backfill is physically unavoidable and must not fail the
    clean-mission criterion. A sustained vacancy must.
    """
    target = [TargetSample(0, 0.0, 0.0, neutralized=True)]
    base = [
        AgentSample(0, 10.0, 0.0, role="interceptor", health=1.0),
        AgentSample(1, 20.0, 0.0, role="tracker", health=1.0),
        AgentSample(2, 50.0, 0.0, role="scout", health=1.0),
    ]

    brief = SwarmScorecard("BRIEF", 4, 1)
    for k in range(200):
        agents = list(base)
        if k != 50:   # a single 0.1 s gap in relay manning
            agents.append(AgentSample(3, 30.0, 0.0, role="relay", health=1.0))
        brief.update(0.1, agents, target)
    assert brief.max_any_vacancy_run <= MAX_ACCEPTABLE_VACANCY_RUN
    assert brief.mission_clean

    sustained = SwarmScorecard("SUSTAINED", 4, 1)
    for k in range(200):
        agents = list(base)
        if k < 100:
            agents.append(AgentSample(3, 30.0, 0.0, role="relay", health=1.0))
        sustained.update(0.1, agents, target)
    assert sustained.max_any_vacancy_run > MAX_ACCEPTABLE_VACANCY_RUN
    assert not sustained.mission_clean
