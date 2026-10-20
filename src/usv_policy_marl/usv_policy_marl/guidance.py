"""
USV Swarm Simulation - Tactical Guidance Law Library
Author: Principal Enterprise Engineer
Stage: 2 (Multi-Target Battle Doctrine)

Pure (rclpy-free) guidance mathematics backing usv_policy_marl. Replaces naive
chase-the-waypoint steering with the laws real intercept platforms actually fly:

- Proportional Navigation (PN): interceptors fly a constant-bearing-decreasing-range
  collision course onto a lead point, producing curved lead pursuit instead of a tail chase.
- Lyapunov orbit vector field: trackers spiral onto a standoff radius and then circulate,
  instead of diving at the target and braking.
- Formation phase-locking: ring stations are held by modulating speed along the orbit, so
  the containment ring co-rotates coherently with the hostile's heading.
- Engagement phase machine: LAUNCH -> TRANSIT -> DEPLOY -> SHADOW -> PINCER -> TERMINAL ->
  EMP_LOCK -> ARREST_GUARD, with RECOVERY / COMBAT_LOSS branches for damaged hulls.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Sequence, Tuple
import numpy as np

# Actuator envelope (mirrors usv_sim_core clamps).
MAX_SURGE = 3.5
MAX_YAW_RATE = 0.8

# Phase transition geometry (metres).
D_TRANSIT = 35.0          # beyond this from station, the vessel is still in transit
D_ON_STATION = 6.0        # within this of station, the vessel is on station
D_TERMINAL = 12.0         # interceptor terminal guidance engagement range
D_EMP_LOCK = 7.5          # EMP defusal lock range
D_LAUNCH_WINDOW = 2.0     # seconds of column discipline after launch

# Health thresholds (mirror establishment.ROLE_FITNESS_FLOOR / CASUALTY_FLOOR).
H_COMBAT_LOSS = 0.25
H_DEGRADED = 0.55

# Guidance gains.
PN_GAIN = 3.0             # proportional navigation constant (classic 3-5)
ORBIT_CONVERGE_GAIN = 2.0 # radial convergence aggressiveness of the orbit course field
ORBIT_RADIAL_GAIN = 0.45  # m/s of closure per metre of standoff-radius error
ORBIT_ARC_GAIN = 0.30     # m/s of along-ring correction per metre of arc-station error
ORBIT_STATION_DEADBAND = 0.08  # rad of phase error inside which the vessel is on station
PHASE_LOCK_GAIN = 0.55    # tangential speed authority used to hold ring phase
HEADING_GAIN = 2.2        # heading-error to yaw-rate proportional gain
TERMINAL_HEADING_GAIN = 2.4


class GuidancePhase(Enum):
    """Engagement phase of a single vessel; surfaced on the dashboard and RViz billboard."""
    LAUNCH = "LAUNCH"
    TRANSIT = "TRANSIT"
    DEPLOY = "DEPLOY"
    SHADOW = "SHADOW"
    PINCER = "PINCER"
    TERMINAL = "TERMINAL"
    EMP_LOCK = "EMP_LOCK"
    ARREST_GUARD = "ARREST_GUARD"
    RECOVERY = "RECOVERY"
    COMBAT_LOSS = "COMBAT_LOSS"


@dataclass
class PNSolution:
    """Proportional navigation solution for one interceptor-target pair."""
    yaw_rate_cmd: float
    desired_heading: float
    lead_point: Tuple[float, float]
    closing_speed: float
    time_to_go: float
    los_rate: float
    range_to_target: float


def wrap_angle(a: float) -> float:
    """Wraps an angle into [-pi, pi)."""
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def facing_factor(heading_err: float, floor: float = 0.2) -> float:
    """
    Throttle authority as a function of heading error: a vessel pointing away from its
    objective should turn before it accelerates, not carve a wide arc at full speed.
    """
    return max(floor, math.cos(heading_err))


def heading_rate_cmd(heading_err: float, gain: float = HEADING_GAIN,
                     max_rate: float = MAX_YAW_RATE) -> float:
    """Proportional heading-hold yaw rate, clamped to the actuator envelope."""
    return float(np.clip(heading_err * gain, -max_rate, max_rate))


# ---------------------------------------------------------------------------
# Proportional navigation (interceptor terminal guidance)
# ---------------------------------------------------------------------------
def proportional_navigation(
    own_pos: Tuple[float, float],
    own_psi: float,
    own_speed: float,
    target_pos: Tuple[float, float],
    target_vel: Tuple[float, float],
    pn_gain: float = PN_GAIN,
    max_rate: float = MAX_YAW_RATE,
) -> PNSolution:
    """
    Classic proportional navigation.

    Commands lateral acceleration proportional to the line-of-sight rate,
        a_cmd = N * V_c * lambda_dot,
    which drives the LOS rate to zero -- a collision course. The commanded acceleration is
    converted to a yaw rate through the vessel's own speed. Unlike pure pursuit this leads a
    crossing target, so the interceptor's track visibly curves ahead of the hostile.
    """
    rx = target_pos[0] - own_pos[0]
    ry = target_pos[1] - own_pos[1]
    rng = math.hypot(rx, ry)

    own_vx = own_speed * math.cos(own_psi)
    own_vy = own_speed * math.sin(own_psi)
    vx = target_vel[0] - own_vx
    vy = target_vel[1] - own_vy

    if rng < 1e-6:
        return PNSolution(0.0, own_psi, target_pos, 0.0, 0.0, 0.0, 0.0)

    # Closing speed: positive while the range is shrinking.
    closing_speed = -(rx * vx + ry * vy) / rng
    # Line-of-sight rate from the transverse component of relative velocity.
    los_rate = (rx * vy - ry * vx) / (rng * rng)

    # Time-to-go drives the lead point; an opening geometry gets a bounded horizon.
    t_go = rng / closing_speed if closing_speed > 0.1 else min(30.0, rng / 0.5)
    lead_point = (target_pos[0] + target_vel[0] * t_go,
                  target_pos[1] + target_vel[1] * t_go)

    lateral_accel = pn_gain * max(closing_speed, 0.0) * los_rate
    yaw_rate = lateral_accel / max(own_speed, 0.5)

    # Blend in direct steering onto the lead point: PN alone is indifferent to absolute
    # bearing, which looks unphysical at low closing speed and long range.
    lead_heading = math.atan2(lead_point[1] - own_pos[1], lead_point[0] - own_pos[0])
    lead_err = wrap_angle(lead_heading - own_psi)
    yaw_rate = 0.65 * yaw_rate + 0.35 * (lead_err * TERMINAL_HEADING_GAIN)

    return PNSolution(
        yaw_rate_cmd=float(np.clip(yaw_rate, -max_rate, max_rate)),
        desired_heading=lead_heading,
        lead_point=lead_point,
        closing_speed=float(closing_speed),
        time_to_go=float(t_go),
        los_rate=float(los_rate),
        range_to_target=float(rng),
    )


# ---------------------------------------------------------------------------
# Standoff orbit vector field (tracker containment guidance)
# ---------------------------------------------------------------------------
def orbit_vector_field(
    own_pos: Tuple[float, float],
    center: Tuple[float, float],
    radius: float,
    circulation: float = 1.0,
    converge_gain: float = ORBIT_CONVERGE_GAIN,
) -> Tuple[float, float, float]:
    """
    Lyapunov standoff-tracking vector field.

        chi_d = lambda + sigma * (pi/2 + atan(k * e)),   e = (d - R) / R

    Far outside the ring the desired course points inboard; on the ring it is purely
    tangential; inside it points outboard -- so the vessel spirals smoothly onto the
    containment radius and then circulates, never diving at the hostile.

    Returns (desired_course, bearing_from_center, radial_error_metres).
    """
    rx = own_pos[0] - center[0]
    ry = own_pos[1] - center[1]
    d = math.hypot(rx, ry)
    if d < 1e-6:
        # Degenerate: sitting on the hostile. Drive straight out along the reference axis.
        return 0.0, 0.0, -radius

    bearing = math.atan2(ry, rx)
    e = (d - radius) / max(radius, 1e-6)
    sigma = 1.0 if circulation >= 0.0 else -1.0
    desired_course = wrap_angle(bearing + sigma * (math.pi / 2.0 + math.atan(converge_gain * e)))
    return desired_course, bearing, float(d - radius)


@dataclass
class OrbitSolution:
    """Standoff-orbit command for a tracker shadowing a moving hostile."""
    desired_heading: float
    desired_speed: float
    bearing_from_center: float
    radial_error: float
    phase_error: float
    radial_rate: float = 0.0
    tangential_rate: float = 0.0


def standoff_orbit_command(
    own_pos: Tuple[float, float],
    center: Tuple[float, float],
    center_vel: Tuple[float, float],
    radius: float,
    station_bearing: float,
    circulation: float = 1.0,
    orbit_speed: float = 1.3,
    radial_gain: float = ORBIT_RADIAL_GAIN,
    arc_gain: float = ORBIT_ARC_GAIN,
    max_speed: float = MAX_SURGE,
) -> OrbitSolution:
    """
    Standoff orbit about a MOVING hostile, composed in the hostile's own frame.

    The command is built from three independent terms and then transformed back to the ground
    frame, which is what makes station keeping actually work:

        v_radial     = -k_r * (range - radius)        close or open to the standoff radius
        v_tangential =  k_a * (phase_error * radius)  walk around to the assigned station
        v_cmd        =  v_radial * r_hat + v_tangential * t_hat + v_centre

    The tangential term is SIGNED, so a vessel that has run ahead of its station slows below
    the hostile's speed and drops back, instead of only ever being able to sprint forward. A
    forward-only phase trim piles the whole ring onto one arc, because every vessel can catch
    up but none can fall back.

    The centre-velocity feed-forward is what lets the ring translate with the hostile rather
    than being left behind by it.
    """
    rx = own_pos[0] - center[0]
    ry = own_pos[1] - center[1]
    d = math.hypot(rx, ry)

    if d < 1e-6:
        # Degenerate: sitting on the hostile. Drive straight out along the station bearing.
        return OrbitSolution(station_bearing, max_speed, station_bearing, -radius, 0.0)

    bearing = math.atan2(ry, rx)
    radial_err = d - radius
    ph_err = phase_error(bearing, station_bearing, circulation)

    sigma = 1.0 if circulation >= 0.0 else -1.0
    radial_unit = (math.cos(bearing), math.sin(bearing))
    tangential_unit = (-sigma * math.sin(bearing), sigma * math.cos(bearing))

    v_radial = float(np.clip(-radial_gain * radial_err, -max_speed, max_speed))
    # Arc-length error in metres; the baseline orbit rate carries the ring round once on station.
    arc_err = ph_err * max(radius, 1.0)
    v_tangential = float(np.clip(arc_gain * arc_err, -max_speed, max_speed))
    if abs(ph_err) < ORBIT_STATION_DEADBAND:
        # On station: hold the nominal orbit rate so the ring keeps rotating coherently.
        v_tangential = orbit_speed * (ph_err / max(ORBIT_STATION_DEADBAND, 1e-6))

    vx = v_radial * radial_unit[0] + v_tangential * tangential_unit[0] + center_vel[0]
    vy = v_radial * radial_unit[1] + v_tangential * tangential_unit[1] + center_vel[1]
    speed = math.hypot(vx, vy)

    if speed < 1e-6:
        return OrbitSolution(bearing, 0.0, bearing, radial_err, ph_err, v_radial, v_tangential)

    return OrbitSolution(
        desired_heading=math.atan2(vy, vx),
        desired_speed=float(min(speed, max_speed)),
        bearing_from_center=bearing,
        radial_error=radial_err,
        phase_error=ph_err,
        radial_rate=v_radial,
        tangential_rate=v_tangential,
    )


def phase_error(current_bearing: float, station_bearing: float, circulation: float = 1.0) -> float:
    """
    Signed phase error along the orbit, in radians, in the direction of circulation.
    Positive means the vessel is behind its station and should speed up.
    """
    err = wrap_angle(station_bearing - current_bearing)
    return err if circulation >= 0.0 else -err


# ---------------------------------------------------------------------------
# Engagement phase machine
# ---------------------------------------------------------------------------
def select_phase(
    role: str,
    slot_kind: str,
    health: float,
    dist_to_station: float,
    dist_to_target: float,
    mission_elapsed: float,
    strike_authorized: bool = False,
    pincer_active: bool = False,
    defusal_progress: float = 0.0,
    target_neutralized: bool = False,
    is_casualty_station: bool = False,
) -> GuidancePhase:
    """
    Resolves a vessel's engagement phase from its role, health and geometry.
    Damage and mission state take precedence over geometry, so a crippled hull peels off
    instead of pressing an attack it cannot complete.
    """
    if health < H_COMBAT_LOSS or is_casualty_station:
        return GuidancePhase.COMBAT_LOSS
    if health < H_DEGRADED and role != "relay":
        return GuidancePhase.RECOVERY

    if slot_kind == "guard" or (target_neutralized and role == "tracker"):
        return GuidancePhase.ARREST_GUARD

    if mission_elapsed < D_LAUNCH_WINDOW:
        return GuidancePhase.LAUNCH

    if role == "interceptor":
        if defusal_progress > 0.0 and dist_to_target <= D_EMP_LOCK:
            return GuidancePhase.EMP_LOCK
        if dist_to_target <= D_TERMINAL:
            return GuidancePhase.TERMINAL
        if dist_to_station > D_TRANSIT:
            return GuidancePhase.TRANSIT
        return GuidancePhase.DEPLOY

    if role == "tracker":
        if pincer_active and dist_to_station <= D_TRANSIT:
            return GuidancePhase.PINCER
        if dist_to_station > D_TRANSIT:
            return GuidancePhase.TRANSIT
        if dist_to_station <= D_ON_STATION:
            return GuidancePhase.SHADOW
        return GuidancePhase.DEPLOY

    # Relay and scout: transit to station, then hold it.
    if dist_to_station > D_TRANSIT:
        return GuidancePhase.TRANSIT
    return GuidancePhase.SHADOW


def speed_profile(
    phase: GuidancePhase,
    dist_to_station: float,
    target_speed: float,
    ring_phase_error: float = 0.0,
    ring_radius: float = 20.0,
    health: float = 1.0,
    max_speed: float = MAX_SURGE,
) -> float:
    """
    Commanded surge speed for the current phase, before the facing factor and the CBF shield.

    SHADOW and PINCER derive speed from the hostile's own speed plus a phase-lock term, so
    the ring holds formation geometry rather than oscillating between sprint and brake.
    """
    if phase is GuidancePhase.COMBAT_LOSS:
        return 0.0

    if phase is GuidancePhase.RECOVERY:
        # Damaged hull withdrawing to its standoff station under reduced power.
        return min(1.0, max(0.4, 1.0 * (health / max(H_DEGRADED, 1e-3))))

    if phase in (GuidancePhase.LAUNCH, GuidancePhase.TRANSIT):
        return max_speed

    if phase is GuidancePhase.DEPLOY:
        # Fanning out into the containment ring: fast, but bleeding off for arrival.
        return float(np.clip(1.8 + 1.7 * (dist_to_station / D_TRANSIT), 1.0, max_speed))

    if phase is GuidancePhase.TERMINAL:
        return max_speed

    if phase is GuidancePhase.EMP_LOCK:
        # Hold the capture corridor: match the hostile and trim on station error.
        return float(np.clip(target_speed + 0.5 * dist_to_station, 0.0, 2.0))

    if phase is GuidancePhase.ARREST_GUARD:
        return float(np.clip(0.25 + 0.35 * dist_to_station, 0.0, 1.5))

    if phase is GuidancePhase.PINCER:
        # Squeezing in: hostile speed, plus phase-lock, plus deliberate closing bias.
        base = target_speed + 0.45
    else:  # SHADOW
        base = target_speed

    phase_term = PHASE_LOCK_GAIN * ring_phase_error * max(ring_radius, 1.0) * 0.1
    station_term = 0.45 * dist_to_station
    return float(np.clip(base + phase_term + station_term, 0.1, max_speed))


# ---------------------------------------------------------------------------
# Transit lane deconfliction
# ---------------------------------------------------------------------------
def transit_waypoint(
    own_pos: Tuple[float, float],
    station: Tuple[float, float],
    lane_index: int,
    lane_count: int,
    lane_spacing: float = 7.0,
) -> Tuple[float, float]:
    """
    Offsets a transit aim point laterally so a wing crosses open water abreast in separate
    lanes rather than stacked single file. The offset collapses to zero on arrival, so the
    vessel still converges exactly onto its station.
    """
    dx = station[0] - own_pos[0]
    dy = station[1] - own_pos[1]
    dist = math.hypot(dx, dy)
    if dist < 1e-6 or lane_count <= 1:
        return station

    # Lane offset shrinks inside the transit envelope: wide apart far out, converged close in.
    taper = min(1.0, max(0.0, (dist - D_ON_STATION) / D_TRANSIT))
    offset = (lane_index - (lane_count - 1) / 2.0) * lane_spacing * taper
    nx = -dy / dist
    ny = dx / dist
    return (station[0] + nx * offset, station[1] + ny * offset)


def circulation_for_station(station_bearing: float, reference_heading: float) -> float:
    """
    Circulation sense for a ring station: vessels orbit the shorter way toward their station
    so the ring settles without a full lap of overshoot.
    """
    return 1.0 if wrap_angle(station_bearing - reference_heading) >= 0.0 else -1.0
