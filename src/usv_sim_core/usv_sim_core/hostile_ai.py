"""
USV Swarm Simulation - Coordinated Hostile Drone AI
Author: Principal Enterprise Engineer
Stage: 2 (Multi-Target Battle Doctrine)

Pure (rclpy-free) behaviour model for the hostile drones. Hostiles do not merely run from the
nearest pursuer -- they fight the encirclement as a group:

- Open-sector escape: the hostile reads the bearings of every pursuer, finds the widest
  unguarded sector of its own perimeter, and drives through it.
- Split evasion: live hostiles bias their escape vectors apart, forcing the swarm to divide
  its force instead of massing on one hull.
- Escort formation: while unthreatened, escorts hold station on the flagship's quarters.
- Mutual support: an unengaged hostile makes a relief run toward a partner under EMP lock,
  trying to pull interceptors off the lock.
- Breakout burst: if a wide sector opens while pursuers are still distant, the hostile
  sprints rather than merely evading.
- Propulsion damage: EMP defusal progressively starves propulsion until full arrest.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple
import numpy as np

# Detection and engagement geometry (metres).
R_THREAT_DETECT = 35.0      # pursuers inside this range are counted as threats
R_FLANKED = 16.0            # pursuers this close choke the hostile's evasion
R_BURST_CLEAR = 22.0        # minimum pursuer range for a breakout sprint
GAP_BURST_RAD = 2.6         # open sector width that justifies a breakout sprint
# Escort station radius off the flagship. This must sit clearly OUTSIDE the wing split radius
# (40 m), or the hostile formation's own station-keeping drifts in and out of the merge band and
# the ally wings merge and split repeatedly, reshuffling the whole fleet each time.
R_ESCORT_STATION = 52.0
R_RELIEF_TRIGGER = 25.0     # defusal percentage at which partners attempt relief

# Speed envelope (m/s).
SPEED_PATROL = 1.20
SPEED_EVADE = 1.65
SPEED_CONTESTED = 1.15
SPEED_BURST = 1.95
SPEED_DEFUSING_FLOOR = 0.30
SPEED_ARREST_DECAY = 1.20   # m/s^2 bleed-off once arrested

MAX_YAW_RATE = 0.60
WEAVE_AMPLITUDE = 0.20
SPLIT_BIAS_GAIN = 0.55
RELIEF_BLEND = 0.45


class HostileTactic(Enum):
    """Group behaviour doctrine for the hostile formation."""
    ESCORT = "escort"                   # hold formation, evade individually
    SPLIT = "split_evasion"             # diverge to force the swarm to divide
    MUTUAL_SUPPORT = "mutual_support"   # relief runs toward a pinned partner
    COORDINATED = "coordinated"         # all of the above, selected by situation


@dataclass
class HostileState:
    """Full dynamic and combat state of one hostile drone."""
    target_id: int
    x: float = 0.0
    y: float = 0.0
    psi: float = 0.0
    speed: float = SPEED_PATROL
    yaw_rate: float = 0.0
    health: float = 1.0
    defusal_progress: float = 0.0
    neutralized: bool = False
    combat_state: str = "COMBAT_PATROL"
    tactic_note: str = ""
    role_label: str = "FLAGSHIP"
    history: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def is_flagship(self) -> bool:
        return self.target_id == 0


def wrap_angle(a: float) -> float:
    """Wraps an angle into [-pi, pi)."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def open_sector_escape(
    hx: float,
    hy: float,
    fallback_heading: float,
    threats: Sequence[Tuple[float, float]],
) -> Tuple[float, float, float]:
    """
    Finds the widest unguarded sector of the hostile's own perimeter.

    Returns (escape_bearing, sector_width_rad, nearest_threat_range). With no threats the
    fallback heading is returned and the sector is reported as fully open.
    """
    if not threats:
        return fallback_heading, 2.0 * math.pi, float('inf')

    bearings = sorted(math.atan2(ty - hy, tx - hx) for tx, ty in threats)
    nearest = min(math.hypot(tx - hx, ty - hy) for tx, ty in threats)

    if len(bearings) == 1:
        # A single pursuer: run directly away from it.
        return wrap_angle(bearings[0] + math.pi), 2.0 * math.pi, nearest

    best_gap = 0.0
    escape = fallback_heading
    count = len(bearings)
    for k in range(count):
        gap = (bearings[(k + 1) % count] - bearings[k]) % (2.0 * math.pi)
        if gap > best_gap:
            best_gap = gap
            escape = wrap_angle(bearings[k] + gap / 2.0)
    return escape, best_gap, nearest


