"""
USV Swarm Simulation - Tactical Establishment Resolver & Multi-Wing Slot Lattice
Author: Principal Enterprise Engineer
Stage: 2 (Multi-Target Battle Doctrine)

Pure (rclpy-free) allocation engine backing usv_recovery_planner.

Responsibilities:
- Resolves the tactical establishment: the role quota the fleet MUST man, derived from the
  live target set and the count of combat-fit agents -- never from the surviving role vector.
  Because slots are generated from the establishment, a role slot cannot disappear when its
  incumbent is lost, so no role class is ever on downtime while a fit agent exists to fill it.
- Groups hostile targets into tactical wings, merging wings whose targets converge.
- Builds the geometric slot lattice: interceptor stern corridors, tracker containment rings,
  arrest guards, relay mesh backbone, scout pickets and rear casualty stations.
- Builds the Hungarian cost matrix and solves the complete agent -> slot matching.

Canonical role taxonomy is unchanged: tracker, interceptor, relay, scout.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment

# ---------------------------------------------------------------------------
# Canonical roles & doctrinal constants
# ---------------------------------------------------------------------------
ROLE_INTERCEPTOR = "interceptor"
ROLE_TRACKER = "tracker"
ROLE_RELAY = "relay"
ROLE_SCOUT = "scout"
CANONICAL_ROLES: Tuple[str, ...] = (ROLE_INTERCEPTOR, ROLE_TRACKER, ROLE_RELAY, ROLE_SCOUT)

# Internal slot kinds (lattice bookkeeping only; never published on /usv_{i}/role)
KIND_STERN = "stern"          # interceptor terminal capture corridor
KIND_RING = "ring"            # tracker containment ring station
KIND_GUARD = "guard"          # tracker holding a neutralized hull under arrest
KIND_MESH = "mesh"            # relay comms backbone station
KIND_PICKET = "picket"        # scout wide-area early warning station
KIND_CASUALTY = "casualty"    # rear station for combat losses / dead-in-water hulls

# Internal demand tags (never published as roles).
DEMAND_GARRISON_INT = "garrison_interceptor"
DEMAND_GARRISON_TRK = "garrison_tracker"

# Minimum health a vessel needs to be trusted with each role.
ROLE_FITNESS_FLOOR: Dict[str, float] = {
    ROLE_INTERCEPTOR: 0.70,   # needs full thrust for the 3.5 m/s terminal sprint
    ROLE_TRACKER: 0.55,       # needs reliable stationkeeping on the ring
    ROLE_RELAY: 0.35,         # comms duty: low propulsion demand
    ROLE_SCOUT: 0.25,         # outer picket: lowest demand
}

# Below this health a vessel is a combat loss: excluded from the establishment entirely.
CASUALTY_FLOOR = 0.25

# Cost-matrix shaping weights.
SOFT_FITNESS_WEIGHT: Dict[str, float] = {
    ROLE_INTERCEPTOR: 90.0,
    ROLE_TRACKER: 45.0,
    ROLE_RELAY: 10.0,
    ROLE_SCOUT: 8.0,
}
RETENTION_BONUS: Dict[str, float] = {
    ROLE_INTERCEPTOR: 55.0,
    ROLE_TRACKER: 35.0,
    ROLE_RELAY: 25.0,
    ROLE_SCOUT: 15.0,
}
RELAY_DEGRADED_FAVOR = 30.0      # damaged-but-alive vessels are preferred for comms duty
# Cross-wing churn penalty. This must dominate the ordinary distance differences between
# stations on two different wings (hostiles are typically 50-100 m apart), otherwise the
# assignment is nearly degenerate for the drones sitting between two wings and they bounce
# from one hostile to the other tick after tick. The penalty is waived automatically once a
# wing's hostile is neutralized, because `live_target_ids` no longer contains it -- so a
# genuine re-task after an arrest is still free.
WING_SWITCH_PENALTY = 120.0
# ...plus a scale-invariant component. A fixed penalty cannot work at every range: during the
# launch transit the fleet is bunched ~85 m out and the hostiles are ~90 m apart, so station
# distances to two different wings differ by far more than any sensible fixed cost and the
# assignment is nearly degenerate. Charging a FRACTION of the distance as well means a switch
# is only ever chosen when the other wing is dramatically closer, at any range.
WING_SWITCH_DISTANCE_FRACTION = 0.5
INFEASIBLE_COST = 1.0e5          # soft-infinite barrier when a fit alternative exists
SCARCITY_PENALTY_SCALE = 400.0   # graded barrier when no fit alternative exists
CASUALTY_IN_ROLE_PENALTY = 1.0e6  # a wreck must never hold a doctrinal role
FIT_AT_CASUALTY_PENALTY = 1.0e4   # a fit vessel should not park at a rear casualty station

# Geometry defaults (metres).
R_RING_BASE = 20.0
R_RING_PINCER_FLOOR = 15.5   # just outside the shield's 14.0 m non-interceptor envelope
R_STERN_CORRIDOR = 4.8
R_ARREST_GUARD = 15.5
R_GARRISON_ESCORT = 9.5      # strike element stationed close around an arrested hull

# A garrison ring precesses about its arrested hull at this rate, so the station each vessel
# is holding sweeps around the hull and the garrison visibly CIRCLES it instead of parking.
GARRISON_SPIN_RATE = 0.10    # rad/s
GARRISON_TRACKERS = 3        # ring trackers per arrested hull at full strength
R_MESH_BACKBONE = 30.0
# Minimum standoff any fleet-level station (mesh backbone, picket) keeps from a hostile hull.
# A bridging relay placed at the formation centroid can otherwise land exactly on a hull --
# with three collinear hostiles the centroid IS the middle one's position.
R_FLEET_STATION_STANDOFF = 25.0
R_PICKET = 45.0
R_CASUALTY_STATION = 60.0

# Wing merge hysteresis (metres between two hostile targets). The band is deliberately WIDE:
# every merge or split invalidates the station lattice and forces a full fleet reshuffle, so a
# narrow band turns the hostiles' ordinary weaving into continuous reallocation churn with
# drones repeatedly turning around for a different hostile.
WING_MERGE_RADIUS = 22.0
WING_SPLIT_RADIUS = 40.0

MAX_TRACKERS_PER_WING = 6

# Width of a wing-priority threat band, in m/s. Surplus force is allocated by band, and within
# a band strictly by wing id, so the allocation is stable. The band is wider than the hostiles'
# whole evasion envelope (contested 1.15 -> burst 1.95 m/s) on purpose: those swings are normal
# evasive behaviour, not a change in how dangerous a hostile is, and re-ranking on them moves
# the surplus tracker slot between wings every few seconds, which makes that drone shuttle
# between hostiles for the entire engagement. A genuinely faster class of hostile lands in a
# higher band and does draw the surplus force.
THREAT_BAND = 2.5


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------
@dataclass
class TargetState:
    """Live tactical picture for one hostile target."""
    target_id: int
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0
    speed: float = 0.0
    defusal_progress: float = 0.0
    neutralized: bool = False

    @property
    def threat(self) -> float:
        """Threat score: fast, undefused hostiles are engaged with surplus force first."""
        if self.neutralized:
            return -1.0
        return self.speed * (1.0 - min(1.0, self.defusal_progress / 100.0)) + 0.05


@dataclass
class Wing:
    """A tactical strike wing: one containment problem, one or more merged targets."""
    wing_id: int
    target_ids: Tuple[int, ...]
    center: Tuple[float, float]
    ref_heading: float
    ring_radius: float
    merged: bool = False
    neutralized: bool = False
    threat: float = 0.0

    @property
    def primary_target_id(self) -> int:
        return self.target_ids[0]


@dataclass
class Slot:
    """One geometric station the fleet must man."""
    key: str                       # stable identity across ticks, drives retention hysteresis
    role: str                      # canonical role published on /usv_{i}/role
    kind: str                      # internal lattice kind (KIND_*)
    point: Tuple[float, float]
    target_id: int                 # wing affiliation; published as assigned_target_id
    phase_angle: float = 0.0       # bearing on the ring, for formation phase-locking
    ring_radius: float = 0.0


@dataclass
class Establishment:
    """The role quota the fleet must man this tick."""
    interceptors: Dict[int, int] = field(default_factory=dict)   # wing_id -> count
    trackers: Dict[int, int] = field(default_factory=dict)       # wing_id -> count
    guards: Dict[int, int] = field(default_factory=dict)         # wing_id -> garrison tracker count
    garrison_interceptors: Dict[int, int] = field(default_factory=dict)  # wing_id -> count
    relays: int = 0
    scouts: int = 0
    casualty_stations: int = 0
    undermanned_roles: Tuple[str, ...] = ()    # role classes with no slot at all
    unengaged_wings: Tuple[int, ...] = ()      # live hostiles with no interceptor
    unshadowed_wings: Tuple[int, ...] = ()     # live hostiles with no containment tracker
    notes: Tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return (
            sum(self.interceptors.values())
            + sum(self.trackers.values())
            + sum(self.guards.values())
            + sum(self.garrison_interceptors.values())
            + self.relays
            + self.scouts
        )

    def role_totals(self) -> Dict[str, int]:
        return {
            ROLE_INTERCEPTOR: (sum(self.interceptors.values())
                               + sum(self.garrison_interceptors.values())),
            ROLE_TRACKER: sum(self.trackers.values()) + sum(self.guards.values()),
            ROLE_RELAY: self.relays,
            ROLE_SCOUT: self.scouts,
        }

    def garrison_total(self, wing_id: int) -> int:
        """Vessels committed to garrisoning one arrested hull."""
        return self.guards.get(wing_id, 0) + self.garrison_interceptors.get(wing_id, 0)


@dataclass
class Assignment:
    """Resolved agent -> slot pairing."""
    agent_id: int
    role: str
    kind: str
    target_id: int
    slot_key: str
    slot_point: Tuple[float, float]
    phase_angle: float
    ring_radius: float
    cost: float
    is_casualty_station: bool = False
    below_fitness_floor: bool = False


# ---------------------------------------------------------------------------
# Angle helpers
# ---------------------------------------------------------------------------
def wrap_angle(a: float) -> float:
    """Wraps an angle into [-pi, pi)."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def circular_mean(angles: Sequence[float]) -> float:
    """Circular mean of a set of bearings; 0.0 for an empty set."""
    if not angles:
        return 0.0
    s = sum(math.sin(a) for a in angles)
    c = sum(math.cos(a) for a in angles)
    if abs(s) < 1e-12 and abs(c) < 1e-12:
        return 0.0
    return math.atan2(s, c)


