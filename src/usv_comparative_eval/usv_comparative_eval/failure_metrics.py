"""
USV Swarm Simulation - Impartial Comparative Scorecard
Author: Principal Enterprise Engineer
Stage: 2 (Architecture Ablation Study)

Pure (rclpy-free) metric engine for the architecture comparison.

The SAME scorer, fed from the SAME ground-truth topics, judges both stacks:
  * the full TritonSwarm architecture (establishment allocator + CBF shield + EKF mesh +
    FA-MAPPO/PN/orbit guidance + fault diagnosis + autonomous damage control), and
  * the ablated baseline swarm (static roles, greedy nearest-target pursuit, no safety
    filter, raw unfiltered sensing, no fault response).

Nothing here is specific to either stack: metrics are computed from vessel and hostile
kinematics, ground-truth health, and the published belief, so neither architecture can score
itself favourably. Containment geometry in particular is judged purely geometrically -- any
tracker or interceptor inside the ring band counts, regardless of what the stack *thinks* it
is assigned to -- so the baseline is not penalised for poor bookkeeping, only for poor results.

Failure modes measured
----------------------
COLLISION          inter-vessel separation below the 4.0 m invariant
HOSTILE_IMPACT     a drone striking a hostile hull: the mission is a NON-DESTRUCTIVE
                   arrest, so a ramming 'capture' is a mission failure even though the
                   target stops moving
ROLE_VACANCY       a canonical role with no fit vessel holding it
CONTAINMENT_FAIL   Section 5 encirclement criteria not satisfied
TRACK_ERROR        published belief vs. ground-truth hostile position
ASSIGNMENT_THRASH  role or target reassignments per minute
STALL              a fit vessel immobile while far from its objective (deadlock)
PATH_WASTE         distance travelled vs. net displacement achieved
ATTRITION          fleet availability over the mission
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

CANONICAL_ROLES: Tuple[str, ...] = ("interceptor", "tracker", "relay", "scout")

# Identical fitness floors to the production allocator, so manning is judged on one standard.
ROLE_FITNESS_FLOOR: Dict[str, float] = {
    "interceptor": 0.70,
    "tracker": 0.55,
    "relay": 0.35,
    "scout": 0.25,
}

D_SAFE = 4.0              # hard inter-vessel collision invariant (m)
D_HULL_STRIKE = 3.0       # centre-to-centre range at which two hulls are in contact (m)
RING_BAND = (8.0, 30.0)   # radial band counted as "on the containment ring" (m)
STALL_SPEED = 0.15        # m/s below which a vessel is considered immobile
# Range from its hostile beyond which an immobile drone is genuinely stuck rather than holding
# a station. It must sit outside EVERY legitimate station radius -- strike staging 22 m, mesh
# backbone 30 m, scout picket 45 m -- or correct stationkeeping is scored as a deadlock. A
# drone 55 m out and not moving is stuck whatever its role, which keeps the metric impartial.
STALL_RANGE = 55.0
CONTAINMENT_MIN = 3       # vessels needed before encirclement geometry is meaningful

# Longest continuous role vacancy still consistent with the no-downtime claim: one 1 Hz
# establishment watchdog period plus margin for the 10 Hz allocator to publish the backfill.
MAX_ACCEPTABLE_VACANCY_RUN = 1.5


@dataclass
class AgentSample:
    """One vessel's ground-truth state for this scoring tick."""
    agent_id: int
    x: float
    y: float
    speed: float = 0.0
    health: float = 1.0
    role: str = "tracker"
    target_id: int = 0

    @property
    def is_fit(self) -> bool:
        return self.health >= ROLE_FITNESS_FLOOR["scout"]


@dataclass
class TargetSample:
    """One hostile's ground-truth state for this scoring tick."""
    target_id: int
    x: float
    y: float
    speed: float = 0.0
    neutralized: bool = False
    defusal_progress: float = 0.0


@dataclass
class ContainmentVerdict:
    """Section 5 encirclement verdict for one hostile."""
    target_id: int
    participants: int
    angular_spread: float
    spread_threshold: float
    criterion_1: bool
    max_gap: float
    gap_threshold: float
    criterion_2: bool
    contained: bool


