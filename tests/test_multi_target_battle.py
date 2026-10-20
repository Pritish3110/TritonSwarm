"""
USV Swarm Simulation - Multi-Target Battle Doctrine Test Suite
Author: Principal Enterprise Engineer
Stage: 2 Verification Suite (Multi-Target)

Validates the rclpy-free doctrine modules that drive the scaled battle simulation:
1. Tactical establishment completeness: no canonical role is ever left without a slot.
2. Role continuity under hard kills: relay, scout, interceptor and tracker losses are
   backfilled by the surviving fleet, and the formation re-forms around the replacement.
3. Doctrinal fleet shapes (N=12/M=3 battle, N=6/M=1 legacy parity).
4. Wing merge / split hysteresis for converging hostiles.
5. Multi-target CBF tiers: 14.0 m exclusion on every hostile, 4.2 m capture corridor on the
   assigned hostile only, and the hard inter-ally invariant d >= 4.0 m.
6. Guidance laws: proportional navigation lead angle, orbit vector field convergence,
   and the engagement phase machine.
7. Closed-loop 12-agent convergent pincer holding the zero-collision invariant.
"""

import math
import pathlib
import sys

import numpy as np
import pytest

_WS_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _pkg in ("usv_recovery_planner", "usv_policy_marl", "usv_safety_shield", "usv_sim_core"):
    _path = str(_WS_ROOT / "src" / _pkg)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from usv_recovery_planner.establishment import (  # noqa: E402
    CANONICAL_ROLES,
    CASUALTY_FLOOR,
    KIND_CASUALTY,
    ROLE_FITNESS_FLOOR,
    ROLE_INTERCEPTOR,
    ROLE_RELAY,
    ROLE_SCOUT,
    ROLE_TRACKER,
    Establishment,
    TargetState,
    audit_manning,
    build_slot_lattice,
    group_wings,
    merged_pairs,
    pincer_ring_radius,
    R_ARREST_GUARD,
    WING_SPLIT_RADIUS,
    resolve_establishment,
    solve_assignment,
)
from usv_policy_marl.guidance import (  # noqa: E402
    GuidancePhase,
    orbit_vector_field,
    standoff_orbit_command,
    phase_error,
    proportional_navigation,
    select_phase,
    speed_profile,
    transit_waypoint,
    wrap_angle,
)
from usv_sim_core.hostile_ai import (  # noqa: E402
    GAP_BURST_RAD,
    R_FLANKED,
    SPEED_BURST,
    SPEED_CONTESTED,
    SPEED_EVADE,
    HostileState,
    HostileTactic,
    escort_station,
    open_sector_escape,
    relief_target,
    spawn_hostile_formation,
    split_bias,
    step_hostile,
)
from usv_safety_shield.barriers import (  # noqa: E402
    CBF_LAG_MARGIN,
    CBF_LAMBDA,
    D_CAPTURE,
    D_EXCLUSION,
    D_SAFE_ALLY,
    TargetObstacle,
    apply_ally_barriers,
    apply_target_barriers,
    cbf_surge_limit,
    closing_rate_from_velocity,
    filter_command,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _targets(count, spacing=70.0, neutralized=()):
    """Builds `count` well-separated hostiles so each gets its own wing."""
    return [
        TargetState(
            target_id=m,
            x=m * spacing,
            y=0.0,
            heading=0.0,
            speed=1.4,
            neutralized=(m in neutralized),
        )
        for m in range(count)
    ]


def _ring_fleet(n, radius=40.0, center=(0.0, 0.0)):
    """Dispersed starting positions for n agents."""
    return {
        i: (center[0] + radius * math.cos(2.0 * math.pi * i / n),
            center[1] + radius * math.sin(2.0 * math.pi * i / n))
        for i in range(n)
    }


def _allocate(num_agents, targets, health):
    """Runs one full establishment -> lattice -> Hungarian cycle."""
    positions = _ring_fleet(num_agents)
    wings = group_wings(targets)
    fit = [i for i in range(num_agents) if health.get(i, 1.0) >= CASUALTY_FLOOR]
    casualties = num_agents - len(fit)
    est = resolve_establishment(len(fit), wings, num_casualties=casualties)
    slots = build_slot_lattice(est, wings)
    assignments = solve_assignment(
        list(range(num_agents)), positions, health, slots,
        live_target_ids=frozenset(t.target_id for t in targets if not t.neutralized),
    )
    return est, slots, assignments


def _roles_of(assignments):
    """Maps canonical role -> set of agent ids holding a doctrinal (non-casualty) slot."""
    out = {role: set() for role in CANONICAL_ROLES}
    for aid, a in assignments.items():
        if not a.is_casualty_station:
            out[a.role].add(aid)
    return out


# ---------------------------------------------------------------------------
# 1. Establishment completeness -- the no-downtime invariant
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("num_agents", list(range(4, 17)))
@pytest.mark.parametrize("num_targets", [1, 2, 3])
def test_establishment_never_leaves_a_role_unmanned(num_agents, num_targets):
    """Every canonical role must hold at least one slot for any fleet of 4+ vessels."""
    targets = _targets(num_targets)
    wings = group_wings(targets)
    est = resolve_establishment(num_agents, wings)
    totals = est.role_totals()

    for role in CANONICAL_ROLES:
        assert totals[role] >= 1, (
            f"N={num_agents} M={num_targets}: role '{role}' has no slot (totals={totals})"
        )
    assert est.total == num_agents, f"Establishment must consume the whole fleet: {est}"
    assert est.undermanned_roles == (), f"Unexpected undermanned roles: {est.undermanned_roles}"


@pytest.mark.parametrize("num_agents", list(range(4, 17)))
@pytest.mark.parametrize("num_targets", [1, 2, 3])
def test_every_live_hostile_is_engaged_once_the_fleet_is_at_strength(num_agents, num_targets):
    """
    Once the fleet can cover the four role classes plus a wing per hostile, every live
    hostile must have its own interceptor and its own containment tracker.
    """
    if num_agents < 2 * num_targets + 2:
        pytest.skip("fleet is below strength for this target count")

    targets = _targets(num_targets)
    wings = group_wings(targets)
    est = resolve_establishment(num_agents, wings)

    for w in wings:
        assert est.interceptors.get(w.wing_id, 0) >= 1, f"Wing {w.wing_id} has no interceptor"
        assert est.trackers.get(w.wing_id, 0) >= 1, f"Wing {w.wing_id} has no tracker"
    assert est.unengaged_wings == ()
    assert est.unshadowed_wings == ()


@pytest.mark.parametrize("num_agents,num_targets", [(4, 2), (4, 3), (5, 2), (5, 3), (7, 3)])
def test_below_strength_fleet_keeps_all_roles_and_reports_the_shortfall(num_agents, num_targets):
    """
    A fleet too small to put a wing on every hostile must still man all four role classes --
    the comms backbone and the picket are never traded away -- and must report exactly which
    hostiles are under-engaged instead of silently dropping them.
    """
    targets = _targets(num_targets)
    wings = group_wings(targets)
    est = resolve_establishment(num_agents, wings)
    totals = est.role_totals()

    for role in CANONICAL_ROLES:
        assert totals[role] >= 1, f"role '{role}' unmanned at N={num_agents} M={num_targets}"
    assert est.undermanned_roles == (), est.undermanned_roles
    assert est.unengaged_wings or est.unshadowed_wings, "Shortfall was not reported"
    assert est.notes, "An under-strength establishment must carry explanatory notes"
    assert est.total == num_agents


def test_scarcity_sheds_offensive_force_before_support_roles():
    """
    Walking the fleet down from full strength must shed containment force first. The relay
    and the scout -- the roles the swarm cannot coordinate without -- are held to the last.
    """
    targets = _targets(3)
    wings = group_wings(targets)

    # Exactly the full-engagement minimum: 3 interceptors + 3 trackers + 1 relay + 1 scout.
    est = resolve_establishment(8, wings)
    assert sum(est.interceptors.values()) == 3
    assert sum(est.trackers.values()) == 3
    assert est.relays == 1
    assert est.scouts == 1

    # Every smaller fleet still holds the relay and the scout; what it gives up is force
    # committed to secondary hostiles.
    for n in range(7, 3, -1):
        shrunk = resolve_establishment(n, wings)
        assert shrunk.relays >= 1, f"relay dropped at N={n}: {shrunk}"
        assert shrunk.scouts >= 1, f"scout picket dropped at N={n}: {shrunk}"
        assert sum(shrunk.interceptors.values()) >= 1, f"no interceptor at N={n}"
        assert sum(shrunk.trackers.values()) >= 1, f"no tracker at N={n}"
        assert shrunk.total == n
        assert shrunk.notes, f"under-strength establishment at N={n} carries no note"


def test_battle_default_n12_m3_doctrinal_shape():
    """The approved battle default resolves to 3 wings, 1 relay and 1 scout across 12 hulls."""
    targets = _targets(3)
    wings = group_wings(targets)
    est = resolve_establishment(12, wings)

    assert sum(est.interceptors.values()) == 3, est
    assert sum(est.trackers.values()) == 7, est
    assert est.relays == 1
    assert est.scouts == 1
    assert est.total == 12
    # Surplus force is weighted onto one wing rather than spread thin.
    assert sorted(est.trackers.values()) == [2, 2, 3], est.trackers


def test_legacy_n6_m1_matches_prototype_role_split():
    """N=6 / M=1 must reproduce the prototype split: 1 interceptor, 3 trackers, 1 relay, 1 scout."""
    targets = _targets(1)
    wings = group_wings(targets)
    est = resolve_establishment(6, wings)

    assert sum(est.interceptors.values()) == 1
    assert sum(est.trackers.values()) == 3
    assert est.relays == 1
    assert est.scouts == 1


# ---------------------------------------------------------------------------
# 2. Role continuity under hard kills
# ---------------------------------------------------------------------------
def test_relay_role_survives_hard_kill_of_its_incumbent():
    """
    Kill the relay outright. A different, relay-fit vessel must take the relay slot, the
    relay role must never be unmanned, and the casualty must not hold any doctrinal role.
    """
    targets = _targets(3)
    health = {i: 1.0 for i in range(12)}

    _, _, before = _allocate(12, targets, health)
    relay_id = next(iter(_roles_of(before)[ROLE_RELAY]))

    # COMBAT_LOSS: dead in the water.
    health[relay_id] = 0.0
    est, slots, after = _allocate(12, targets, health)
    roles_after = _roles_of(after)

    assert len(roles_after[ROLE_RELAY]) >= 1, "Relay role fell vacant after a hard kill"
    assert relay_id not in roles_after[ROLE_RELAY], "A dead hull is still holding the relay role"
    assert after[relay_id].is_casualty_station, "Combat loss was not routed to a casualty station"

    replacement = next(iter(roles_after[ROLE_RELAY]))
    assert health[replacement] >= ROLE_FITNESS_FLOOR[ROLE_RELAY]

    audit = audit_manning(est, after, health)
    assert audit[ROLE_RELAY]["manned"] >= audit[ROLE_RELAY]["required"]
    assert audit[ROLE_RELAY]["fit"] >= 1


def test_scout_role_survives_hard_kill_and_re_tasks_a_lower_priority_vessel():
    """Kill the scout; a surviving vessel must be re-tasked onto the picket."""
    targets = _targets(3)
    health = {i: 1.0 for i in range(12)}

    _, _, before = _allocate(12, targets, health)
    scout_id = next(iter(_roles_of(before)[ROLE_SCOUT]))

    health[scout_id] = 0.0
    est, _, after = _allocate(12, targets, health)
    roles_after = _roles_of(after)

    assert len(roles_after[ROLE_SCOUT]) >= 1, "Scout role fell vacant after a hard kill"
    assert scout_id not in roles_after[ROLE_SCOUT]
    assert after[scout_id].is_casualty_station

    audit = audit_manning(est, after, health)
    assert audit[ROLE_SCOUT]["fit"] >= 1


def test_simultaneous_relay_and_scout_loss_is_fully_backfilled():
    """Both support roles killed at once: both must be manned again by fit vessels."""
    targets = _targets(3)
    health = {i: 1.0 for i in range(12)}

    _, _, before = _allocate(12, targets, health)
    roles_before = _roles_of(before)
    relay_id = next(iter(roles_before[ROLE_RELAY]))
    scout_id = next(iter(roles_before[ROLE_SCOUT]))

    health[relay_id] = 0.0
    health[scout_id] = 0.0
    est, _, after = _allocate(12, targets, health)
    roles_after = _roles_of(after)

    assert len(roles_after[ROLE_RELAY]) >= 1
    assert len(roles_after[ROLE_SCOUT]) >= 1
    assert {relay_id, scout_id}.isdisjoint(roles_after[ROLE_RELAY] | roles_after[ROLE_SCOUT])

    audit = audit_manning(est, after, health)
    for role in CANONICAL_ROLES:
        assert audit[role]["fit"] >= 1, f"Role '{role}' is not held by a fit vessel: {audit}"


def test_interceptor_loss_is_backfilled_by_a_fit_vessel():
    """An interceptor hard kill must not leave its hostile unengaged."""
    targets = _targets(3)
    health = {i: 1.0 for i in range(12)}

    _, _, before = _allocate(12, targets, health)
    victim = next(iter(_roles_of(before)[ROLE_INTERCEPTOR]))
    victim_wing = before[victim].target_id

    health[victim] = 0.0
    _, _, after = _allocate(12, targets, health)

    engaged_wings = {
        a.target_id for a in after.values()
        if a.role == ROLE_INTERCEPTOR and not a.is_casualty_station
    }
    assert victim_wing in engaged_wings, "Hostile left with no interceptor after a hard kill"
    assert after[victim].is_casualty_station


def test_unfit_vessel_is_never_given_a_role_it_cannot_fly_when_alternatives_exist():
    """A thruster-degraded hull must not hold interceptor duty while fit hulls are available."""
    targets = _targets(2)
    health = {i: 1.0 for i in range(10)}
    health[0] = 0.40  # above the casualty floor, below the interceptor floor
    _, _, assignments = _allocate(10, targets, health)

    assert assignments[0].role != ROLE_INTERCEPTOR
    for aid, a in assignments.items():
        if a.role == ROLE_INTERCEPTOR and not a.is_casualty_station:
            assert health[aid] >= ROLE_FITNESS_FLOOR[ROLE_INTERCEPTOR]


def test_role_is_manned_under_degraded_doctrine_when_no_fit_vessel_remains():
    """
    When nobody meets the interceptor fitness floor the role is still manned by the
    least-unfit survivor -- degraded, flagged, but never vacant.
    """
    targets = _targets(1)
    health = {i: 0.50 for i in range(6)}  # all below the 0.70 interceptor floor
    est, _, assignments = _allocate(6, targets, health)
    roles = _roles_of(assignments)

    assert len(roles[ROLE_INTERCEPTOR]) == 1, "Interceptor role fell vacant under scarcity"
    holder = next(iter(roles[ROLE_INTERCEPTOR]))
    assert assignments[holder].below_fitness_floor, "Degraded manning was not flagged"

    audit = audit_manning(est, assignments, health)
    assert audit[ROLE_INTERCEPTOR]["manned"] == 1
    assert audit[ROLE_INTERCEPTOR]["fit"] == 0


def test_casualty_stations_do_not_consume_the_establishment_quota():
    """Combat losses are excluded from the quota so the doctrine is sized to fit hulls only."""
    targets = _targets(3)
    health = {i: 1.0 for i in range(12)}
    for dead in (2, 5, 9):
        health[dead] = 0.0

    est, slots, assignments = _allocate(12, targets, health)

    assert est.total == 9, f"Establishment must be sized to the 9 fit hulls, got {est.total}"
    assert est.casualty_stations == 3
    assert sum(1 for s in slots if s.kind == KIND_CASUALTY) == 3
    assert len(slots) == 12, "Lattice must stay square with the fleet"

    for dead in (2, 5, 9):
        assert assignments[dead].is_casualty_station

    audit = audit_manning(est, assignments, health)
    for role in CANONICAL_ROLES:
        assert audit[role]["fit"] >= 1, audit


def test_neutralized_hostile_keeps_an_arrest_guard_and_frees_its_wing():
    """A defused hull retains a guard while the rest of its wing re-tasks to the live fight."""
    targets = _targets(3, neutralized=(1,))
    wings = group_wings(targets)
    est = resolve_establishment(12, wings)

    assert est.guards.get(1, 0) >= 1, f"Neutralized hull left unguarded: {est}"
    assert 1 not in est.interceptors, "Interceptor still committed to a neutralized hull"
    # Force freed from the arrested hull is concentrated on the two live hostiles.
    assert set(est.interceptors.keys()) == {0, 2}


# ---------------------------------------------------------------------------
# 3. Wing grouping & merge hysteresis
# ---------------------------------------------------------------------------
def test_separated_hostiles_get_independent_wings():
    wings = group_wings(_targets(3, spacing=70.0))
    assert len(wings) == 3
    assert all(not w.merged for w in wings)


def test_converging_hostiles_merge_into_one_containment_ring():
    targets = [
        TargetState(0, 0.0, 0.0, 0.0, 1.4),
        TargetState(1, 18.0, 0.0, 0.1, 1.4),   # inside the 25 m merge radius
        TargetState(2, 90.0, 0.0, 0.0, 1.4),
    ]
    wings = group_wings(targets)
    assert len(wings) == 2
    merged = [w for w in wings if w.merged]
    assert len(merged) == 1
    assert merged[0].target_ids == (0, 1)
    # A merged wing widens its ring to contain the whole hostile cluster.
    assert merged[0].ring_radius > 20.0


def test_wing_merge_has_split_hysteresis():
    """Once merged, a wing holds together out to the wider split radius before releasing."""
    targets = [
        TargetState(0, 0.0, 0.0, 0.0, 1.4),
        TargetState(1, 30.0, 0.0, 0.0, 1.4),   # between merge (22 m) and split (40 m)
    ]
    fresh = group_wings(targets)
    assert len(fresh) == 2, "30 m apart must not trigger a fresh merge"

    sticky = group_wings(targets, previously_merged=frozenset([frozenset((0, 1))]))
    assert len(sticky) == 1, "An existing merge must hold until the split radius"
    assert merged_pairs(sticky) == frozenset([frozenset((0, 1))])

    targets[1].x = 48.0  # beyond the split radius
    released = group_wings(targets, previously_merged=frozenset([frozenset((0, 1))]))
    assert len(released) == 2


def test_hostile_formation_spawns_outside_the_wing_merge_band():
    """
    Regression for wing merge/split chatter. If the hostile formation spawns inside the
    merge/split band, the ally wings merge and split as the formation settles and every
    transition reshuffles the entire fleet -- which is visible as drones repeatedly turning
    around for a different hostile.
    """
    formation = spawn_hostile_formation(3, origin=(0.0, 0.0), heading=0.0)
    ids = sorted(formation)
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            ha, hb = formation[ids[a]], formation[ids[b]]
            d = math.hypot(ha.x - hb.x, ha.y - hb.y)
            assert d > WING_SPLIT_RADIUS, (
                f"Hostiles {ids[a]} and {ids[b]} spawn {d:.1f} m apart, inside the "
                f"{WING_SPLIT_RADIUS:.0f} m split radius"
            )

    # One wing per hostile at mission start.
    targets = [TargetState(h.target_id, h.x, h.y, h.psi, h.speed) for h in formation.values()]
    assert len(group_wings(targets)) == 3

    # Escort station-keeping must also hold the formation outside the band.
    flagship = formation[0]
    for escort_index in (1, 2):
        sx, sy = escort_station(flagship, escort_index)
        assert math.hypot(sx - flagship.x, sy - flagship.y) > WING_SPLIT_RADIUS


def test_establishment_totals_are_conserved_across_a_wing_merge():
    """Merging wings must not change how many hulls the establishment commits."""
    apart = group_wings(_targets(2, spacing=70.0))
    together = group_wings([TargetState(0, 0.0, 0.0, 0.0, 1.4),
                            TargetState(1, 15.0, 0.0, 0.0, 1.4)])
    assert resolve_establishment(10, apart).total == resolve_establishment(10, together).total == 10


# ---------------------------------------------------------------------------
# 4. Slot lattice geometry
# ---------------------------------------------------------------------------
def test_tracker_ring_stations_are_evenly_phased_on_the_containment_radius():
    targets = _targets(1)
    wings = group_wings(targets)
    est = resolve_establishment(6, wings)
    slots = build_slot_lattice(est, wings)

    ring = [s for s in slots if s.kind == "ring"]
    assert len(ring) == 3
    for s in ring:
        d = math.hypot(s.point[0] - wings[0].center[0], s.point[1] - wings[0].center[1])
        assert abs(d - 20.0) < 1e-6, f"Ring station off radius: {d}"

    gaps = sorted(
        abs(wrap_angle(a.phase_angle - b.phase_angle))
        for a, b in zip(ring, ring[1:] + ring[:1])
    )
    assert all(abs(g - (2.0 * math.pi / 3.0)) < 1e-6 for g in gaps), gaps


def test_pincer_collapse_walks_the_ring_in_without_breaching_the_floor():
    assert pincer_ring_radius(20.0, 0.0) == pytest.approx(20.0)
    assert pincer_ring_radius(20.0, 1.0) == pytest.approx(15.5)
    assert pincer_ring_radius(20.0, 0.5) == pytest.approx(17.75)
    # Progress is clamped, so an overrun can never collapse the ring onto the hull.
    assert pincer_ring_radius(20.0, 3.0) == pytest.approx(15.5)
    assert pincer_ring_radius(20.0, -2.0) == pytest.approx(20.0)


def test_pincer_floor_stays_outside_the_shields_exclusion_envelope():
    """
    Trackers must never be commanded onto a station the shield forbids. The fully collapsed
    containment ring therefore has to sit outside the 14.0 m non-interceptor envelope, or
    guidance and the barrier fight each other for the rest of the engagement.
    """
    assert pincer_ring_radius(20.0, 1.0) > D_EXCLUSION
    assert R_ARREST_GUARD > D_EXCLUSION


def test_pincer_collapsed_ring_still_clears_the_ally_invariant():
    """Six trackers on the fully collapsed ring must stay more than 4.0 m apart."""
    r = pincer_ring_radius(20.0, 1.0)
    chord = 2.0 * r * math.sin(math.pi / 6.0)  # adjacent spacing for 6 stations
    assert chord > D_SAFE_ALLY, f"Collapsed ring spacing {chord:.2f} m breaches the invariant"


def test_interceptor_station_sits_astern_of_its_assigned_hostile():
    targets = [TargetState(0, 0.0, 0.0, heading=0.0, speed=1.4)]
    wings = group_wings(targets)
    est = resolve_establishment(6, wings)
    slots = build_slot_lattice(est, wings)

    stern = [s for s in slots if s.kind == "stern"]
    assert len(stern) == 1
    # Hostile heading +x, so the capture corridor must lie on its -x quarter.
    assert stern[0].point[0] < 0.0
    assert abs(math.hypot(*stern[0].point) - 4.8) < 1e-6
    assert stern[0].target_id == 0


def test_every_slot_carries_a_canonical_role_and_wing_affiliation():
    """Nothing in the lattice may publish a role outside the canonical taxonomy."""
    targets = _targets(3, neutralized=(2,))
    wings = group_wings(targets)
    est = resolve_establishment(12, wings, num_casualties=2)
    slots = build_slot_lattice(est, wings)

    live_ids = {t.target_id for t in targets}
    for s in slots:
        assert s.role in CANONICAL_ROLES, f"Non-canonical role in lattice: {s.role}"
        assert s.target_id in live_ids, f"Slot {s.key} has no valid wing affiliation"
    keys = [s.key for s in slots]
    assert len(keys) == len(set(keys)), "Slot keys must be unique to drive retention hysteresis"


# ---------------------------------------------------------------------------
# 5. Multi-target CBF barriers
# ---------------------------------------------------------------------------
def test_non_interceptor_is_held_off_every_hostile():
    """A tracker must be pushed out of the 14 m envelope of any hostile, assigned or not."""
    targets = [TargetObstacle(0, 0.0, 0.0), TargetObstacle(1, 60.0, 0.0)]

    # Closing on hostile 0 from 16 m: inside the warning band, throttled hard.
    res = apply_target_barriers((-16.0, 0.0), 0.0, 3.5, 0.0, ROLE_TRACKER, 0, targets)
    assert res.u_safe < 3.5
    assert any("envelope_warn" in i for i in res.interventions)

    # Already inside the envelope of the *unassigned* hostile: breach flagged, escape commanded.
    res2 = apply_target_barriers((48.0, 0.0), 0.0, 3.5, 0.0, ROLE_TRACKER, 0, targets)
    assert res2.envelope_breach
    assert any("envelope_breach:t1" in i for i in res2.interventions)
    assert res2.u_safe <= 1.2, "Envelope breach must bleed off speed"
    assert res2.r_safe != 0.0, "Envelope breach must command a tangential escape"


def test_interceptor_capture_corridor_applies_only_to_its_assigned_hostile():
    """The capture corridor is a privilege granted per target, never fleet-wide."""
    targets = [TargetObstacle(0, 0.0, 0.0), TargetObstacle(1, 60.0, 0.0)]

    # 5 m from its ASSIGNED hostile: allowed inside, still under way.
    own = (-5.0, 0.0)
    res = apply_target_barriers(own, 0.0, 3.5, 0.0, ROLE_INTERCEPTOR, 0, targets)
    assert res.u_safe > 0.0, "Interceptor must be allowed into its own capture corridor"
    assert not res.envelope_breach
    assert any("capture_corridor:t0" in i for i in res.interventions)

    # 5 m from an UNASSIGNED hostile: treated exactly as a tracker would treat it.
    res2 = apply_target_barriers((55.0, 0.0), 0.0, 3.5, 0.0, ROLE_INTERCEPTOR, 0, targets)
    assert res2.envelope_breach, "Unassigned hostile must remain a hard obstacle"
    assert any("envelope_breach:t1" in i for i in res2.interventions)


def test_interceptor_brakes_hard_at_the_arrest_boundary():
    """Arrest is non-destructive: thrust is cut at 4.0 m and the vessel sheers off."""
    targets = [TargetObstacle(0, 0.0, 0.0)]
    res = apply_target_barriers((-3.8, 0.0), 0.0, 3.5, 0.0, ROLE_INTERCEPTOR, 0, targets)
    assert res.u_safe == 0.0
    assert abs(res.r_safe) == pytest.approx(0.8)
    assert any("capture_brake" in i for i in res.interventions)


def test_vessel_caught_between_two_hostiles_is_pushed_out_through_the_gap():
    """Overlapping envelopes must produce a resultant escape course, not a stall."""
    targets = [TargetObstacle(0, -8.0, 0.0), TargetObstacle(1, 8.0, 0.0)]
    res = apply_target_barriers((0.0, 1.0), math.pi / 2.0, 2.0, 0.0, ROLE_TRACKER, 0, targets)

    assert res.envelope_breach
    assert res.u_safe > 0.0, "A sandwiched vessel must keep way on to escape"
    # Both hostiles lie on the x-axis, so the escape must drive it clear along +y.
    assert abs(wrap_angle(math.pi / 2.0 - math.atan2(1.0, 0.0))) < 1e-6


def test_inter_ally_barrier_enforces_the_hard_collision_invariant():
    res = apply_ally_barriers((0.0, 0.0), 0.0, 3.5, 0.0, {1: (6.0, 0.0)})
    assert res.u_safe < 3.5
    assert abs(res.r_safe) > 0.0

    # 4.5 m is exactly the barrier (d_safe + lag margin): the closing budget is zero.
    close = apply_ally_barriers((0.0, 0.0), 0.0, 3.5, 0.0, {1: (4.5, 0.0)})
    assert close.u_safe == pytest.approx(0.0)
    assert close.ally_breach
    assert abs(close.r_safe) == pytest.approx(0.8)


def test_ally_invariant_outranks_target_avoidance():
    """A target-avoidance manoeuvre may never be the reason two allies collide."""
    targets = [TargetObstacle(0, 0.0, 0.0)]
    res = filter_command(
        own_pos=(-15.0, 0.0), own_psi=0.0, u_nominal=3.5, r_nominal=0.0,
        role=ROLE_TRACKER, assigned_target_id=0, targets=targets,
        ally_positions={1: (-11.0, 0.0)},
    )
    assert res.u_safe == 0.0, "Ally brake must dominate the final command"
    assert res.ally_breach


# ---------------------------------------------------------------------------
# 6. Guidance laws
# ---------------------------------------------------------------------------
def test_proportional_navigation_leads_a_crossing_target():
    """PN must aim ahead of a crossing hostile, not at it."""
    sol = proportional_navigation(
        own_pos=(0.0, 0.0), own_psi=0.0, own_speed=3.0,
        target_pos=(40.0, 0.0), target_vel=(0.0, 1.5),
    )
    los_heading = math.atan2(0.0, 40.0)
    assert sol.desired_heading > los_heading, "PN failed to establish a lead angle"
    assert sol.lead_point[1] > 0.0, "Lead point must sit ahead of a northbound hostile"
    assert sol.closing_speed > 0.0
    assert sol.yaw_rate_cmd > 0.0, "PN must turn toward the lead point"
    assert abs(sol.yaw_rate_cmd) <= 0.8


def test_proportional_navigation_drives_los_rate_toward_zero():
    """Flying the PN solution must reduce the line-of-sight rate: a collision course."""
    own = [0.0, 0.0]
    psi = 0.6
    speed = 3.0
    tgt = [60.0, 0.0]
    tvel = (0.0, 1.2)
    dt = 0.05

    rates = []
    for _ in range(1200):  # 60 s: long enough for a 3.0 m/s platform to run down the hostile
        sol = proportional_navigation((own[0], own[1]), psi, speed, (tgt[0], tgt[1]), tvel)
        rates.append(abs(sol.los_rate))
        psi = wrap_angle(psi + sol.yaw_rate_cmd * dt)
        own[0] += speed * math.cos(psi) * dt
        own[1] += speed * math.sin(psi) * dt
        tgt[0] += tvel[0] * dt
        tgt[1] += tvel[1] * dt
        if math.hypot(tgt[0] - own[0], tgt[1] - own[1]) < 5.0:
            break

    assert rates[-1] < rates[0], f"LOS rate did not converge: {rates[0]:.4f} -> {rates[-1]:.4f}"
    assert math.hypot(tgt[0] - own[0], tgt[1] - own[1]) < 8.0, "PN failed to close the range"


def test_orbit_vector_field_points_inboard_outside_and_tangential_on_the_ring():
    center = (0.0, 0.0)
    radius = 20.0

    # Well outside: desired course must have an inboard component.
    course_far, bearing_far, err_far = orbit_vector_field((60.0, 0.0), center, radius)
    assert err_far == pytest.approx(40.0)
    assert math.cos(wrap_angle(course_far - (bearing_far + math.pi))) > 0.0

    # On the ring: purely tangential.
    course_on, bearing_on, err_on = orbit_vector_field((20.0, 0.0), center, radius)
    assert err_on == pytest.approx(0.0)
    assert abs(wrap_angle(course_on - (bearing_on + math.pi / 2.0))) < 1e-9

    # Inside: desired course must have an outboard component.
    course_in, bearing_in, err_in = orbit_vector_field((8.0, 0.0), center, radius)
    assert err_in == pytest.approx(-12.0)
    assert math.cos(wrap_angle(course_in - bearing_in)) > 0.0


def test_orbit_vector_field_converges_onto_the_standoff_radius():
    """Flying the field from far out must settle the vessel onto the ring and circulate."""
    pos = [55.0, 10.0]
    psi = 0.0
    speed = 2.5
    dt = 0.05
    center = (0.0, 0.0)
    radius = 20.0

    bearings = []
    for _ in range(1200):
        course, bearing, _ = orbit_vector_field((pos[0], pos[1]), center, radius)
        psi = wrap_angle(psi + float(np.clip(wrap_angle(course - psi) * 2.2, -0.8, 0.8)) * dt)
        pos[0] += speed * math.cos(psi) * dt
        pos[1] += speed * math.sin(psi) * dt
        bearings.append(bearing)

    final_r = math.hypot(pos[0], pos[1])
    assert abs(final_r - radius) < 3.0, f"Orbit did not converge: r={final_r:.2f} m"
    # Net bearing travel confirms it is circulating, not parked on one spot.
    travel = sum(abs(wrap_angle(b - a)) for a, b in zip(bearings, bearings[1:]))
    assert travel > math.pi, f"Vessel is not circulating the ring: {travel:.2f} rad"


def test_phase_error_is_signed_by_circulation_sense():
    assert phase_error(0.0, 0.5, circulation=1.0) == pytest.approx(0.5)
    assert phase_error(0.0, 0.5, circulation=-1.0) == pytest.approx(-0.5)


def test_transit_lane_offsets_spread_the_wing_then_converge_on_station():
    station = (0.0, 0.0)
    far = transit_waypoint((-80.0, 0.0), station, lane_index=0, lane_count=4)
    near = transit_waypoint((-7.0, 0.0), station, lane_index=0, lane_count=4)

    assert abs(far[1]) > 5.0, "Lanes must be spread wide during transit"
    assert abs(near[1]) < abs(far[1]), "Lane offset must taper on arrival"
    # A single-vessel wing has no lane to deconflict against.
    assert transit_waypoint((-80.0, 0.0), station, 0, 1) == station


# ---------------------------------------------------------------------------
# 7. Engagement phase machine
# ---------------------------------------------------------------------------
def test_phase_machine_walks_an_interceptor_through_the_engagement():
    common = dict(role=ROLE_INTERCEPTOR, slot_kind="stern", health=1.0, mission_elapsed=30.0)
    assert select_phase(**common, dist_to_station=80.0, dist_to_target=85.0) is GuidancePhase.TRANSIT
    assert select_phase(**common, dist_to_station=20.0, dist_to_target=25.0) is GuidancePhase.DEPLOY
    assert select_phase(**common, dist_to_station=8.0, dist_to_target=10.0) is GuidancePhase.TERMINAL
    assert select_phase(**common, dist_to_station=2.0, dist_to_target=6.0,
                        defusal_progress=35.0) is GuidancePhase.EMP_LOCK


def test_phase_machine_walks_a_tracker_onto_the_ring_and_into_the_pincer():
    common = dict(role=ROLE_TRACKER, slot_kind="ring", health=1.0, mission_elapsed=30.0)
    assert select_phase(**common, dist_to_station=60.0, dist_to_target=70.0) is GuidancePhase.TRANSIT
    assert select_phase(**common, dist_to_station=18.0, dist_to_target=35.0) is GuidancePhase.DEPLOY
    assert select_phase(**common, dist_to_station=3.0, dist_to_target=20.0) is GuidancePhase.SHADOW
    assert select_phase(**common, dist_to_station=3.0, dist_to_target=20.0,
                        pincer_active=True) is GuidancePhase.PINCER


def test_phase_machine_prioritises_damage_over_geometry():
    """A crippled hull peels off instead of pressing an attack it cannot complete."""
    assert select_phase(ROLE_INTERCEPTOR, "stern", 0.10, 2.0, 5.0, 30.0) is GuidancePhase.COMBAT_LOSS
    assert select_phase(ROLE_INTERCEPTOR, "stern", 0.40, 2.0, 5.0, 30.0) is GuidancePhase.RECOVERY
    # Relay duty is explicitly survivable on reduced power, so it is not withdrawn.
    assert select_phase(ROLE_RELAY, "mesh", 0.40, 2.0, 40.0, 30.0) is GuidancePhase.SHADOW
    # A defused hull is held under guard.
    assert select_phase(ROLE_TRACKER, "guard", 1.0, 2.0, 12.0, 30.0) is GuidancePhase.ARREST_GUARD


def test_launch_window_holds_column_discipline():
    assert select_phase(ROLE_TRACKER, "ring", 1.0, 90.0, 95.0, 0.5) is GuidancePhase.LAUNCH
    assert select_phase(ROLE_TRACKER, "ring", 1.0, 90.0, 95.0, 5.0) is GuidancePhase.TRANSIT


def test_speed_profile_respects_the_actuator_envelope_and_mission_state():
    for phase in GuidancePhase:
        v = speed_profile(phase, dist_to_station=12.0, target_speed=1.4, health=1.0)
        assert 0.0 <= v <= 3.5, f"{phase} produced out-of-envelope speed {v}"

    assert speed_profile(GuidancePhase.COMBAT_LOSS, 0.0, 1.4) == 0.0
    assert speed_profile(GuidancePhase.TRANSIT, 90.0, 1.4) == pytest.approx(3.5)
    # Shadowing matches the hostile's speed when sitting exactly on station.
    assert speed_profile(GuidancePhase.SHADOW, 0.0, 1.4) == pytest.approx(1.4, abs=0.05)
    # The pincer deliberately closes faster than the hostile.
    assert speed_profile(GuidancePhase.PINCER, 0.0, 1.4) > speed_profile(GuidancePhase.SHADOW, 0.0, 1.4)
    # Phase lag commands a catch-up sprint along the ring.
    assert speed_profile(GuidancePhase.SHADOW, 0.0, 1.4, ring_phase_error=0.8) > 1.4


# ---------------------------------------------------------------------------
# 8. Closed-loop convergent pincer: the zero-collision invariant
# ---------------------------------------------------------------------------
def test_twelve_agent_convergent_pincer_never_breaches_the_collision_invariant():
    """
    Drives 12 vessels from a wide dispersal onto a fully collapsed containment ring around
    three hostiles, through the real guidance laws and the real CBF shield, integrating the
    same 3-DOF response model as usv_sim_core. Minimum separation must hold at 4.0 m.
    """
    dt = 0.05
    num_agents = 12
    targets = _targets(3, spacing=45.0)
    health = {i: 1.0 for i in range(num_agents)}

    # Deliberately adversarial start: the whole fleet packed into one launch corridor.
    states = {
        i: np.array([-90.0 + (i % 3) * 5.0, -12.0 + (i // 3) * 8.0, 0.0, 0.0, 0.0, 0.0])
        for i in range(num_agents)
    }

    wings = group_wings(targets)
    est = resolve_establishment(num_agents, wings)
    # Full pincer collapse from the outset: the tightest geometry the doctrine ever commands.
    slots = build_slot_lattice(est, wings, collapse_progress={w.wing_id: 1.0 for w in wings})
    positions = {i: (states[i][0], states[i][1]) for i in range(num_agents)}
    assignments = solve_assignment(list(range(num_agents)), positions, health, slots,
                                   live_target_ids=frozenset((0, 1, 2)))

    obstacles = [TargetObstacle(t.target_id, t.x, t.y) for t in targets]
    min_sep = float('inf')
    worst_step = -1

    for step in range(1600):  # 80 s
        positions = {i: (states[i][0], states[i][1]) for i in range(num_agents)}

        for i in range(num_agents):
            x, y, psi, u, v, r = states[i]
            a = assignments[i]
            gx, gy = a.slot_point
            heading_err = wrap_angle(math.atan2(gy - y, gx - x) - psi)
            dist = math.hypot(gx - x, gy - y)

            # Nominal guidance: press hard onto station, as the policy executor would.
            u_nom = min(3.5, 1.0 + 0.6 * dist) * max(0.2, math.cos(heading_err))
            r_nom = float(np.clip(heading_err * 2.2, -0.8, 0.8))

            res = filter_command(
                own_pos=(x, y), own_psi=psi, u_nominal=u_nom, r_nominal=r_nom,
                role=a.role, assigned_target_id=a.target_id, targets=obstacles,
                ally_positions={j: positions[j] for j in range(num_agents) if j != i},
            )

            # Same 3-DOF response model as usv_sim_core.
            u += 2.0 * (res.u_safe - u) * dt
            r += 3.0 * (res.r_safe - r) * dt
            x += (u * math.cos(psi)) * dt
            y += (u * math.sin(psi)) * dt
            psi = wrap_angle(psi + r * dt)
            states[i] = np.array([x, y, psi, u, v, r])

        for i in range(num_agents):
            for j in range(i + 1, num_agents):
                d = math.hypot(states[i][0] - states[j][0], states[i][1] - states[j][1])
                if d < min_sep:
                    min_sep = d
                    worst_step = step

    assert min_sep >= D_SAFE_ALLY, (
        f"Collision invariant breached: min separation {min_sep:.3f} m at step {worst_step} "
        f"(t={worst_step * dt:.1f}s)"
    )


def test_trackers_hold_off_the_hostiles_through_the_whole_pincer():
    """No containment tracker may ever end up inside a hostile's exclusion envelope."""
    dt = 0.05
    num_agents = 10
    targets = _targets(2, spacing=50.0)
    health = {i: 1.0 for i in range(num_agents)}
    states = {
        i: np.array([-70.0 + (i % 2) * 6.0, -10.0 + (i // 2) * 6.0, 0.0, 0.0, 0.0, 0.0])
        for i in range(num_agents)
    }

    wings = group_wings(targets)
    est = resolve_establishment(num_agents, wings)
    slots = build_slot_lattice(est, wings)
    positions = {i: (states[i][0], states[i][1]) for i in range(num_agents)}
    assignments = solve_assignment(list(range(num_agents)), positions, health, slots,
                                   live_target_ids=frozenset((0, 1)))
    obstacles = [TargetObstacle(t.target_id, t.x, t.y) for t in targets]

    worst_tracker_dist = float('inf')
    for _ in range(1400):
        positions = {i: (states[i][0], states[i][1]) for i in range(num_agents)}
        for i in range(num_agents):
            x, y, psi, u, v, r = states[i]
            a = assignments[i]
            gx, gy = a.slot_point
            heading_err = wrap_angle(math.atan2(gy - y, gx - x) - psi)
            dist = math.hypot(gx - x, gy - y)
            res = filter_command(
                own_pos=(x, y), own_psi=psi,
                u_nominal=min(3.5, 1.0 + 0.6 * dist) * max(0.2, math.cos(heading_err)),
                r_nominal=float(np.clip(heading_err * 2.2, -0.8, 0.8)),
                role=a.role, assigned_target_id=a.target_id, targets=obstacles,
                ally_positions={j: positions[j] for j in range(num_agents) if j != i},
            )
            u += 2.0 * (res.u_safe - u) * dt
            r += 3.0 * (res.r_safe - r) * dt
            x += (u * math.cos(psi)) * dt
            y += (u * math.sin(psi)) * dt
            psi = wrap_angle(psi + r * dt)
            states[i] = np.array([x, y, psi, u, v, r])

            if a.role == ROLE_TRACKER:
                for t in targets:
                    worst_tracker_dist = min(worst_tracker_dist, math.hypot(x - t.x, y - t.y))

    # Allow a small transient while the shield turns the vessel, but no hull overlay.
    assert worst_tracker_dist > 8.0, (
        f"A tracker penetrated to {worst_tracker_dist:.2f} m of a hostile hull"
    )


# ---------------------------------------------------------------------------
# 9. Coordinated hostile AI
# ---------------------------------------------------------------------------
def test_hostile_formation_spawns_as_a_flagship_with_echeloned_escorts():
    """Hostiles must spawn as a formation, not stacked on a single point."""
    formation = spawn_hostile_formation(3, origin=(0.0, 0.0), heading=0.0)
    assert len(formation) == 3
    assert formation[0].role_label == "FLAGSHIP"
    assert formation[0].x == pytest.approx(0.0)

    # Escorts sit astern of the flagship, on opposite quarters.
    assert formation[1].x < 0.0 and formation[2].x < 0.0
    assert formation[1].y * formation[2].y < 0.0, "Escorts must flank opposite sides"

    positions = [(h.x, h.y) for h in formation.values()]
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            d = math.hypot(positions[i][0] - positions[j][0], positions[i][1] - positions[j][1])
            assert d > 10.0, "Hostile hulls must spawn clear of one another"


def test_open_sector_escape_drives_through_the_widest_unguarded_sector():
    """The hostile must read its whole perimeter, not just the nearest pursuer."""
    # Pursuers clustered on the -x side; the open water is +x.
    threats = [(-20.0, 0.0), (-18.0, 10.0), (-18.0, -10.0)]
    escape, gap, nearest = open_sector_escape(0.0, 0.0, 0.0, threats)

    assert abs(wrap_angle(escape)) < 0.6, f"Escape should head into open water, got {escape:.2f}"
    assert gap > math.pi, "The open sector spans more than half the perimeter here"
    assert nearest == pytest.approx(math.hypot(18.0, 10.0), rel=1e-3) or nearest == pytest.approx(20.0)


def test_open_sector_escape_runs_directly_away_from_a_lone_pursuer():
    escape, _, nearest = open_sector_escape(0.0, 0.0, 0.0, [(10.0, 0.0)])
    assert abs(abs(escape) - math.pi) < 1e-9
    assert nearest == pytest.approx(10.0)


def test_split_bias_pushes_hostiles_apart():
    """Under pressure, hostiles must diverge so the swarm has to divide its force."""
    own = HostileState(target_id=0, x=0.0, y=0.0, psi=0.0)
    partner = HostileState(target_id=1, x=0.0, y=-20.0, psi=0.0)

    bias = split_bias(own, [own, partner])
    # Partner is to the south, so the bias must steer this hostile north.
    assert bias > 0.0, f"Expected a northward split bias, got {bias:.3f}"
    # With no live partners there is nothing to diverge from.
    assert split_bias(own, [own]) == 0.0
    dead = HostileState(target_id=1, x=0.0, y=-20.0, neutralized=True)
    assert split_bias(own, [own, dead]) == 0.0


def test_relief_target_selects_a_pinned_partner_and_ignores_a_pinned_self():
    free = HostileState(target_id=0, x=0.0, y=0.0)
    pinned_near = HostileState(target_id=1, x=30.0, y=0.0, defusal_progress=60.0)
    pinned_far = HostileState(target_id=2, x=200.0, y=0.0, defusal_progress=80.0)

    assert relief_target(free, [free, pinned_near, pinned_far]).target_id == 1
    # A hostile already under lock cannot mount a relief run.
    free.defusal_progress = 40.0
    assert relief_target(free, [free, pinned_near]) is None
    # Nor can one be mounted for an already-arrested hull.
    free.defusal_progress = 0.0
    arrested = HostileState(target_id=1, x=30.0, y=0.0, defusal_progress=100.0, neutralized=True)
    assert relief_target(free, [free, arrested]) is None


def test_escort_station_sits_on_the_flagship_quarters():
    flagship = HostileState(target_id=0, x=0.0, y=0.0, psi=0.0)
    port = escort_station(flagship, 1)
    starboard = escort_station(flagship, 2)

    # Both stations lie astern of a flagship heading +x, on opposite sides.
    assert port[0] < 0.0 and starboard[0] < 0.0
    assert port[1] * starboard[1] < 0.0


def test_hostile_decelerates_to_a_standstill_once_arrested():
    """Full defusal must bring the hostile to 0.00 m/s -- the mission success condition."""
    host = HostileState(target_id=0, speed=1.6, defusal_progress=100.0)
    for _ in range(200):  # 10 s
        step_hostile(host, dt=0.05, sim_time=1.0, threats=[(10.0, 0.0)], partners=[host])

    assert host.neutralized
    assert host.speed == pytest.approx(0.0, abs=1e-9)
    assert host.yaw_rate == 0.0
    assert host.combat_state == "DEFUSED_AND_ARRESTED"


def test_emp_lock_progressively_starves_hostile_propulsion():
    """Speed ceiling and hull health must both fall as the defusal lock accumulates."""
    speeds = []
    for pct in (10.0, 25.0, 50.0, 75.0, 99.0):
        host = HostileState(target_id=0, speed=1.6, defusal_progress=pct)
        for _ in range(120):  # 6 s to settle on the ceiling
            # Pursuer shadows the hostile so the geometry stays constant across the sweep.
            step_hostile(host, dt=0.05, sim_time=2.0,
                         threats=[(host.x + 24.0, host.y)], partners=[host])
        speeds.append(host.speed)

    assert all(b <= a + 1e-6 for a, b in zip(speeds, speeds[1:])), speeds
    assert speeds[-1] <= 0.35, f"Near-full defusal must nearly stop the hostile: {speeds[-1]:.2f}"

    # Damage and geometry compound: flanked AND locked is slower than locked alone.
    boxed = HostileState(target_id=0, speed=1.6, defusal_progress=25.0)
    for _ in range(120):
        step_hostile(boxed, dt=0.05, sim_time=2.0,
                     threats=[(boxed.x + 9.0, boxed.y)], partners=[boxed])
    assert boxed.speed < speeds[1] + 1e-6, (
        f"Flanked-and-locked ({boxed.speed:.3f}) must not exceed locked-only ({speeds[1]:.3f})"
    )

    locked = HostileState(target_id=0, speed=1.6, defusal_progress=80.0)
    step_hostile(locked, dt=0.05, sim_time=2.0, threats=[(12.0, 0.0)], partners=[locked])
    assert locked.health < 0.6, "EMP lock must degrade hull health"
    assert "UNDER_DEFUSAL" in locked.combat_state


def test_hostile_chokes_down_when_flanked_and_bursts_through_an_opening():
    """Speed governance must respond to how boxed in the hostile actually is."""
    # Flanked: pursuers all round and close.
    flanked = HostileState(target_id=0, speed=1.6)
    ring = [(R_FLANKED - 4.0) * np.array([math.cos(a), math.sin(a)])
            for a in np.linspace(0.0, 2.0 * math.pi, 7)[:-1]]
    for _ in range(100):
        step_hostile(flanked, dt=0.05, sim_time=3.0,
                     threats=[(float(p[0]), float(p[1])) for p in ring], partners=[flanked])
    assert flanked.combat_state == "CONTESTED_FLANKED"
    assert flanked.speed <= SPEED_CONTESTED + 1e-6

    # Wide opening with pursuers still distant: breakout sprint. The pursuers shadow from
    # astern so they stay in contact instead of falling out of detection range.
    clear = HostileState(target_id=0, speed=1.2)
    for _ in range(100):
        step_hostile(clear, dt=0.05, sim_time=3.0,
                     threats=[(clear.x - 30.0, clear.y + 2.0),
                              (clear.x - 30.0, clear.y - 2.0)], partners=[clear])
    assert clear.combat_state == "BREAKOUT_BURST"
    assert clear.speed > SPEED_EVADE


def test_unthreatened_escort_closes_onto_its_flagship_station():
    """Escort formation keeping must actually reduce the station-keeping error."""
    flagship = HostileState(target_id=0, x=0.0, y=0.0, psi=0.0, speed=1.2, role_label="FLAGSHIP")
    escort = HostileState(target_id=1, x=-80.0, y=60.0, psi=0.0, speed=1.0, role_label="ESCORT")
    partners = [flagship, escort]

    def station_error():
        sx, sy = escort_station(flagship, 1)
        return math.hypot(escort.x - sx, escort.y - sy)

    before = station_error()
    for _ in range(1600):  # 80 s, no pursuers in sight
        step_hostile(flagship, dt=0.05, sim_time=1.0, threats=[], partners=partners,
                     tactic=HostileTactic.COORDINATED)
        step_hostile(escort, dt=0.05, sim_time=1.0, threats=[], partners=partners,
                     tactic=HostileTactic.COORDINATED)

    assert station_error() < before, "Escort failed to close on its station"
    assert escort.combat_state == "ESCORT_FORMATION"


def test_legacy_target_modes_are_preserved():
    """The prototype's nominal and sinusoidal demonstration modes must still behave."""
    nominal = HostileState(target_id=0, speed=0.0)
    for _ in range(40):
        step_hostile(nominal, dt=0.05, sim_time=1.0, threats=[(5.0, 0.0)],
                     partners=[nominal], mode="nominal")
    assert nominal.speed == pytest.approx(1.2)
    assert nominal.yaw_rate == 0.0
    assert nominal.x > 0.0, "Nominal mode must transit in a straight line"

    sinus = HostileState(target_id=0)
    yaw_rates = []
    for k in range(200):
        step_hostile(sinus, dt=0.05, sim_time=k * 0.05, threats=[], partners=[sinus],
                     mode="sinusoidal")
        yaw_rates.append(sinus.yaw_rate)
    assert sinus.speed == pytest.approx(1.5)
    assert max(yaw_rates) > 0.0 and min(yaw_rates) < 0.0, "Sinusoidal mode must weave both ways"


def test_hostiles_under_pressure_diverge_rather_than_bunching():
    """Two pressured hostiles must end up further apart: split evasion in closed loop."""
    h0 = HostileState(target_id=0, x=0.0, y=10.0, psi=0.0, speed=1.4)
    h1 = HostileState(target_id=1, x=0.0, y=-10.0, psi=0.0, speed=1.4)
    partners = [h0, h1]
    start_sep = math.hypot(h0.x - h1.x, h0.y - h1.y)

    for k in range(900):  # 45 s under constant pursuit from astern
        threats = [(h0.x - 20.0, h0.y), (h1.x - 20.0, h1.y)]
        step_hostile(h0, dt=0.05, sim_time=k * 0.05, threats=threats, partners=partners,
                     tactic=HostileTactic.SPLIT)
        step_hostile(h1, dt=0.05, sim_time=k * 0.05, threats=threats, partners=partners,
                     tactic=HostileTactic.SPLIT)

    assert math.hypot(h0.x - h1.x, h0.y - h1.y) > start_sep * 1.5, "Hostiles failed to split"


# ---------------------------------------------------------------------------
# 10. Velocity-aware CBF
# ---------------------------------------------------------------------------
def test_cbf_surge_limit_shrinks_the_closing_budget_toward_the_barrier():
    """The permitted closing rate must fall linearly to zero at the barrier."""
    assert cbf_surge_limit(10.0, 4.0, 0.0) == pytest.approx(CBF_LAMBDA * 6.0)
    assert cbf_surge_limit(5.0, 4.0, 0.0) == pytest.approx(CBF_LAMBDA * 1.0)
    assert cbf_surge_limit(4.0, 4.0, 0.0) == pytest.approx(0.0)
    # Already inside the barrier: no surge toward the obstacle at all.
    assert cbf_surge_limit(3.0, 4.0, 0.0) == pytest.approx(0.0)


def test_cbf_imposes_no_constraint_when_opening_or_tangential():
    """
    A vessel passing a wreck tangentially must not be braked. A position-only barrier stops it
    dead and the swarm jams up behind drifting hulls.
    """
    assert cbf_surge_limit(4.5, 4.0, math.pi / 2.0) is None     # exactly abeam
    assert cbf_surge_limit(4.5, 4.0, math.pi) is None            # dead astern
    assert cbf_surge_limit(4.5, 4.0, 2.0) is None                # opening


def test_cbf_absorbs_an_obstacle_that_is_closing_on_its_own_account():
    """A hostile running the vessel down eats into its closing budget; a fleeing one frees it."""
    neutral = cbf_surge_limit(8.0, 4.0, 0.0, obstacle_closing=0.0)
    chased = cbf_surge_limit(8.0, 4.0, 0.0, obstacle_closing=2.0)
    fleeing = cbf_surge_limit(8.0, 4.0, 0.0, obstacle_closing=-2.0)

    assert chased < neutral < fleeing
    assert chased == pytest.approx(CBF_LAMBDA * 4.0 - 2.0)


def test_closing_rate_sign_convention():
    """Positive while the obstacle reduces the range, negative while it opens it."""
    # Obstacle at +x moving toward the origin: closing.
    assert closing_rate_from_velocity((0.0, 0.0), (10.0, 0.0), (-1.5, 0.0)) == pytest.approx(1.5)
    # Same obstacle running away: opening.
    assert closing_rate_from_velocity((0.0, 0.0), (10.0, 0.0), (1.5, 0.0)) == pytest.approx(-1.5)
    # Crossing: no radial component.
    assert closing_rate_from_velocity((0.0, 0.0), (10.0, 0.0), (0.0, 1.5)) == pytest.approx(0.0)


def test_ally_barrier_does_not_brake_a_vessel_passing_a_drifting_wreck():
    """
    The exact failure seen in the live run: a live vessel abeam of a dead hull was commanded to
    zero thrust and jammed. Passing abeam at 4.6 m must leave the command untouched.
    """
    res = apply_ally_barriers(
        own_pos=(0.0, 0.0), own_psi=0.0, u_cmd=3.0, r_cmd=0.0,
        ally_positions={1: (0.0, 4.6)},           # dead abeam to port
        ally_velocities={1: (0.0, 0.0)},          # wreck, dead in the water
    )
    assert res.u_safe == pytest.approx(3.0), "A vessel passing abeam must not be braked"
    assert not res.ally_breach


def test_standoff_orbit_feeds_the_hostile_velocity_forward():
    """
    Shadowing a moving hostile requires the hostile's velocity as feed-forward. Without it the
    commanded speed cannot exceed the orbit rate and the ring translates out from under the
    tracker -- which is exactly why containment never closed in the first live run.
    """
    stationary = standoff_orbit_command(
        own_pos=(20.0, 0.0), center=(0.0, 0.0), center_vel=(0.0, 0.0),
        radius=20.0, station_bearing=0.0, orbit_speed=1.3,
    )
    running = standoff_orbit_command(
        own_pos=(20.0, 0.0), center=(0.0, 0.0), center_vel=(1.9, 0.0),
        radius=20.0, station_bearing=0.0, orbit_speed=1.3,
    )

    # Exactly on station and on radius with a stationary hostile: nothing to do.
    assert stationary.radial_error == pytest.approx(0.0)
    assert stationary.phase_error == pytest.approx(0.0)
    assert stationary.desired_speed == pytest.approx(0.0, abs=1e-6)

    # Same geometry, hostile making 1.9 m/s: the command must match it exactly.
    assert running.desired_speed == pytest.approx(1.9, abs=1e-6)
    assert running.desired_heading == pytest.approx(0.0, abs=1e-6)


def test_standoff_orbit_phase_correction_is_signed():
    """
    A vessel ahead of its station must be able to fall BACK, not only sprint forward. A
    forward-only trim piles every tracker onto one arc of the ring.
    """
    behind = standoff_orbit_command(
        own_pos=(20.0 * math.cos(-0.6), 20.0 * math.sin(-0.6)),
        center=(0.0, 0.0), center_vel=(0.0, 0.0),
        radius=20.0, station_bearing=0.0, circulation=1.0,
    )
    ahead = standoff_orbit_command(
        own_pos=(20.0 * math.cos(0.6), 20.0 * math.sin(0.6)),
        center=(0.0, 0.0), center_vel=(0.0, 0.0),
        radius=20.0, station_bearing=0.0, circulation=1.0,
    )

    assert behind.phase_error > 0.0 and behind.tangential_rate > 0.0
    assert ahead.phase_error < 0.0 and ahead.tangential_rate < 0.0, (
        "A vessel ahead of station must be commanded retrograde"
    )


def test_standoff_orbit_closes_a_wide_standoff_error():
    """Far outside the ring, the command must be dominated by inbound radial closure."""
    sol = standoff_orbit_command(
        own_pos=(85.0, 0.0), center=(0.0, 0.0), center_vel=(0.0, 0.0),
        radius=20.0, station_bearing=0.0,
    )
    assert sol.radial_error == pytest.approx(65.0)
    assert sol.radial_rate < 0.0, "Must be commanded inbound"
    assert abs(wrap_angle(sol.desired_heading - math.pi)) < 0.9, "Heading must be broadly inbound"


def test_trackers_close_containment_on_a_running_hostile():
    """
    Closed-loop regression for the feed-forward fix: six trackers starting 85 m away must
    establish and HOLD a containment ring on a hostile making 1.9 m/s, through the real
    guidance law, the real CBF shield and the sim's own 3-DOF response model.
    """
    dt = 0.05
    n = 6
    radius = 20.0
    tgt = [0.0, 0.0]
    tvel = (1.9, 0.0)

    states = {
        i: np.array([-85.0, -15.0 + i * 6.0, 0.0, 0.0, 0.0, 0.0], dtype=float)
        for i in range(n)
    }
    stations = [2.0 * math.pi * i / n for i in range(n)]

    min_sep = float('inf')
    for step in range(3000):  # 150 s
        tgt[0] += tvel[0] * dt
        tgt[1] += tvel[1] * dt
        positions = {i: (states[i][0], states[i][1]) for i in range(n)}
        obstacles = [TargetObstacle(0, tgt[0], tgt[1], tvel[0], tvel[1])]

        for i in range(n):
            x, y, psi, u, v, r = states[i]
            sol = standoff_orbit_command(
                own_pos=(x, y), center=(tgt[0], tgt[1]), center_vel=tvel,
                radius=radius, station_bearing=stations[i], orbit_speed=1.3,
            )
            heading_err = wrap_angle(sol.desired_heading - psi)
            res = filter_command(
                own_pos=(x, y), own_psi=psi,
                u_nominal=sol.desired_speed * max(0.2, math.cos(heading_err)),
                r_nominal=float(np.clip(heading_err * 2.2, -0.8, 0.8)),
                role=ROLE_TRACKER, assigned_target_id=0, targets=obstacles,
                ally_positions={j: positions[j] for j in range(n) if j != i},
                ally_velocities={
                    j: (states[j][3] * math.cos(states[j][2]),
                        states[j][3] * math.sin(states[j][2]))
                    for j in range(n) if j != i
                },
            )
            u += 2.0 * (res.u_safe - u) * dt
            r += 3.0 * (res.r_safe - r) * dt
            x += (u * math.cos(psi)) * dt
            y += (u * math.sin(psi)) * dt
            psi = wrap_angle(psi + r * dt)
            states[i] = np.array([x, y, psi, u, v, r])

        if step > 600:  # allow the transit-and-fan-out phase to complete
            for i in range(n):
                for j in range(i + 1, n):
                    min_sep = min(min_sep, math.hypot(states[i][0] - states[j][0],
                                                      states[i][1] - states[j][1]))

    # 1. Every tracker must be on the containment ring, not trailing behind it.
    ranges = [math.hypot(states[i][0] - tgt[0], states[i][1] - tgt[1]) for i in range(n)]
    assert max(ranges) < 32.0, f"Trackers failed to hold the ring: ranges {ranges}"
    assert min(ranges) > D_EXCLUSION, f"A tracker penetrated the envelope: ranges {ranges}"

    # 2. The ring must actually encircle: Section 5 criterion 2 on the angular gap.
    bearings = sorted(math.atan2(states[i][1] - tgt[1], states[i][0] - tgt[0]) for i in range(n))
    gaps = [(bearings[(k + 1) % n] - bearings[k]) % (2.0 * math.pi) for k in range(n)]
    assert max(gaps) <= (2.0 * math.pi / n) * 1.60, (
        f"Containment left an escape sector of {max(gaps):.2f} rad"
    )

    # 3. The collision invariant holds throughout.
    assert min_sep >= D_SAFE_ALLY, f"Collision invariant breached at {min_sep:.3f} m"


def test_ring_stations_stay_evenly_phased_under_full_pincer_collapse():
    """
    Regression for the scissor-bias defect. Displacing flank stations to "seal the escape
    sector" opened a ~117 deg hole elsewhere on the ring and made Section 5 criterion 2 fail
    for a whole engagement. Even spacing minimises the maximum angular gap by construction,
    so the squeeze must come from the radius alone.
    """
    targets = _targets(1)
    wings = group_wings(targets)
    est = resolve_establishment(12, wings)
    collapsed = build_slot_lattice(est, wings, collapse_progress={w.wing_id: 1.0 for w in wings})

    ring = [s for s in collapsed if s.kind == "ring"]
    assert len(ring) >= 3

    # Every station on the fully collapsed radius...
    for s in ring:
        d = math.hypot(s.point[0] - wings[0].center[0], s.point[1] - wings[0].center[1])
        assert d == pytest.approx(pincer_ring_radius(20.0, 1.0), abs=1e-6)

    # ...and evenly phased, so the maximum gap is the ideal 2*pi/n.
    phases = sorted(wrap_angle(s.phase_angle) for s in ring)
    n = len(phases)
    gaps = [(phases[(k + 1) % n] - phases[k]) % (2.0 * math.pi) for k in range(n)]
    ideal = 2.0 * math.pi / n
    assert max(gaps) == pytest.approx(ideal, abs=1e-6), (
        f"Ring is not evenly phased: gaps {[round(g, 3) for g in gaps]}"
    )

    # And that spacing satisfies the Section 5 gap criterion with margin.
    assert max(gaps) <= (2.0 * math.pi / n) * 1.60


def test_wing_switch_penalty_dominates_inter_wing_distance_differences():
    """
    Regression for cross-wing bouncing. A drone sitting between two live hostiles must not
    flip wings over a small distance advantage: the switch penalty has to outweigh the
    ordinary station-distance differences between wings (hostiles sit 50-100 m apart).
    """
    targets = _targets(2, spacing=90.0)
    wings = group_wings(targets)
    est = resolve_establishment(10, wings)
    slots = build_slot_lattice(est, wings)
    health = {i: 1.0 for i in range(10)}

    # Place the fleet, solve once to establish incumbency, then re-solve unchanged.
    positions = {i: (10.0 + 8.0 * i, 30.0) for i in range(10)}
    live = frozenset((0, 1))
    first = solve_assignment(list(range(10)), positions, health, slots, live_target_ids=live)

    prev_keys = {aid: a.slot_key for aid, a in first.items()}
    prev_wing = {aid: a.target_id for aid, a in first.items()}
    second = solve_assignment(list(range(10)), positions, health, slots,
                              previous_slot_keys=prev_keys, previous_target=prev_wing,
                              live_target_ids=live)

    switched = [aid for aid in first if first[aid].target_id != second[aid].target_id]
    assert not switched, f"Drones switched wings with no change in the picture: {switched}"


def test_wing_switch_penalty_is_waived_once_a_hostile_is_neutralized():
    """
    The penalty must not strand a wing on an arrested hull. Once a hostile is neutralized it
    leaves `live_target_ids`, and re-tasking to the live fight becomes free.
    """
    targets = _targets(2, spacing=90.0, neutralized=(0,))
    wings = group_wings(targets)
    est = resolve_establishment(10, wings)
    slots = build_slot_lattice(est, wings)
    health = {i: 1.0 for i in range(10)}
    positions = {i: (10.0 + 8.0 * i, 30.0) for i in range(10)}

    # Everyone was previously committed to the now-arrested hostile 0.
    prev_wing = {i: 0 for i in range(10)}
    result = solve_assignment(list(range(10)), positions, health, slots,
                              previous_target=prev_wing,
                              live_target_ids=frozenset((1,)))

    on_live = sum(1 for a in result.values()
                  if a.target_id == 1 and not a.is_casualty_station)
    assert on_live >= 1, "Force must be free to re-task onto the live hostile"


def test_wing_priority_is_stable_across_hostile_speed_regimes():
    """
    Regression for surplus-tracker shuttling. Hostile speed legitimately swings between
    regimes (contested 1.15, evade 1.65, burst 1.95 m/s). Ordering wings on raw threat flips
    the ranking every few seconds, which moves the surplus tracker slot between wings and
    makes that drone shuttle between hostiles for the whole engagement.
    """
    quotas = set()
    for speeds in ((1.15, 1.65, 1.95), (1.95, 1.15, 1.65), (1.65, 1.95, 1.15),
                   (1.20, 1.20, 1.20), (1.95, 1.95, 1.95)):
        targets = [
            TargetState(target_id=m, x=m * 90.0, y=0.0, heading=0.0, speed=s)
            for m, s in enumerate(speeds)
        ]
        est = resolve_establishment(12, group_wings(targets))
        quotas.add(tuple(sorted(est.trackers.items())))

    assert len(quotas) == 1, (
        f"Tracker quota per wing changed with hostile speed regime: {quotas}"
    )


def test_a_genuinely_faster_hostile_still_takes_priority():
    """
    Stability must not blind the allocator: a hostile a whole m/s faster than the others is a
    different threat band and should draw the surplus force.
    """
    targets = [
        TargetState(0, 0.0, 0.0, 0.0, speed=1.2),
        TargetState(1, 90.0, 0.0, 0.0, speed=1.2),
        TargetState(2, 180.0, 0.0, 0.0, speed=3.4),   # a band above the others
    ]
    est = resolve_establishment(12, group_wings(targets))
    assert est.trackers[2] == max(est.trackers.values()), (
        f"The fastest hostile did not draw the surplus tracker: {est.trackers}"
    )


def test_wing_assignment_is_stable_during_the_launch_transit():
    """
    Regression for the churn visible in the first ten seconds of a mission. During the launch
    transit the fleet is bunched in one corridor at near-equal range from every hostile, so the
    assignment is nearly degenerate and a fixed switch penalty is small against the station
    distance differences. Re-solving an unchanged picture must not move anyone.
    """
    targets = _targets(3, spacing=90.0)
    wings = group_wings(targets)
    est = resolve_establishment(12, wings)
    slots = build_slot_lattice(est, wings)
    health = {i: 1.0 for i in range(12)}
    live = frozenset((0, 1, 2))

    # The launch wedge: a tight double column 85 m off the engagement box.
    positions = {
        i: (-85.0 + (i % 2) * 6.5, -12.0 + (i // 2) * 9.0)
        for i in range(12)
    }
    first = solve_assignment(list(range(12)), positions, health, slots, live_target_ids=live)
    prev_keys = {aid: a.slot_key for aid, a in first.items()}
    prev_wing = {aid: a.target_id for aid, a in first.items()}

    # Re-solve repeatedly with the fleet creeping forward, as it does during transit.
    for step in range(1, 25):
        positions = {i: (p[0] + 0.35 * step, p[1]) for i, p in positions.items()}
        nxt = solve_assignment(list(range(12)), positions, health, slots,
                               previous_slot_keys=prev_keys, previous_target=prev_wing,
                               live_target_ids=live)
        switched = [aid for aid in nxt if nxt[aid].target_id != prev_wing[aid]]
        assert not switched, (
            f"Drones switched wings mid-transit at step {step}: {switched}"
        )
        prev_keys = {aid: a.slot_key for aid, a in nxt.items()}
        prev_wing = {aid: a.target_id for aid, a in nxt.items()}