# ---------------------------------------------------------------------------
# Wing grouping (with merge/split hysteresis)
# ---------------------------------------------------------------------------
def group_wings(
    targets: Sequence[TargetState],
    base_ring: float = R_RING_BASE,
    merge_radius: float = WING_MERGE_RADIUS,
    split_radius: float = WING_SPLIT_RADIUS,
    previously_merged: FrozenSet[FrozenSet[int]] = frozenset(),
) -> List[Wing]:
    """
    Partitions hostile targets into tactical wings.

    Two live targets share a wing when they are close enough that separate containment rings
    would overlap -- which would otherwise trap trackers between two exclusion envelopes.
    Merge engages at merge_radius and only releases at the wider split_radius, so wings do
    not chatter when targets hold station near the threshold.
    """
    live = [t for t in targets if not t.neutralized]
    dead = [t for t in targets if t.neutralized]

    parent: Dict[int, int] = {t.target_id: t.target_id for t in live}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            ti, tj = live[i], live[j]
            d = math.hypot(ti.x - tj.x, ti.y - tj.y)
            pair = frozenset((ti.target_id, tj.target_id))
            threshold = split_radius if pair in previously_merged else merge_radius
            if d <= threshold:
                union(ti.target_id, tj.target_id)

    groups: Dict[int, List[TargetState]] = {}
    for t in live:
        groups.setdefault(find(t.target_id), []).append(t)

    wings: List[Wing] = []
    for root in sorted(groups.keys()):
        members = sorted(groups[root], key=lambda t: t.target_id)
        cx = sum(t.x for t in members) / len(members)
        cy = sum(t.y for t in members) / len(members)
        heading = circular_mean([t.heading for t in members])

        max_sep = 0.0
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                max_sep = max(max_sep, math.hypot(members[i].x - members[j].x,
                                                  members[i].y - members[j].y))

        wings.append(Wing(
            wing_id=members[0].target_id,
            target_ids=tuple(t.target_id for t in members),
            center=(cx, cy),
            ref_heading=heading,
            # A merged wing needs a wider ring to contain the whole hostile cluster.
            ring_radius=base_ring + 0.5 * max_sep,
            merged=len(members) > 1,
            neutralized=False,
            threat=max(t.threat for t in members),
        ))

    # Neutralized hulls each keep a private guard wing so an arrest is never abandoned.
    for t in dead:
        wings.append(Wing(
            wing_id=t.target_id,
            target_ids=(t.target_id,),
            center=(t.x, t.y),
            ref_heading=t.heading,
            ring_radius=R_ARREST_GUARD,
            merged=False,
            neutralized=True,
            threat=-1.0,
        ))

    return wings