def wrap_angle(a: float) -> float:
    """Wraps an angle into [-pi, pi)."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def evaluate_containment(
    target: TargetSample,
    agents: Sequence[AgentSample],
    ring_band: Tuple[float, float] = RING_BAND,
) -> ContainmentVerdict:
    """
    Judges Section 5 encirclement geometry for one hostile, purely geometrically.

    An arrested hull counts as contained: the criteria exist to prove escape is impossible,
    and a hull at a standstill under guard cannot escape.
    """
    lo, hi = ring_band
    bearings: List[float] = []
    for a in agents:
        if a.health < 0.30:
            continue
        if a.role not in ("tracker", "interceptor"):
            continue
        d = math.hypot(a.x - target.x, a.y - target.y)
        # An interceptor holding the terminal capture corridor sits INSIDE the tracker ring
        # band by design, so applying the tracker's lower bound to it would exclude the one
        # vessel pinning the hostile. This mirrors the production mission verifier exactly.
        in_band = (d <= hi) if a.role == "interceptor" else (lo <= d <= hi)
        if in_band:
            bearings.append(math.atan2(a.y - target.y, a.x - target.x))

    n = len(bearings)
    spread = 0.0
    max_gap = 2.0 * math.pi
    spread_thresh = 2.0 * math.pi
    gap_thresh = 0.0
    c1 = False
    c2 = False

    if n >= CONTAINMENT_MIN:
        bearings.sort()
        gaps = [(bearings[(k + 1) % n] - bearings[k]) % (2.0 * math.pi) for k in range(n)]
        max_gap = max(gaps)
        spread = 2.0 * math.pi - max_gap
        spread_thresh = 2.0 * math.pi * (1.0 - 1.0 / n) * 0.90
        gap_thresh = (2.0 * math.pi / n) * 1.60
        c1 = spread >= spread_thresh
        c2 = max_gap <= gap_thresh

    return ContainmentVerdict(
        target_id=target.target_id,
        participants=n,
        angular_spread=spread,
        spread_threshold=spread_thresh,
        criterion_1=c1,
        max_gap=max_gap,
        gap_threshold=gap_thresh,
        criterion_2=c2,
        contained=bool(target.neutralized or (c1 and c2)),
    )


def manned_roles(agents: Sequence[AgentSample]) -> Dict[str, int]:
    """Counts, per canonical role, how many FIT vessels actually hold it."""
    counts = {role: 0 for role in CANONICAL_ROLES}
    for a in agents:
        role = (a.role or "").lower()
        if role in counts and a.health >= ROLE_FITNESS_FLOOR[role]:
            counts[role] += 1
    return counts


class SwarmScorecard:
    """Accumulates the comparative failure-mode metrics over a mission."""

    def __init__(
        self,
        architecture: str,
        num_agents: int,
        num_targets: int,
        d_safe: float = D_SAFE,
        ring_band: Tuple[float, float] = RING_BAND,
    ):
        self.architecture = architecture
        self.num_agents = num_agents
        self.num_targets = num_targets
        self.d_safe = d_safe
        self.ring_band = ring_band

        self.mission_time = 0.0

        # Collisions
        self.min_separation = float('inf')
        self.collision_pairs: set = set()
        self.collision_events = 0
        self.seconds_below_safe = 0.0
        self._in_breach: set = set()

        # Role manning
        self.role_vacancy_seconds: Dict[str, float] = {r: 0.0 for r in CANONICAL_ROLES}
        self.any_vacancy_seconds = 0.0
        # Accumulated vacancy conflates "backfilled in 0.4 s" with "unmanned for the rest of
        # the mission". The no-downtime claim is about SUSTAINED vacancy, so the longest
        # continuous run is tracked separately and is what the clean-mission verdict uses.
        self._vacancy_run: Dict[str, float] = {r: 0.0 for r in CANONICAL_ROLES}
        self.max_vacancy_run: Dict[str, float] = {r: 0.0 for r in CANONICAL_ROLES}
        self._any_vacancy_run = 0.0
        self.max_any_vacancy_run = 0.0

        # Hostile hull strikes: a ramming capture is not a non-destructive arrest
        self.min_target_separation = float('inf')
        self.hostile_impact_pairs: set = set()
        self.per_target_impacts: Dict[int, int] = {m: 0 for m in range(num_targets)}

        # Containment. Raw containment time credits an arrested hull as contained, which
        # rewards whoever disabled a hostile earliest regardless of encirclement skill. The
        # ACTIVE figure is measured only while at least one hostile is still live, so it
        # measures the encirclement itself rather than how soon the fight ended.
        self.live_hostile_seconds = 0.0
        self.active_containment_seconds = 0.0
        self.containment_seconds = 0.0
        self.per_target_containment: Dict[int, float] = {m: 0.0 for m in range(num_targets)}

        # Tracking accuracy
        self._belief_sq_sum = 0.0
        self._belief_samples = 0
        self.worst_track_error = 0.0

        # Assignment churn
        self._prev_role: Dict[int, str] = {}
        self._prev_target: Dict[int, int] = {}
        self.role_changes = 0
        self.target_changes = 0

        # Attrition & motion
        self._availability_integral = 0.0
        self.final_fit_count = num_agents
        self.stall_seconds = 0.0
        self._path_length: Dict[int, float] = {}
        self._last_pos: Dict[int, Tuple[float, float]] = {}
        self._first_pos: Dict[int, Tuple[float, float]] = {}
        self.worst_fit_count = num_agents

        # Mission outcome
        self.peak_clean_arrests = 0
        self.peak_arrested = 0
        self.time_to_first_arrest: Optional[float] = None
        self.time_to_all_arrest: Optional[float] = None

    # ------------------------------------------------------------------
    def update(
        self,
        dt: float,
        agents: Sequence[AgentSample],
        targets: Sequence[TargetSample],
        beliefs: Optional[Dict[Tuple[int, int], Tuple[float, float]]] = None,
    ) -> List[ContainmentVerdict]:
        """Folds one scoring tick into the accumulators and returns the containment verdicts."""
        self.mission_time += dt
        live_agents = [a for a in agents if a.is_fit]

        # --- COLLISION ---------------------------------------------------------------
        breaching_now: set = set()
        for i in range(len(agents)):
            for j in range(i + 1, len(agents)):
                a, b = agents[i], agents[j]
                d = math.hypot(a.x - b.x, a.y - b.y)
                self.min_separation = min(self.min_separation, d)
                if d < self.d_safe:
                    pair = frozenset((a.agent_id, b.agent_id))
                    breaching_now.add(pair)
                    if pair not in self._in_breach:
                        self.collision_events += 1
                    self.collision_pairs.add(pair)
        if breaching_now:
            self.seconds_below_safe += dt
        self._in_breach = breaching_now

        # --- HOSTILE_IMPACT ----------------------------------------------------------
        # The objective is a NON-DESTRUCTIVE arrest. A drone that strikes a hostile hull has
        # rammed it, which disables the target but fails the mission's own terms, so clean and
        # destructive captures are counted separately.
        for a in agents:
            if not a.is_fit:
                continue
            for t in targets:
                d = math.hypot(a.x - t.x, a.y - t.y)
                self.min_target_separation = min(self.min_target_separation, d)
                if d < D_HULL_STRIKE:
                    pair = (a.agent_id, t.target_id)
                    if pair not in self.hostile_impact_pairs:
                        self.hostile_impact_pairs.add(pair)
                        self.per_target_impacts[t.target_id] = \
                            self.per_target_impacts.get(t.target_id, 0) + 1

        # --- ROLE_VACANCY ------------------------------------------------------------
        counts = manned_roles(agents)
        vacant_any = False
        for role in CANONICAL_ROLES:
            if counts[role] < 1:
                self.role_vacancy_seconds[role] += dt
                self._vacancy_run[role] += dt
                self.max_vacancy_run[role] = max(
                    self.max_vacancy_run[role], self._vacancy_run[role]
                )
                vacant_any = True
            else:
                self._vacancy_run[role] = 0.0
        if vacant_any:
            self.any_vacancy_seconds += dt
            self._any_vacancy_run += dt
            self.max_any_vacancy_run = max(self.max_any_vacancy_run, self._any_vacancy_run)
        else:
            self._any_vacancy_run = 0.0

        # --- CONTAINMENT_FAIL --------------------------------------------------------
        verdicts = [evaluate_containment(t, agents, self.ring_band) for t in targets]
        for v in verdicts:
            if v.contained:
                self.per_target_containment[v.target_id] = \
                    self.per_target_containment.get(v.target_id, 0.0) + dt
        if verdicts and all(v.contained for v in verdicts):
            self.containment_seconds += dt

        # Measured PER LIVE HOSTILE rather than as a simultaneous conjunction. Requiring every
        # hostile to be encircled at once needs 3+ trackers each, which a 12-drone fleet cannot
        # supply against 3 hostiles, so the conjunction would read 0% for any architecture and
        # discriminate nothing. Per-hostile hostile-seconds is achievable and comparable.
        for v, t in zip(verdicts, targets):
            if t.neutralized:
                continue
            self.live_hostile_seconds += dt
            if v.contained:
                self.active_containment_seconds += dt

        # --- TRACK_ERROR -------------------------------------------------------------
        if beliefs:
            truth = {t.target_id: (t.x, t.y) for t in targets}
            for (aid, tid), (bx, by) in beliefs.items():
                if tid not in truth:
                    continue
                tx, ty = truth[tid]
                err = math.hypot(bx - tx, by - ty)
                self._belief_sq_sum += err * err
                self._belief_samples += 1
                self.worst_track_error = max(self.worst_track_error, err)

        # --- ASSIGNMENT_THRASH -------------------------------------------------------
        for a in agents:
            prev_r = self._prev_role.get(a.agent_id)
            prev_t = self._prev_target.get(a.agent_id)
            if prev_r is not None and prev_r != a.role:
                self.role_changes += 1
            if prev_t is not None and prev_t != a.target_id:
                self.target_changes += 1
            self._prev_role[a.agent_id] = a.role
            self._prev_target[a.agent_id] = a.target_id

        # --- ATTRITION / STALL / PATH_WASTE -----------------------------------------
        self._availability_integral += (len(live_agents) / max(1, self.num_agents)) * dt
        self.worst_fit_count = min(self.worst_fit_count, len(live_agents))
        self.final_fit_count = len(live_agents)

        target_by_id = {t.target_id: t for t in targets}
        for a in agents:
            if a.agent_id not in self._first_pos:
                self._first_pos[a.agent_id] = (a.x, a.y)
                self._last_pos[a.agent_id] = (a.x, a.y)
                self._path_length[a.agent_id] = 0.0
            lx, ly = self._last_pos[a.agent_id]
            self._path_length[a.agent_id] += math.hypot(a.x - lx, a.y - ly)
            self._last_pos[a.agent_id] = (a.x, a.y)

            if a.is_fit and a.speed < STALL_SPEED:
                tgt = target_by_id.get(a.target_id)
                if tgt is not None and not tgt.neutralized:
                    if math.hypot(a.x - tgt.x, a.y - tgt.y) > STALL_RANGE:
                        self.stall_seconds += dt

        # --- MISSION OUTCOME ---------------------------------------------------------
        arrested = sum(1 for t in targets if t.neutralized)
        self.peak_clean_arrests = max(self.peak_clean_arrests, self.clean_arrests(targets))
        if arrested > self.peak_arrested:
            self.peak_arrested = arrested
            if self.time_to_first_arrest is None and arrested >= 1:
                self.time_to_first_arrest = self.mission_time
        if (self.time_to_all_arrest is None and targets
                and arrested == len(targets)):
            self.time_to_all_arrest = self.mission_time

        return verdicts

    # ------------------------------------------------------------------
    @property
    def track_rmse(self) -> float:
        if self._belief_samples == 0:
            return float('nan')
        return math.sqrt(self._belief_sq_sum / self._belief_samples)

    @property
    def containment_fraction(self) -> float:
        return self.containment_seconds / self.mission_time if self.mission_time > 0 else 0.0

    @property
    def active_containment_fraction(self) -> float:
        """
        Fraction of live hostile-seconds during which that hostile was encircled. This is the
        encirclement metric: it cannot be inflated by disabling a hostile early and coasting on
        the arrest credit, and it is measured per hostile so it stays achievable for a fleet
        that cannot ring every hostile simultaneously.
        """
        if self.live_hostile_seconds <= 0.0:
            return 0.0
        return self.active_containment_seconds / self.live_hostile_seconds

    @property
    def availability(self) -> float:
        return self._availability_integral / self.mission_time if self.mission_time > 0 else 0.0

    @property
    def path_efficiency(self) -> float:
        """
        Mean net-displacement-to-path-length ratio. 1.0 is a straight run to the objective;
        low values mean the swarm burned distance thrashing without getting anywhere.
        """
        ratios = []
        for aid, path in self._path_length.items():
            if path < 1.0:
                continue
            fx, fy = self._first_pos[aid]
            lx, ly = self._last_pos[aid]
            ratios.append(math.hypot(lx - fx, ly - fy) / path)
        return sum(ratios) / len(ratios) if ratios else 0.0

    @property
    def reassignments_per_minute(self) -> float:
        if self.mission_time <= 0:
            return 0.0
        return (self.role_changes + self.target_changes) * 60.0 / self.mission_time

    @property
    def zero_collision_invariant(self) -> bool:
        return len(self.collision_pairs) == 0

    @property
    def hostile_impacts(self) -> int:
        return len(self.hostile_impact_pairs)

    @property
    def mission_clean(self) -> bool:
        """
        The composite success condition a capstone defence actually rests on: every hostile
        cleanly arrested, the collision invariant never breached, and no role left unmanned.
        Disabling the hostiles while destroying your own fleet is not a successful mission.
        """
        # The allocator runs at 10 Hz behind a 1 Hz establishment watchdog, so a sub-second
        # gap between a role holder being destroyed and its backfill is physically
        # unavoidable. The claim being tested is that no role suffers SUSTAINED downtime.
        return (
            self.peak_clean_arrests >= self.num_targets
            and self.zero_collision_invariant
            and self.max_any_vacancy_run <= MAX_ACCEPTABLE_VACANCY_RUN
            and self.hostile_impacts == 0
        )

    def clean_arrests(self, targets: Sequence[TargetSample]) -> int:
        """
        Arrests achieved without ever striking the hostile: the mission's actual success
        criterion. A hostile that was rammed is disabled, but it was not cleanly arrested.
        """
        return sum(
            1 for t in targets
            if t.neutralized and self.per_target_impacts.get(t.target_id, 0) == 0
        )

    def as_dict(self) -> Dict[str, object]:
        """Machine-readable summary, identical keys for both architectures."""
        return {
            "arch": self.architecture,
            "t": round(self.mission_time, 1),
            "targets_total": self.num_targets,
            "targets_arrested": self.peak_arrested,
            "clean_arrests": self.peak_clean_arrests,
            "hostile_impacts": self.hostile_impacts,
            "min_target_sep": (round(self.min_target_separation, 2)
                               if self.min_target_separation != float('inf') else -1.0),
            "t_first_arrest": (round(self.time_to_first_arrest, 1)
                               if self.time_to_first_arrest is not None else -1.0),
            "t_all_arrest": (round(self.time_to_all_arrest, 1)
                             if self.time_to_all_arrest is not None else -1.0),
            "collisions": len(self.collision_pairs),
            "collision_events": self.collision_events,
            "min_sep": round(self.min_separation, 2) if self.min_separation != float('inf') else -1.0,
            "sec_below_safe": round(self.seconds_below_safe, 1),
            "zero_collision": int(self.zero_collision_invariant),
            "role_vacancy_s": round(self.any_vacancy_seconds, 1),
            "max_vacancy_run_s": round(self.max_any_vacancy_run, 2),
            "vacancy_int_s": round(self.role_vacancy_seconds["interceptor"], 1),
            "vacancy_trk_s": round(self.role_vacancy_seconds["tracker"], 1),
            "vacancy_rly_s": round(self.role_vacancy_seconds["relay"], 1),
            "vacancy_sct_s": round(self.role_vacancy_seconds["scout"], 1),
            "containment_frac": round(self.containment_fraction, 3),
            "active_containment_frac": round(self.active_containment_fraction, 3),
            "final_fit_count": self.final_fit_count,
            "mission_clean": int(self.mission_clean),
            "track_rmse": (round(self.track_rmse, 2)
                           if self._belief_samples else -1.0),
            "worst_track_err": round(self.worst_track_error, 2),
            "reassign_per_min": round(self.reassignments_per_minute, 1),
            "availability": round(self.availability, 3),
            "worst_fit_count": self.worst_fit_count,
            "stall_s": round(self.stall_seconds, 1),
            "path_efficiency": round(self.path_efficiency, 3),
        }

    def scorecard_line(self) -> str:
        """Single parseable line consumed by run_comparison.sh."""
        d = self.as_dict()
        return "[SCORECARD] " + " ".join(f"{k}={v}" for k, v in d.items())

    def render(self, verdicts: Optional[Sequence[ContainmentVerdict]] = None) -> str:
        """Human-readable scorecard block for the live console."""
        d = self.as_dict()
        width = 118
        lines = ["", "=" * width]
        lines.append(f"[COMPARATIVE SCORECARD]  architecture = {self.architecture}   "
                     f"t = {self.mission_time:6.1f}s   fleet = {self.num_agents}   "
                     f"hostiles = {self.num_targets}")
        lines.append("-" * width)
        lines.append(f" MISSION      | hostiles disabled {d['targets_arrested']}/{d['targets_total']}"
                     f"   CLEAN non-destructive arrests {d['clean_arrests']}/{d['targets_total']}"
                     f"   first {d['t_first_arrest']}s   all {d['t_all_arrest']}s")
        lines.append(f" HULL STRIKES | drone-on-hostile impacts {d['hostile_impacts']}"
                     f"   closest approach to a hostile {d['min_target_sep']}m"
                     f"   (a ramming capture fails the non-destructive objective)")
        lines.append(f" COLLISION    | distinct colliding pairs {d['collisions']}"
                     f"   entries into breach {d['collision_events']}"
                     f"   min separation {d['min_sep']}m"
                     f"   time below {self.d_safe:.1f}m {d['sec_below_safe']}s"
                     f"   invariant held {'YES' if d['zero_collision'] else 'NO'}")
        lines.append(f" ROLE MANNING | longest sustained vacancy {d['max_vacancy_run_s']}s"
                     f"   total unmanned {d['role_vacancy_s']}s"
                     f"   (int {d['vacancy_int_s']}s, trk {d['vacancy_trk_s']}s,"
                     f" rly {d['vacancy_rly_s']}s, sct {d['vacancy_sct_s']}s)")
        lines.append(f" CONTAINMENT  | live hostiles encircled {d['active_containment_frac']*100:5.1f}%"
                     f" of engaged time ({d['containment_frac']*100:5.1f}% of mission incl. arrests)"
                     + ("   per-hostile: " + ", ".join(
                         f"H{m} {s / max(self.mission_time, 1e-6) * 100:4.1f}%"
                         for m, s in sorted(self.per_target_containment.items())) if self.per_target_containment else ""))
        lines.append(f" TRACKING     | belief RMSE {d['track_rmse']}m   worst error {d['worst_track_err']}m")
        lines.append(f" STABILITY    | reassignments {d['reassign_per_min']}/min"
                     f"   stalled {d['stall_s']}s   path efficiency {d['path_efficiency']}")
        lines.append(f" ATTRITION    | mean availability {d['availability']*100:5.1f}%"
                     f"   worst {d['worst_fit_count']}/{self.num_agents}"
                     f"   final {d['final_fit_count']}/{self.num_agents}")
        lines.append(f" VERDICT      | clean mission (all cleanly arrested, zero collisions, "
                     f"no vacancy): {'PASS' if d['mission_clean'] else 'FAIL'}")
        if verdicts:
            lines.append("-" * width)
            for v in verdicts:
                lines.append(
                    f"  H{v.target_id}: participants {v.participants:2d} | "
                    f"spread {v.angular_spread:5.2f}>={v.spread_threshold:4.2f} "
                    f"{'OK' if v.criterion_1 else 'NO'} | "
                    f"gap {v.max_gap:5.2f}<={v.gap_threshold:4.2f} "
                    f"{'OK' if v.criterion_2 else 'NO'} | "
                    f"{'CONTAINED' if v.contained else 'NOT CONTAINED'}"
                )
        lines.append("=" * width)
        return "\n".join(lines)
