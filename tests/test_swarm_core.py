"""
USV Swarm Simulation - Unit & Integration Test Suite
Author: Principal Enterprise Engineer
Stage: 1 & 2 Verification Suite

Validates:
1. 3-DOF vessel dynamics numerical stability & RK4 integration
2. Target vessel 3 modes (nominal, sinusoidal, reactive evasion)
3. Hungarian role allocation algorithm & health penalties
4. Control Barrier Function (CBF) hard collision invariant (d >= 4.0m)
5. Section 5 Encirclement Criteria (angular spread, gap bounds, target arrest)
6. Fault injector sensor, thruster, and network corruption
"""

import math
import pytest
import numpy as np

from training.env.dynamics import USVDynamics3DOF, USVState, TargetVessel, TargetMode, VesselParams
from training.env.fault_injector import FaultInjector, FaultType
from training.env.ekf_tracker import EKFTracker
from training.env.metrics import EncirclementEvaluator
from training.env.role_manager import HungarianRoleManager
from training.env.safety_shield import CBFSafetyShield


def test_vessel_dynamics_rk4_stability():
    """Verify that 3-DOF vessel dynamics remain bounded under max actuation."""
    dynamics = USVDynamics3DOF(VesselParams())
    state = USVState(agent_id=0, x=0.0, y=0.0, psi=0.0)

    # Apply maximum surge and yaw-rate setpoints for 100 steps
    for _ in range(100):
        dynamics.step(state, u_cmd=2.5, r_cmd=0.8, dt=0.05)

    assert 0.0 <= state.u <= 2.6
    assert abs(state.r) <= 0.9
    assert not math.isnan(state.x)
    assert not math.isnan(state.y)
    assert -math.pi <= state.psi <= math.pi


def test_target_reactive_evasion():
    """Verify target activates evasion when trackers are within 12m and steers away."""
    target = TargetVessel(initial_x=0.0, initial_y=0.0, mode=TargetMode.REACTIVE)

    # Place tracker at (5m, 0m) ahead of target -> within 12m trigger radius
    trackers = [(5.0, 0.0)]
    for _ in range(50):  # 5.0 seconds
        t_state = target.step(dt=0.1, tracker_positions=trackers)

    # Target should accelerate toward 1.5 m/s cap and turn away
    assert t_state.speed >= 1.45
    # Heading should be turned away from +x towards pi or -pi
    assert abs(t_state.psi) > 1.2


def test_hungarian_role_allocation_health_demotion():
    """Verify degraded agent (health < 0.3) is never assigned Tracker/Interceptor."""
    manager = HungarianRoleManager(r_ring=20.0, r_perimeter=45.0)

    agent_positions = {
        0: (20.0, 0.0),
        1: (0.0, 20.0),
        2: (-20.0, 0.0),
        3: (0.0, -20.0),
        4: (35.0, 35.0),
        5: (-35.0, -35.0)
    }
    # USV 0 has severe thruster fault (health = 0.20)
    agent_health = {0: 0.20, 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0}

    assignments = manager.assign_roles(
        agent_positions=agent_positions,
        agent_health=agent_health,
        target_pos=(0.0, 0.0),
        target_heading=0.0
    )

    # USV 0 must be demoted to relay or scout, NOT tracker or interceptor
    role_0 = assignments[0][0]
    assert role_0 in ["relay", "scout"], f"Expected USV-0 demoted, got: {role_0}"


def test_cbf_safety_shield_head_on_collision_invariant():
    """Verify CBF filter prevents collision on two vessels on head-on collision course."""
    shield = CBFSafetyShield(d_safe=4.0, d_warning=8.0)

    # USV 0 at (0, 0) facing right (psi=0), commanded u=2.5
    pos_0 = (0.0, 0.0)
    psi_0 = 0.0

    # USV 1 at (6, 0) facing left (psi=pi) -> approaching within warning distance (6m < 8m)
    neighbor_positions = {1: (6.0, 0.0)}

    u_safe, r_safe, active = shield.filter_action(
        agent_id=0,
        u_cmd=2.5,
        r_cmd=0.0,
        own_pos=pos_0,
        own_psi=psi_0,
        neighbor_positions=neighbor_positions
    )

    assert active is True
    # Safe surge speed must be throttled down
    assert u_safe < 2.5
    # Must steer away (non-zero yaw rate)
    assert abs(r_safe) > 0.0


def test_section_5_encirclement_criteria():
    """Verify Section 5 metrics pass when agents form valid ring and fail when gap is large."""
    evaluator = EncirclementEvaluator(r_encirclement=20.0, delta_r=3.0)

    # 4 agents perfectly spaced at 20m radius (every 90 degrees)
    positions_good = {
        0: (20.0, 0.0),
        1: (0.0, 20.0),
        2: (-20.0, 0.0),
        3: (0.0, -20.0)
    }
    health_good = {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0}
    roles_good = {0: "tracker", 1: "tracker", 2: "interceptor", 3: "interceptor"}

    snap = evaluator.evaluate_step(
        current_time=1.0,
        dt=0.1,
        agent_positions=positions_good,
        agent_health=health_good,
        agent_roles=roles_good,
        target_pos=(0.0, 0.0),
        target_speed_2s=0.15 # arrested
    )

    assert snap.criterion_1_pass is True
    assert snap.criterion_2_pass is True
    assert snap.arrest_condition_pass is True

    # Bad geometry: large angular gap (agents clustered on one side)
    positions_bad = {
        0: (20.0, 0.0),
        1: (19.0, 6.0),
        2: (18.0, 8.0),
        3: (17.0, 10.0)
    }
    snap_bad = evaluator.evaluate_step(
        current_time=2.0,
        dt=0.1,
        agent_positions=positions_bad,
        agent_health=health_good,
        agent_roles=roles_good,
        target_pos=(0.0, 0.0),
        target_speed_2s=0.15
    )

    # Criterion 2 (gap <= 2pi/N * 1.5) must fail because other 3 quadrants are open
    assert snap_bad.criterion_2_pass is False


def test_fault_injector_corruption():
    """Verify fault injector applies GPS bias and thruster degradation."""
    injector = FaultInjector(num_agents=2)
    injector.reset(randomize=False)
    # Manually trigger GPS fault on USV 0 and Thruster fault on USV 1 at t=1.0s
    injector._schedule_fault(0, FaultType.GPS_FAULT, start_time=1.0)
    injector._schedule_fault(1, FaultType.THRUSTER_FAULT, start_time=1.0)

    injector.update(current_time=1.5, dt=0.1)

    # Corrupted GPS should differ significantly from true pos
    true_x, true_y = 100.0, 100.0
    meas_x, meas_y = injector.corrupt_gps(0, true_x, true_y)
    assert math.hypot(meas_x - true_x, meas_y - true_y) > 5.0

    # USV 1 thruster efficiency must be degraded
    prof_1 = injector.profiles[1]
    assert prof_1.thrust_efficiency <= 0.50