def merged_pairs(wings: Sequence[Wing]) -> FrozenSet[FrozenSet[int]]:
    """Returns the set of target pairs currently sharing a wing, for next-tick hysteresis."""
    pairs: Set[FrozenSet[int]] = set()
    for w in wings:
        ids = w.target_ids
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.add(frozenset((ids[i], ids[j])))
    return frozenset(pairs)


# ---------------------------------------------------------------------------
# Establishment resolution
# ---------------------------------------------------------------------------
def _demand_sequence(
    active_wing_ids: Sequence[int],
    garrison_wing_ids: Sequence[int],
) -> List[Tuple[str, Optional[int]]]:
    """
    The doctrinal fill order. Earlier demands are manned first when the fleet is too small, so
    scarcity degrades predictably instead of leaving a critical role class unmanned.

    Live wings and garrison wings are ordered into one list -- live first, so a hostile still
    under way is never stripped of force to garrison an arrested one -- and then filled with a
    single rule. That matters for the no-downtime invariant: giving every hull its strike
    element before any hull gets a tracker would leave the tracker role unmanned on a small
    fleet, so the LEAD wing is completed first and the fleet-level roles come next.

    P1  interceptor on the lead wing          (live if any hostile is still under way)
    P2  tracker on the lead wing
    P3  one relay            - the mesh backbone keeps the whole formation coordinated
    P4  one scout            - wide-area early warning is never blind
    P5  interceptor per remaining wing
    P6  tracker per remaining wing
    P7  rings built out, every wing in step   (trackers 2 and 3 each)
    P8  second relay         - bridges dispersed garrisons
    P9  second scout
    P10+ surplus trackers, round-robin across every wing, live and arrested alike

    An ARRESTED hull is not released from the fleet's responsibility: it keeps its own standing
    garrison -- a strike element stationed on it and a tracker ring circling it -- built out in
    step with the live rings, so once every hostile is down the fleet has divided itself
    roughly equally between them rather than massing in one place.
    """
    # (wing_id, interceptor demand, tracker demand). Live wings lead.
    wing_specs: List[Tuple[int, str, str]] = (
        [(w, ROLE_INTERCEPTOR, ROLE_TRACKER) for w in active_wing_ids]
        + [(w, DEMAND_GARRISON_INT, DEMAND_GARRISON_TRK) for w in garrison_wing_ids]
    )

    demands: List[Tuple[str, Optional[int]]] = []

    if wing_specs:
        lead, lead_int, lead_trk = wing_specs[0]
        demands.append((lead_int, lead))
        demands.append((lead_trk, lead))

    demands.append((ROLE_RELAY, None))
    demands.append((ROLE_SCOUT, None))

    for wing_id, int_demand, _ in wing_specs[1:]:
        demands.append((int_demand, wing_id))
    for wing_id, _, trk_demand in wing_specs[1:]:
        demands.append((trk_demand, wing_id))

    # Rings are built out in step so force divides evenly across every hull the fleet is
    # responsible for, instead of one engagement absorbing all of it.
    for _ in range(GARRISON_TRACKERS - 1):
        for wing_id, _, trk_demand in wing_specs:
            demands.append((trk_demand, wing_id))

    demands.append((ROLE_RELAY, None))
    demands.append((ROLE_SCOUT, None))

    for _ in range(MAX_TRACKERS_PER_WING - GARRISON_TRACKERS):
        for wing_id, _, trk_demand in wing_specs:
            demands.append((trk_demand, wing_id))

    return demands