def split_bias(
    own: HostileState,
    partners: Sequence[HostileState],
    gain: float = SPLIT_BIAS_GAIN,
) -> float:
    """
    Angular bias that pushes this hostile's escape vector away from its live partners, so the
    formation fans out under pressure and the swarm has to split its force to follow.
    """
    live = [p for p in partners if p.target_id != own.target_id and not p.neutralized]
    if not live:
        return 0.0

    ax = 0.0
    ay = 0.0
    for p in live:
        dx = own.x - p.x
        dy = own.y - p.y
        d = math.hypot(dx, dy)
        if d < 1e-6:
            continue
        w = 1.0 / max(d, 5.0)
        ax += (dx / d) * w
        ay += (dy / d) * w

    if abs(ax) < 1e-9 and abs(ay) < 1e-9:
        return 0.0
    return gain * wrap_angle(math.atan2(ay, ax) - own.psi)


def relief_target(
    own: HostileState,
    partners: Sequence[HostileState],
    trigger_pct: float = R_RELIEF_TRIGGER,
) -> Optional[HostileState]:
    """
    Picks the partner most worth relieving: the closest hull under meaningful EMP lock that
    has not yet been arrested. Returns None when this hostile is itself under lock.
    """
    if own.defusal_progress > 0.0 or own.neutralized:
        return None
    candidates = [
        p for p in partners
        if p.target_id != own.target_id
        and not p.neutralized
        and p.defusal_progress >= trigger_pct
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda p: math.hypot(p.x - own.x, p.y - own.y))


def escort_station(flagship: HostileState, escort_index: int) -> Tuple[float, float]:
    """Station for an escort on the flagship's quarters, alternating port and starboard."""
    side = 1.0 if escort_index % 2 == 1 else -1.0
    rank = (escort_index + 1) // 2
    bearing = wrap_angle(flagship.psi + math.pi + side * 0.65)
    radius = R_ESCORT_STATION * max(1, rank)
    return (flagship.x + radius * math.cos(bearing),
            flagship.y + radius * math.sin(bearing))


