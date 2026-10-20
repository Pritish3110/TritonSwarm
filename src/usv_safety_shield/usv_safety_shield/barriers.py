"""
USV Swarm Simulation - Multi-Target Control Barrier Function Library
Author: Principal Enterprise Engineer
Stage: 2 (Hard Safety Enforcement, Multi-Target)

Pure (rclpy-free) barrier mathematics backing usv_safety_shield.

Formulation
-----------
Each constraint is a control barrier function h(p) = ||p_i - p_obs|| - d_barrier >= 0,
enforced through the standard class-K condition

    h_dot + lambda * h >= 0,

i.e. the permitted CLOSING RATE is bounded by lambda * h and shrinks to zero at the barrier.
The commanded surge is then limited by the component of own velocity along the line of sight:

    u * cos(bearing_to_obstacle) <= lambda * h - (closing contributed by the obstacle)

This is velocity-aware rather than position-only, which matters twice over:
  * it intervenes early enough on a fast closure that the barrier is never crossed, where a
    position-only throttle lets actuator lag carry the vessel through it;
  * it does NOT brake a vessel that is merely near an obstacle while opening or passing
    tangentially, so the swarm can thread past drifting wrecks instead of jamming behind them.

Barrier tiers:
- Inter-ally pairwise barrier, d_safe = 4.0 m. This is the hard collision invariant and is
  never relaxed. Both vessels brake, so each is allowed only half the closing budget.
- Target exclusion envelope: non-interceptors hold 14.0 m off EVERY hostile.
- Interceptor capture corridor: an interceptor may close to 4.2 m on its ASSIGNED hostile
  only, with a zero-thrust brake at 4.0 m. Unassigned hostiles stay 14.0 m obstacles for it.

Deadlock avoidance: a vessel already inside an envelope is given a tangential escape course at
reduced-but-nonzero speed rather than zero thrust. Zeroing thrust inside a moving hostile's
envelope would leave the vessel to be run down, and with overlapping envelopes it would strand
the vessel permanently between two hostiles.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
import numpy as np

# Actuator envelope (mirrors usv_sim_core clamps).
MAX_SURGE = 3.5
MAX_YAW_RATE = 0.8

# Inter-ally hard collision invariant.
D_SAFE_ALLY = 4.0
D_WARN_ALLY = 8.0

# Target exclusion envelope for non-interceptors.
D_EXCLUSION = 14.0
D_EXCLUSION_WARN = 18.0

# Interceptor terminal capture corridor on its assigned hostile.
D_CAPTURE = 4.2
D_CAPTURE_WARN = 6.5
D_CAPTURE_BRAKE = 4.0

# CBF class-K gain, and the stand-off margin that absorbs first-order actuator lag
# (surge responds with tau ~ 0.5 s, so the barrier is placed slightly outside d_safe).
CBF_LAMBDA = 0.9
CBF_LAG_MARGIN = 0.5

# Speed retained for a tangential slide out of an envelope.
V_TANGENTIAL_ESCAPE = 1.1


@dataclass
class TargetObstacle:
    """One hostile hull as seen by the safety shield, with its world-frame velocity."""
    target_id: int
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    neutralized: bool = False


@dataclass
class BarrierResult:
    """Filtered command plus the telemetry needed to audit the safety invariants."""
    u_safe: float
    r_safe: float
    min_target_dist: float = float('inf')
    min_ally_dist: float = float('inf')
    interventions: Tuple[str, ...] = ()
    envelope_breach: bool = False
    ally_breach: bool = False


def wrap_angle(a: float) -> float:
    """Wraps an angle into [-pi, pi)."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def is_interceptor(role: str) -> bool:
    """Role test tolerant of the canonical role strings published by the planner."""
    return "interceptor" in (role or "").lower()


def cbf_surge_limit(
    dist: float,
    d_barrier: float,
    rel_bearing: float,
    obstacle_closing: float = 0.0,
    budget_share: float = 1.0,
    lam: float = CBF_LAMBDA,
) -> Optional[float]:
    """
    Maximum own surge permitted by h_dot + lambda*h >= 0.

    `obstacle_closing` is the closing rate the obstacle contributes on its own (positive when
    it is running the vessel down, negative when it is fleeing). `budget_share` splits the
    closing allowance when the obstacle is a cooperating ally that brakes symmetrically.

    Returns None when the geometry imposes no surge constraint (opening or tangential).
    """
    los = math.cos(rel_bearing)
    if los <= 1e-3:
        return None

    allowed_closing = lam * (dist - d_barrier) * budget_share
    budget = allowed_closing - obstacle_closing
    return max(0.0, budget / los)


def closing_rate_from_velocity(
    own_pos: Tuple[float, float],
    obs_pos: Tuple[float, float],
    obs_vel: Tuple[float, float],
) -> float:
    """
    Closing rate contributed by the obstacle's own motion: positive while it is reducing the
    range on its own account, negative while it is opening.
    """
    rx = obs_pos[0] - own_pos[0]
    ry = obs_pos[1] - own_pos[1]
    dist = math.hypot(rx, ry)
    if dist < 1e-6:
        return 0.0
    return -(rx * obs_vel[0] + ry * obs_vel[1]) / dist