def resolve_establishment(
    num_fit_agents: int,
    wings: Sequence[Wing],
    num_casualties: int = 0,
) -> Establishment:
    """
    Resolves the role quota the fleet must man from the live target set and the number of
    combat-fit vessels. Role slots are a function of the mission, not of who happens to still
    hold a role, which is what makes the no-downtime invariant structural: an incumbent can be
    destroyed, but its slot cannot be, so the next Hungarian solve backfills it.
    """
    # Wing priority decides which wing receives surplus force: by THREAT_BAND first, then
    # strictly by wing id, so ordinary evasive speed swings cannot re-rank the wings.
    active = sorted(
        (w for w in wings if not w.neutralized),
        key=lambda w: (-int(w.threat / THREAT_BAND), w.wing_id),
    )
    guards = sorted((w for w in wings if w.neutralized), key=lambda w: w.wing_id)

    # Arrested hulls are handled through the garrison demands rather than being promoted into
    # the live-wing list, so the fleet keeps a complete establishment with every hostile down.
    active_ids = [w.wing_id for w in active]
    guard_ids = [w.wing_id for w in guards]

    est = Establishment(casualty_stations=max(0, num_casualties))
    budget = max(0, num_fit_agents)
    notes: List[str] = []

    if budget == 0:
        est.undermanned_roles = CANONICAL_ROLES
        est.unengaged_wings = tuple(w.wing_id for w in active)
        est.unshadowed_wings = tuple(w.wing_id for w in active)
        est.notes = ("No combat-fit vessels remain; establishment cannot be manned.",)
        return est

    for role, wing_id in _demand_sequence(active_ids, guard_ids):
        if budget <= 0:
            break
        if role == ROLE_INTERCEPTOR:
            est.interceptors[wing_id] = est.interceptors.get(wing_id, 0) + 1
        elif role == ROLE_TRACKER:
            est.trackers[wing_id] = est.trackers.get(wing_id, 0) + 1
        elif role == DEMAND_GARRISON_TRK:
            est.guards[wing_id] = est.guards.get(wing_id, 0) + 1
        elif role == DEMAND_GARRISON_INT:
            est.garrison_interceptors[wing_id] = est.garrison_interceptors.get(wing_id, 0) + 1
        elif role == ROLE_RELAY:
            est.relays += 1
        else:
            est.scouts += 1
        budget -= 1

    # Any remaining hulls become additional scouts: wide-area coverage absorbs surplus.
    if budget > 0:
        est.scouts += budget
        budget = 0

    # Report, rather than hide, any role class the surviving fleet could not man at all.
    totals = est.role_totals()
    undermanned = [role for role in CANONICAL_ROLES if totals[role] < 1]
    for role in undermanned:
        notes.append(f"Fleet of {num_fit_agents} fit vessel(s) cannot man the {role} role.")

    # Per-wing shortfalls are a weaker condition than a vacant role class: the hostile is
    # still tracked through the mesh, it just has no dedicated force committed to it.
    unengaged = [w.wing_id for w in active if est.interceptors.get(w.wing_id, 0) < 1]
    unshadowed = [w.wing_id for w in active if est.trackers.get(w.wing_id, 0) < 1]
    for wid in unengaged:
        notes.append(f"Hostile {wid} has no interceptor committed; fleet is below strength.")
    for wid in unshadowed:
        notes.append(f"Hostile {wid} has no containment tracker; fleet is below strength.")

    est.undermanned_roles = tuple(undermanned)
    est.unengaged_wings = tuple(unengaged)
    est.unshadowed_wings = tuple(unshadowed)
    est.notes = tuple(notes)
    return est