def step_hostile(
    host: HostileState,
    dt: float,
    sim_time: float,
    threats: Sequence[Tuple[float, float]],
    partners: Sequence[HostileState],
    tactic: HostileTactic = HostileTactic.COORDINATED,
    mode: str = "reactive",
    drift: Tuple[float, float] = (0.0, 0.0),
    history_limit: int = 60,
) -> None:
    """
    Advances one hostile by dt, in place.

    Mission state takes precedence over evasion: an arrested hull decelerates to a standstill,
    and a hull under EMP lock loses propulsion authority progressively, so the swarm's work is
    always visible in the hostile's own telemetry.
    """
    # --- 1. Non-reactive demonstration modes (preserved from the prototype) -------------
    if mode == "nominal":
        host.speed = SPEED_PATROL
        host.yaw_rate = 0.0
        host.combat_state = "TRANSIT"
        host.tactic_note = ""
        _integrate(host, dt, drift, history_limit)
        return

    if mode == "sinusoidal":
        host.speed = 1.5
        omega = 2.0 * math.pi / 15.0
        host.yaw_rate = float(np.clip(
            4.0 * omega * math.cos(omega * sim_time) * 0.1, -0.4, 0.4))
        host.combat_state = "EVASIVE_WEAVE"
        host.tactic_note = ""
        _integrate(host, dt, drift, history_limit)
        return

    # --- 2. Arrested: propulsion is gone, the hull coasts to a standstill ---------------
    if host.neutralized or host.defusal_progress >= 100.0:
        host.neutralized = True
        host.speed = max(0.0, host.speed - SPEED_ARREST_DECAY * dt)
        host.yaw_rate = 0.0
        host.health = 0.15
        host.combat_state = "DEFUSED_AND_ARRESTED"
        host.tactic_note = "UNDER GUARD"
        _integrate(host, dt, drift, history_limit)
        return

    # --- 3. Under active EMP lock: propulsion stutters and decays -----------------------
    if host.defusal_progress > 0.0:
        frac = min(1.0, host.defusal_progress / 100.0)
        host.health = max(0.20, 1.0 - 0.65 * frac)
        host.combat_state = f"UNDER_DEFUSAL ({int(host.defusal_progress)}%)"

        # A hull under lock still thrashes: stuttering yaw as the drive cuts in and out.
        escape, _, nearest = open_sector_escape(host.x, host.y, host.psi, threats)

        # Damage and geometry compound: a hull that is both boxed in and losing propulsion is
        # slower than one suffering either alone.
        lock_ceiling = SPEED_EVADE * (1.0 - frac)
        situational = SPEED_CONTESTED if nearest < R_FLANKED else SPEED_EVADE
        ceiling = max(SPEED_DEFUSING_FLOOR, min(lock_ceiling, situational))
        if host.speed < ceiling:
            host.speed = min(ceiling, host.speed + 0.4 * dt)
        else:
            host.speed = max(ceiling, host.speed - 0.6 * dt)

        yaw_err = wrap_angle(escape - host.psi)
        stutter = 0.25 * math.sin(5.0 * sim_time + host.target_id)
        host.yaw_rate = float(np.clip(yaw_err * 1.2 + stutter, -MAX_YAW_RATE, MAX_YAW_RATE))
        host.tactic_note = "PROPULSION FAILING"
        _integrate(host, dt, drift, history_limit)
        return

    # --- 4. Reactive combat: fight the encirclement -------------------------------------
    close = [(tx, ty) for tx, ty in threats
             if math.hypot(tx - host.x, ty - host.y) <= R_THREAT_DETECT]

    if not close:
        _patrol(host, dt, sim_time, partners, tactic, drift, history_limit)
        return

    escape, gap, nearest = open_sector_escape(host.x, host.y, host.psi, close)
    desired = escape
    note_parts: List[str] = []

    # Split evasion: bias the escape vector away from partners to divide the swarm.
    if tactic in (HostileTactic.SPLIT, HostileTactic.COORDINATED):
        bias = split_bias(host, partners)
        if abs(bias) > 1e-6:
            desired = wrap_angle(desired + float(np.clip(bias, -0.9, 0.9)))
            note_parts.append("SPLIT EVASION")

    # Mutual support: an unengaged hull runs to relieve a partner under lock.
    if tactic in (HostileTactic.MUTUAL_SUPPORT, HostileTactic.COORDINATED):
        ally = relief_target(host, partners)
        if ally is not None:
            relief_bearing = math.atan2(ally.y - host.y, ally.x - host.x)
            desired = wrap_angle(desired + RELIEF_BLEND * wrap_angle(relief_bearing - desired))
            note_parts.append(f"RELIEF RUN -> H{ally.target_id}")

    yaw_err = wrap_angle(desired - host.psi)
    weave = WEAVE_AMPLITUDE * math.sin(2.0 * sim_time + host.target_id * 1.7)
    host.yaw_rate = float(np.clip(yaw_err * 2.0 + weave, -MAX_YAW_RATE, MAX_YAW_RATE))

    # Speed governance: sprint through a wide opening, choke down when flanked.
    if gap >= GAP_BURST_RAD and nearest >= R_BURST_CLEAR:
        ceiling = SPEED_BURST
        host.combat_state = "BREAKOUT_BURST"
    elif nearest < R_FLANKED:
        ceiling = SPEED_CONTESTED
        host.combat_state = "CONTESTED_FLANKED"
    else:
        ceiling = SPEED_EVADE
        host.combat_state = "HOSTILE_EVASION"

    if host.speed < ceiling:
        host.speed = min(ceiling, host.speed + 0.35 * dt)
    else:
        host.speed = max(ceiling, host.speed - 0.5 * dt)

    host.tactic_note = " | ".join(note_parts)
    _integrate(host, dt, drift, history_limit)