def composite_escape_heading(
    own_pos: Tuple[float, float],
    violators: Sequence[Tuple[float, float]],
) -> Optional[float]:
    """
    Resultant escape bearing away from every hull currently crowding this vessel.

    Each violator contributes a repulsion vector weighted by proximity, so a vessel caught
    between two hostiles is pushed out through the gap between them rather than stalling.
    """
    if not violators:
        return None
    ex = 0.0
    ey = 0.0
    for vx, vy in violators:
        dx = own_pos[0] - vx
        dy = own_pos[1] - vy
        d = math.hypot(dx, dy)
        if d < 1e-6:
            continue
        w = 1.0 / max(d, 1.0)
        ex += (dx / d) * w
        ey += (dy / d) * w
    if abs(ex) < 1e-9 and abs(ey) < 1e-9:
        return None
    return math.atan2(ey, ex)


def apply_target_barriers(
    own_pos: Tuple[float, float],
    own_psi: float,
    u_cmd: float,
    r_cmd: float,
    role: str,
    assigned_target_id: int,
    targets: Sequence[TargetObstacle],
    exclusion: float = D_EXCLUSION,
    exclusion_warn: float = D_EXCLUSION_WARN,
    capture: float = D_CAPTURE,
    capture_warn: float = D_CAPTURE_WARN,
    capture_brake: float = D_CAPTURE_BRAKE,
) -> BarrierResult:
    """
    Enforces the tiered target barriers across every hostile in the battlespace.

    An interceptor's capture corridor applies solely to its assigned hostile; every other
    hostile is treated exactly as a non-interceptor would treat it. A hostile does not
    cooperate, so the vessel carries the entire closing budget itself.
    """
    u_safe = float(np.clip(u_cmd, 0.0, MAX_SURGE))
    r_safe = float(np.clip(r_cmd, -MAX_YAW_RATE, MAX_YAW_RATE))
    interventions: List[str] = []
    min_dist = float('inf')
    breach = False
    violators: List[Tuple[float, float]] = []
    interceptor = is_interceptor(role)

    for tgt in targets:
        dx = tgt.x - own_pos[0]
        dy = tgt.y - own_pos[1]
        dist = math.hypot(dx, dy)
        min_dist = min(min_dist, dist)

        rel_bearing = wrap_angle(math.atan2(dy, dx) - own_psi)
        obs_closing = closing_rate_from_velocity(own_pos, (tgt.x, tgt.y), (tgt.vx, tgt.vy))

        if interceptor and tgt.target_id == assigned_target_id:
            # Terminal capture corridor on the assigned hostile.
            if dist < capture_warn:
                limit = cbf_surge_limit(dist, capture, rel_bearing, obs_closing)
                if limit is not None:
                    u_safe = min(u_safe, limit)
                    interventions.append(f"capture_corridor:t{tgt.target_id}")
            if dist <= capture_brake:
                # Hard brake and sheer off: an arrest is non-destructive.
                u_safe = 0.0
                r_safe = (-1.0 if rel_bearing >= 0.0 else 1.0) * MAX_YAW_RATE
                interventions.append(f"capture_brake:t{tgt.target_id}")
            continue

        # Every other hostile is a hard exclusion envelope.
        if dist <= exclusion:
            breach = True
            violators.append((tgt.x, tgt.y))
            interventions.append(f"envelope_breach:t{tgt.target_id}")
        elif dist < exclusion_warn:
            limit = cbf_surge_limit(dist, exclusion, rel_bearing, obs_closing)
            if limit is not None:
                u_safe = min(u_safe, limit)
                steer_away = -1.0 if rel_bearing >= 0.0 else 1.0
                r_safe = float(np.clip(0.3 * r_safe + 0.7 * steer_away * MAX_YAW_RATE,
                                       -MAX_YAW_RATE, MAX_YAW_RATE))
                interventions.append(f"envelope_warn:t{tgt.target_id}")

    if breach:
        # Tangential slide out of the envelope: steer onto the composite escape bearing and
        # keep way on, so the vessel clears the hull instead of being overrun or stranded.
        escape = composite_escape_heading(own_pos, violators)
        if escape is not None:
            err = wrap_angle(escape - own_psi)
            r_safe = float(np.clip(err * 2.0, -MAX_YAW_RATE, MAX_YAW_RATE))
            u_safe = min(u_safe, V_TANGENTIAL_ESCAPE * max(0.25, math.cos(err)))

    return BarrierResult(
        u_safe=u_safe,
        r_safe=r_safe,
        min_target_dist=min_dist,
        interventions=tuple(interventions),
        envelope_breach=breach,
    )