# ---------------------------------------------------------------------------
# Slot lattice construction
# ---------------------------------------------------------------------------
def pincer_ring_radius(
    base_radius: float,
    collapse_progress: float,
    floor: float = R_RING_PINCER_FLOOR,
) -> float:
    """
    Containment ring radius during the pincer squeeze. collapse_progress ramps 0 -> 1 once a
    strike is authorized, walking the ring in from its standoff radius to the arrest radius.
    """
    p = min(1.0, max(0.0, collapse_progress))
    return base_radius - (base_radius - floor) * p


def build_slot_lattice(
    establishment: Establishment,
    wings: Sequence[Wing],
    collapse_progress: Dict[int, float] | None = None,
    garrison_spin: float = 0.0,
    engagement_center: Optional[Tuple[float, float]] = None,
    engagement_heading: float = 0.0,
) -> List[Slot]:
    """
    Builds the geometric station list the fleet will be matched onto.

    Stations are emitted in doctrinal order so the lattice is deterministic tick to tick:
    stern corridors, containment rings, arrest guards, mesh backbone, pickets, casualties.
    """
    collapse = collapse_progress or {}
    slots: List[Slot] = []
    wing_by_id = {w.wing_id: w for w in wings}

    # Engagement centroid anchors the fleet-level (relay / scout / casualty) stations so they
    # bridge every wing instead of orbiting a single hostile.
    if engagement_center is None:
        live = [w for w in wings if not w.neutralized] or list(wings)
        if live:
            engagement_center = (
                sum(w.center[0] for w in live) / len(live),
                sum(w.center[1] for w in live) / len(live),
            )
            engagement_heading = circular_mean([w.ref_heading for w in live])
        else:
            engagement_center = (0.0, 0.0)
    ex, ey = engagement_center

    # 1. Interceptor terminal capture corridors, dead astern of each hostile.
    for wing_id in sorted(establishment.interceptors.keys()):
        wing = wing_by_id.get(wing_id)
        if wing is None:
            continue
        count = establishment.interceptors[wing_id]
        for k in range(count):
            # A second interceptor on the same wing attacks from the opposite quarter.
            bearing = wrap_angle(wing.ref_heading + math.pi + (0.0 if k == 0 else 0.7 * (-1) ** k))
            slots.append(Slot(
                key=f"int:w{wing_id}:#{k}",
                role=ROLE_INTERCEPTOR,
                kind=KIND_STERN,
                point=(wing.center[0] + R_STERN_CORRIDOR * math.cos(bearing),
                       wing.center[1] + R_STERN_CORRIDOR * math.sin(bearing)),
                target_id=wing.primary_target_id,
                phase_angle=bearing,
                ring_radius=R_STERN_CORRIDOR,
            ))

    # 2. Containment ring stations, phase-locked to the hostile's heading.
    for wing_id in sorted(establishment.trackers.keys()):
        wing = wing_by_id.get(wing_id)
        if wing is None:
            continue
        count = establishment.trackers[wing_id]
        radius = pincer_ring_radius(wing.ring_radius, collapse.get(wing_id, 0.0))
        for k in range(count):
            # Stations stay EVENLY phased. An earlier revision biased the two flank stations
            # inward during the pincer to "seal the escape sector"; live measurement showed it
            # did the opposite -- displacing two stations opened a ~117 deg hole elsewhere on
            # the ring and Section 5 criterion 2 failed for the whole engagement. Even spacing
            # minimises the maximum angular gap by construction, which is precisely what the
            # containment criteria measure, so the squeeze is delivered by collapsing the
            # radius alone.
            phase = wrap_angle(wing.ref_heading + (2.0 * math.pi / max(1, count)) * k)
            slots.append(Slot(
                key=f"trk:w{wing_id}:#{k}",
                role=ROLE_TRACKER,
                kind=KIND_RING,
                point=(wing.center[0] + radius * math.cos(phase),
                       wing.center[1] + radius * math.sin(phase)),
                target_id=wing.primary_target_id,
                phase_angle=phase,
                ring_radius=radius,
            ))

    # 3. Standing garrisons on arrested hulls. Each garrison is a complete sub-formation: a
    #    strike element stationed close in, and a tracker ring circling the hull. Both rings
    #    PRECESS with `garrison_spin`, so the station each vessel holds sweeps around the hull
    #    and the garrison visibly circles it rather than parking on a fixed bearing.
    for wing_id in sorted(set(establishment.garrison_interceptors) | set(establishment.guards)):
        wing = wing_by_id.get(wing_id)
        if wing is None:
            continue

        n_int = establishment.garrison_interceptors.get(wing_id, 0)
        for k in range(n_int):
            phase = wrap_angle(wing.ref_heading + garrison_spin
                               + (2.0 * math.pi / max(1, n_int)) * k)
            slots.append(Slot(
                key=f"gint:w{wing_id}:#{k}",
                role=ROLE_INTERCEPTOR,
                kind=KIND_GUARD,
                point=(wing.center[0] + R_GARRISON_ESCORT * math.cos(phase),
                       wing.center[1] + R_GARRISON_ESCORT * math.sin(phase)),
                target_id=wing.primary_target_id,
                phase_angle=phase,
                ring_radius=R_GARRISON_ESCORT,
            ))

        n_trk = establishment.guards.get(wing_id, 0)
        for k in range(n_trk):
            # Counter-phased against the strike element so the two rings do not stack up.
            phase = wrap_angle(wing.ref_heading + math.pi + garrison_spin
                               + (2.0 * math.pi / max(1, n_trk)) * k)
            slots.append(Slot(
                key=f"grd:w{wing_id}:#{k}",
                role=ROLE_TRACKER,
                kind=KIND_GUARD,
                point=(wing.center[0] + R_ARREST_GUARD * math.cos(phase),
                       wing.center[1] + R_ARREST_GUARD * math.sin(phase)),
                target_id=wing.primary_target_id,
                phase_angle=phase,
                ring_radius=R_ARREST_GUARD,
            ))

    # 4. Relay mesh backbone. With the fleet dispersed across several garrisons the backbone's
    #    job is to BRIDGE them, so relays are stationed on the chords between wing centres
    #    rather than all sitting astern of one engagement box. A single relay holds the
    #    centroid, where it is in range of every garrison at once.
    centres = [w.center for w in wings]
    bridge_points: List[Tuple[float, float]] = []
    if len(centres) >= 2:
        for a in range(len(centres)):
            for b in range(a + 1, len(centres)):
                bridge_points.append(((centres[a][0] + centres[b][0]) / 2.0,
                                      (centres[a][1] + centres[b][1]) / 2.0))

    for k in range(establishment.relays):
        if bridge_points and establishment.relays > 1:
            point = clear_of_hulls(bridge_points[k % len(bridge_points)], wings)
            bearing = math.atan2(point[1] - ey, point[0] - ex)
            radius = math.hypot(point[0] - ex, point[1] - ey)
        elif bridge_points:
            # One relay: as central as possible, in reach of every garrison, but clear of hulls.
            point = clear_of_hulls((ex, ey), wings)
            bearing = math.atan2(point[1] - ey, point[0] - ex) if point != (ex, ey) else \
                wrap_angle(engagement_heading + math.pi)
            radius = math.hypot(point[0] - ex, point[1] - ey)
        else:
            spread = 0.0 if establishment.relays == 1 else (k - (establishment.relays - 1) / 2.0) * 0.55
            bearing = wrap_angle(engagement_heading + math.pi + 0.8 + spread)
            point = (ex + R_MESH_BACKBONE * math.cos(bearing),
                     ey + R_MESH_BACKBONE * math.sin(bearing))
            radius = R_MESH_BACKBONE
        slots.append(Slot(
            key=f"rly:fleet:#{k}",
            role=ROLE_RELAY,
            kind=KIND_MESH,
            point=point,
            target_id=_nearest_target_id(wings, point),
            phase_angle=bearing,
            ring_radius=radius,
        ))

    # 5. Scout pickets: wide-area early warning around the WHOLE formation. The picket radius
    #    grows with how far apart the garrisons are, so a dispersed fleet is not screened by a
    #    ring that sits inside it.
    extent = 0.0
    for a in range(len(centres)):
        for b in range(a + 1, len(centres)):
            extent = max(extent, math.hypot(centres[a][0] - centres[b][0],
                                            centres[a][1] - centres[b][1]))
    picket_radius = max(R_PICKET, extent * 0.5 + 25.0)

    for k in range(establishment.scouts):
        bearing = wrap_angle(
            engagement_heading + 0.5 + (2.0 * math.pi / max(1, establishment.scouts)) * k
        )
        point = clear_of_hulls(
            (ex + picket_radius * math.cos(bearing), ey + picket_radius * math.sin(bearing)),
            wings)
        slots.append(Slot(
            key=f"sct:fleet:#{k}",
            role=ROLE_SCOUT,
            kind=KIND_PICKET,
            point=point,
            target_id=_nearest_target_id(wings, point),
            phase_angle=bearing,
            ring_radius=picket_radius,
        ))

    # 6. Rear casualty stations: dead-in-water hulls drift clear of the engagement.
    for k in range(establishment.casualty_stations):
        bearing = wrap_angle(
            engagement_heading + math.pi + (2.0 * math.pi / max(1, establishment.casualty_stations)) * k * 0.35
        )
        point = (ex + R_CASUALTY_STATION * math.cos(bearing), ey + R_CASUALTY_STATION * math.sin(bearing))
        slots.append(Slot(
            key=f"cas:fleet:#{k}",
            role=ROLE_SCOUT,
            kind=KIND_CASUALTY,
            point=point,
            target_id=_nearest_target_id(wings, point),
            phase_angle=bearing,
            ring_radius=R_CASUALTY_STATION,
        ))

    return slots