def _patrol(
    host: HostileState,
    dt: float,
    sim_time: float,
    partners: Sequence[HostileState],
    tactic: HostileTactic,
    drift: Tuple[float, float],
    history_limit: int,
) -> None:
    """Unthreatened behaviour: the flagship cruises, escorts hold station on its quarters."""
    flagship = next((p for p in partners if p.is_flagship and not p.neutralized), None)
    holds_station = (
        tactic in (HostileTactic.ESCORT, HostileTactic.COORDINATED)
        and not host.is_flagship
        and flagship is not None
    )

    if holds_station:
        sx, sy = escort_station(flagship, host.target_id)
        d = math.hypot(sx - host.x, sy - host.y)
        yaw_err = wrap_angle(math.atan2(sy - host.y, sx - host.x) - host.psi)
        host.yaw_rate = float(np.clip(yaw_err * 1.5, -MAX_YAW_RATE, MAX_YAW_RATE))
        # Close the gap on station, then settle into the flagship's speed.
        target_speed = float(np.clip(flagship.speed + 0.35 * d, 0.4, SPEED_EVADE))
        host.speed += float(np.clip(target_speed - host.speed, -0.5 * dt, 0.5 * dt))
        host.combat_state = "ESCORT_FORMATION"
        host.tactic_note = f"STATION {d:.0f}m"
    else:
        host.speed = min(SPEED_PATROL, host.speed + 0.2 * dt)
        host.yaw_rate = 0.1 * math.sin(0.5 * sim_time + host.target_id)
        host.combat_state = "COMBAT_PATROL"
        host.tactic_note = ""

    host.health = min(1.0, host.health + 0.02 * dt)
    _integrate(host, dt, drift, history_limit)


def _integrate(
    host: HostileState,
    dt: float,
    drift: Tuple[float, float],
    history_limit: int,
) -> None:
    """Advances the hostile's pose and trims its wake history."""
    host.psi = wrap_angle(host.psi + host.yaw_rate * dt)
    host.x += (host.speed * math.cos(host.psi) + drift[0]) * dt
    host.y += (host.speed * math.sin(host.psi) + drift[1]) * dt
    host.history.append((float(host.x), float(host.y)))
    if len(host.history) > history_limit:
        del host.history[0:len(host.history) - history_limit]


def spawn_hostile_formation(
    num_targets: int,
    origin: Tuple[float, float] = (0.0, 0.0),
    heading: float = 0.0,
    along_spacing: float = 20.0,
    across_spacing: float = 46.0,
) -> Dict[int, HostileState]:
    """
    Spawns the hostile formation as a flagship with escorts echeloned on its quarters, rather
    than stacking every hull on a single point.

    The default spacing places every pair well outside the ally wing merge/split band, so the
    engagement starts as one wing per hostile. Spawning inside that band makes the wings merge
    and split as the formation settles, and every transition reshuffles the entire fleet.
    """
    hostiles: Dict[int, HostileState] = {}
    labels = ("FLAGSHIP", "ESCORT", "PICKET", "RAIDER", "TENDER")

    for m in range(max(1, num_targets)):
        if m == 0:
            hx, hy = origin
        else:
            side = 1.0 if m % 2 == 1 else -1.0
            rank = (m + 1) // 2
            along = -along_spacing * rank
            across = side * across_spacing * rank
            hx = origin[0] + along * math.cos(heading) - across * math.sin(heading)
            hy = origin[1] + along * math.sin(heading) + across * math.cos(heading)

        hostiles[m] = HostileState(
            target_id=m,
            x=float(hx),
            y=float(hy),
            psi=float(heading),
            speed=SPEED_PATROL,
            role_label=labels[m % len(labels)],
        )
    return hostiles