def apply_ally_barriers(
    own_pos: Tuple[float, float],
    own_psi: float,
    u_cmd: float,
    r_cmd: float,
    ally_positions: Dict[int, Tuple[float, float]],
    ally_velocities: Optional[Dict[int, Tuple[float, float]]] = None,
    d_safe: float = D_SAFE_ALLY,
    d_warning: float = D_WARN_ALLY,
) -> BarrierResult:
    """
    Pairwise inter-ally barrier. This is the hard collision invariant (d >= 4.0 m) and is
    enforced identically regardless of role, phase or mission state.

    Allies brake symmetrically, so each vessel claims only half the closing budget. A drifting
    wreck does not brake, so when its velocity is known and it is the one closing the range,
    the live vessel absorbs that term in full.
    """
    u_safe = float(np.clip(u_cmd, 0.0, MAX_SURGE))
    r_safe = float(np.clip(r_cmd, -MAX_YAW_RATE, MAX_YAW_RATE))
    velocities = ally_velocities or {}
    interventions: List[str] = []
    min_dist = float('inf')
    breach = False

    # The barrier stands slightly outside d_safe so first-order actuator lag cannot carry the
    # vessel through the invariant itself.
    d_barrier = d_safe + CBF_LAG_MARGIN

    for other_id, (ox, oy) in ally_positions.items():
        dx = ox - own_pos[0]
        dy = oy - own_pos[1]
        dist = math.hypot(dx, dy)
        min_dist = min(min_dist, dist)

        if dist >= d_warning:
            continue

        rel_bearing = wrap_angle(math.atan2(dy, dx) - own_psi)
        is_ahead = abs(rel_bearing) < (math.pi / 2.0)

        obs_closing = 0.0
        share = 0.5
        if other_id in velocities:
            obs_closing = closing_rate_from_velocity(own_pos, (ox, oy), velocities[other_id])
            if obs_closing <= 0.0:
                # The neighbour is opening the range; this vessel owns the whole budget.
                share = 1.0

        limit = cbf_surge_limit(dist, d_barrier, rel_bearing,
                                obstacle_closing=max(0.0, obs_closing), budget_share=share)
        if limit is not None:
            u_safe = min(u_safe, limit)
            interventions.append(f"ally_cbf:{other_id}")

        steer_dir = -1.0 if rel_bearing >= 0.0 else 1.0

        if is_ahead:
            # Graded lateral evasion while still outside the barrier.
            proximity = 1.0 - max(0.0, min(1.0, (dist - d_safe) / max(0.1, d_warning - d_safe)))
            evasion_r = steer_dir * MAX_YAW_RATE * proximity
            r_safe = float(np.clip((1.0 - proximity) * r_safe + proximity * evasion_r,
                                   -MAX_YAW_RATE, MAX_YAW_RATE))

        if dist <= d_barrier:
            # On or inside the barrier: the CBF has saturated, so sheer off at full authority.
            r_safe = steer_dir * MAX_YAW_RATE
            breach = True
            interventions.append(f"ally_barrier:{other_id}")

        if dist <= d_safe + 0.3:
            # Backstop: inside the invariant margin, all stop and sheer off hard.
            u_safe = 0.0
            r_safe = steer_dir * MAX_YAW_RATE
            interventions.append(f"ally_brake:{other_id}")

    return BarrierResult(
        u_safe=u_safe,
        r_safe=r_safe,
        min_ally_dist=min_dist,
        interventions=tuple(interventions),
        ally_breach=breach,
    )


def filter_command(
    own_pos: Tuple[float, float],
    own_psi: float,
    u_nominal: float,
    r_nominal: float,
    role: str,
    assigned_target_id: int,
    targets: Sequence[TargetObstacle],
    ally_positions: Dict[int, Tuple[float, float]],
    ally_velocities: Optional[Dict[int, Tuple[float, float]]] = None,
    d_safe: float = D_SAFE_ALLY,
    d_warning: float = D_WARN_ALLY,
) -> BarrierResult:
    """
    Full shield pass: target barriers first, then the inter-ally invariant.

    The ally barrier runs last and is therefore authoritative -- a target-avoidance manoeuvre
    can never be the reason two allies collide.
    """
    tgt_res = apply_target_barriers(
        own_pos, own_psi, u_nominal, r_nominal, role, assigned_target_id, targets
    )
    ally_res = apply_ally_barriers(
        own_pos, own_psi, tgt_res.u_safe, tgt_res.r_safe, ally_positions,
        ally_velocities=ally_velocities, d_safe=d_safe, d_warning=d_warning,
    )

    return BarrierResult(
        u_safe=ally_res.u_safe,
        r_safe=ally_res.r_safe,
        min_target_dist=tgt_res.min_target_dist,
        min_ally_dist=ally_res.min_ally_dist,
        interventions=tgt_res.interventions + ally_res.interventions,
        envelope_breach=tgt_res.envelope_breach,
        ally_breach=ally_res.ally_breach,
    )