def _formation_axis(wings: Sequence[Wing]) -> Tuple[float, float]:
    """Unit vector along the widest separation in the formation; +x for a single wing."""
    best = (1.0, 0.0)
    best_d = 0.0
    for a in range(len(wings)):
        for b in range(a + 1, len(wings)):
            dx = wings[b].center[0] - wings[a].center[0]
            dy = wings[b].center[1] - wings[a].center[1]
            d = math.hypot(dx, dy)
            if d > best_d:
                best_d = d
                best = (dx / d, dy / d)
    return best


def clear_of_hulls(
    point: Tuple[float, float],
    wings: Sequence[Wing],
    standoff: float = R_FLEET_STATION_STANDOFF,
) -> Tuple[float, float]:
    """
    Pushes a fleet-level station clear of every hostile hull while keeping it as central as
    possible, so a bridging relay or a picket is never commanded onto a hull the safety shield
    will then spend the rest of the mission shoving it away from.
    """
    if not wings:
        return point

    px, py = point
    for _ in range(8):
        nearest = min(wings, key=lambda w: math.hypot(w.center[0] - px, w.center[1] - py))
        dx = px - nearest.center[0]
        dy = py - nearest.center[1]
        d = math.hypot(dx, dy)
        if d >= standoff:
            break
        if d < 1e-6:
            # Sitting exactly on a hull: break the tie across the formation axis.
            ax, ay = _formation_axis(wings)
            dx, dy, d = -ay, ax, 1.0
        px = nearest.center[0] + (dx / d) * standoff
        py = nearest.center[1] + (dy / d) * standoff
    return (px, py)


def _nearest_target_id(wings: Sequence[Wing], point: Tuple[float, float]) -> int:
    """Wing affiliation for fleet-level stations: whichever hostile they are closest to."""
    if not wings:
        return 0
    best = min(wings, key=lambda w: math.hypot(w.center[0] - point[0], w.center[1] - point[1]))
    return best.primary_target_id


# ---------------------------------------------------------------------------
# Cost matrix & assignment
# ---------------------------------------------------------------------------
def _fitness_cost(health: float, role: str, hard_barrier: bool) -> Tuple[float, bool]:
    """
    Cost of trusting a vessel of this health with this role.

    When enough fit vessels exist to man the quota the barrier is effectively hard, so an
    unfit vessel is never given the role. When they do not, the barrier degrades to a graded
    penalty: the least-unfit vessel takes the slot rather than leaving the role unmanned.
    """
    floor = ROLE_FITNESS_FLOOR[role]
    if health >= floor:
        return (1.0 - health) * SOFT_FITNESS_WEIGHT[role], False
    if hard_barrier:
        return INFEASIBLE_COST, True
    return (floor - health) * SCARCITY_PENALTY_SCALE, True


def build_cost_matrix(
    agent_ids: Sequence[int],
    agent_positions: Dict[int, Tuple[float, float]],
    agent_health: Dict[int, float],
    slots: Sequence[Slot],
    previous_slot_keys: Dict[int, str] | None = None,
    previous_target: Dict[int, int] | None = None,
    live_target_ids: FrozenSet[int] = frozenset(),
    suppress_retention: bool = False,
) -> Tuple[np.ndarray, Dict[str, bool]]:
    """
    Builds the square Hungarian cost matrix over agents x slots.

    Returns the matrix and, per role, whether its fitness barrier was hard this tick
    (i.e. whether the fleet actually had enough fit vessels for the quota).
    """
    prev_keys = previous_slot_keys or {}
    prev_target = previous_target or {}
    n = len(agent_ids)
    if n == 0 or len(slots) != n:
        raise ValueError(f"Cost matrix must be square: {n} agents vs {len(slots)} slots")

    # A role's barrier is hard only while fit alternatives outnumber the quota.
    quota: Dict[str, int] = {r: 0 for r in CANONICAL_ROLES}
    for s in slots:
        if s.kind != KIND_CASUALTY:
            quota[s.role] += 1
    hard: Dict[str, bool] = {}
    for role in CANONICAL_ROLES:
        fit = sum(1 for a in agent_ids if agent_health.get(a, 1.0) >= ROLE_FITNESS_FLOOR[role])
        hard[role] = fit >= quota[role]

    cost = np.zeros((n, n), dtype=np.float64)
    for i, aid in enumerate(agent_ids):
        ax, ay = agent_positions.get(aid, (0.0, 0.0))
        h = float(agent_health.get(aid, 1.0))
        is_casualty = h < CASUALTY_FLOOR

        for j, slot in enumerate(slots):
            dist = math.hypot(ax - slot.point[0], ay - slot.point[1])

            if slot.kind == KIND_CASUALTY:
                # Only genuine combat losses belong at a rear station.
                cost[i, j] = dist if is_casualty else dist + FIT_AT_CASUALTY_PENALTY
                continue

            if is_casualty:
                # A dead hull must never hold a doctrinal role while anyone else can.
                cost[i, j] = dist + CASUALTY_IN_ROLE_PENALTY
                continue

            c = dist
            fitness, _ = _fitness_cost(h, slot.role, hard[slot.role])
            c += fitness

            # Damaged-but-alive vessels are the natural choice for comms duty.
            if slot.role == ROLE_RELAY:
                c -= (1.0 - h) * RELAY_DEGRADED_FAVOR

            # Cross-wing churn is discouraged only while the old wing is still live, so force
            # freed by an arrest re-tasks for nothing.
            prev_t = prev_target.get(aid)
            if prev_t is not None and prev_t != slot.target_id and prev_t in live_target_ids:
                c += WING_SWITCH_PENALTY + WING_SWITCH_DISTANCE_FRACTION * dist

            # Slot retention keeps the formation steady -- suppressed when a role fell vacant,
            # so the fleet re-shuffles immediately instead of clinging to stale stations.
            if not suppress_retention and prev_keys.get(aid) == slot.key:
                c -= RETENTION_BONUS[slot.role]

            cost[i, j] = c

    return cost, hard


def solve_assignment(
    agent_ids: Sequence[int],
    agent_positions: Dict[int, Tuple[float, float]],
    agent_health: Dict[int, float],
    slots: Sequence[Slot],
    previous_slot_keys: Dict[int, str] | None = None,
    previous_target: Dict[int, int] | None = None,
    live_target_ids: FrozenSet[int] = frozenset(),
    suppress_retention: bool = False,
) -> Dict[int, Assignment]:
    """Solves the complete agent -> slot matching. Every slot is filled by exactly one agent."""
    ids = list(agent_ids)
    cost, _ = build_cost_matrix(
        ids, agent_positions, agent_health, slots,
        previous_slot_keys=previous_slot_keys,
        previous_target=previous_target,
        live_target_ids=live_target_ids,
        suppress_retention=suppress_retention,
    )
    row_ind, col_ind = linear_sum_assignment(cost)

    out: Dict[int, Assignment] = {}
    for r, c in zip(row_ind, col_ind):
        aid = ids[r]
        slot = slots[c]
        h = float(agent_health.get(aid, 1.0))
        out[aid] = Assignment(
            agent_id=aid,
            role=slot.role,
            kind=slot.kind,
            target_id=slot.target_id,
            slot_key=slot.key,
            slot_point=slot.point,
            phase_angle=slot.phase_angle,
            ring_radius=slot.ring_radius,
            cost=float(cost[r, c]),
            is_casualty_station=(slot.kind == KIND_CASUALTY),
            below_fitness_floor=(h < ROLE_FITNESS_FLOOR[slot.role]),
        )
    return out


def audit_manning(
    establishment: Establishment,
    assignments: Dict[int, Assignment],
    agent_health: Dict[int, float],
) -> Dict[str, Dict[str, int]]:
    """
    Post-assignment manning audit, per canonical role:
      required   - slots the establishment demanded
      manned     - slots actually filled
      fit        - slots filled by a vessel at or above the role's fitness floor
    `manned` below `required` means a genuine vacancy; `fit` below `manned` means the role is
    held under degraded doctrine. Both are reported, never silently tolerated.
    """
    audit: Dict[str, Dict[str, int]] = {
        role: {"required": 0, "manned": 0, "fit": 0} for role in CANONICAL_ROLES
    }
    for role, count in establishment.role_totals().items():
        audit[role]["required"] = count

    for a in assignments.values():
        if a.is_casualty_station:
            continue
        audit[a.role]["manned"] += 1
        if agent_health.get(a.agent_id, 1.0) >= ROLE_FITNESS_FLOOR[a.role]:
            audit[a.role]["fit"] += 1

    return audit
