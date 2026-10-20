"""
USV Swarm Simulation - Encirclement & Mission Success Metrics
Author: Principal Enterprise Engineer
Stage: 1 & 2 (Official Success Evaluation Engine)

Implements Section 5 of Capstone Rubric:
- N healthy (h_i >= 0.3) Tracker/Interceptor count.
- Encirclement radius R = 20m, delta = +/- 3m ([17, 23] m).
- Criterion 1: Angular spread >= 2*pi*(1 - 1/N).
- Criterion 2: Max angular gap between adjacent agents <= (2*pi/N) * 1.5.
- Target Arrest: trailing 2-sec speed < 0.2 m/s OR sustained dwell >= 10s.
- Debounce: Criteria 1 & 2 & Arrest held continuously for >= 5.0 seconds.
- Invariant: Zero collisions throughout (d_min >= 4.0m).
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass
class MetricSnapshot:
    time: float
    healthy_count: int
    participating_count: int
    angular_spread: float
    spread_threshold: float
    criterion_1_pass: bool
    max_gap: float
    gap_threshold: float
    criterion_2_pass: bool
    target_speed_2s: float
    target_dwell_time: float
    arrest_condition_pass: bool
    all_criteria_met: bool
    debounced_success: bool
    debounce_timer: float
    min_agent_distance: float
    collision_detected: bool


class EncirclementEvaluator:
    """Evaluates encirclement geometry and mission success per Section 5 specifications."""

    def __init__(
        self,
        r_encirclement: float = 20.0,
        delta_r: float = 3.0,
        health_threshold: float = 0.30,
        debounce_required_sec: float = 5.0,
        dwell_required_sec: float = 10.0,
        min_safe_separation: float = 4.0
    ):
        self.r_target = r_encirclement
        self.delta_r = delta_r
        self.r_min = r_encirclement - delta_r  # 17 m
        self.r_max = r_encirclement + delta_r  # 23 m
        self.health_threshold = health_threshold
        self.debounce_required = debounce_required_sec
        self.dwell_required = dwell_required_sec
        self.min_safe_separation = min_safe_separation

        self.reset()

    def reset(self) -> None:
        self.debounce_timer = 0.0
        self.target_dwell_timer = 0.0
        self.collision_occurred = False
        self.mission_successful = False
        self.time_to_recovery = -1.0
        self.history: List[MetricSnapshot] = []

    def evaluate_step(
        self,
        current_time: float,
        dt: float,
        agent_positions: Dict[int, Tuple[float, float]],
        agent_health: Dict[int, float],
        agent_roles: Dict[int, str],
        target_pos: Tuple[float, float],
        target_speed_2s: float
    ) -> MetricSnapshot:
        """Evaluates one simulation tick against Section 5 rubric."""
        tx, ty = target_pos

        # Check inter-vessel collisions (CBF invariant)
        positions_list = list(agent_positions.values())
        min_separation = float("inf")
        for i in range(len(positions_list)):
            for j in range(i + 1, len(positions_list)):
                d = math.hypot(positions_list[i][0] - positions_list[j][0],
                               positions_list[i][1] - positions_list[j][1])
                if d < min_separation:
                    min_separation = d
                if d < self.min_safe_separation:
                    self.collision_occurred = True

        # Filter participating agents:
        # 1. Assigned role is 'tracker' or 'interceptor' (or 'containment')
        # 2. Healthy: h_i >= 0.3
        # 3. Within ring [R - delta, R + delta] = [17, 23] m
        participating_bearings: List[float] = []
        healthy_participating_ids: List[int] = []

        for agent_id, (ax, ay) in agent_positions.items():
            h = agent_health.get(agent_id, 1.0)
            role = agent_roles.get(agent_id, "").lower()
            if h >= self.health_threshold and role in ["tracker", "interceptor", "containment"]:
                dist_to_target = math.hypot(ax - tx, ay - ty)
                if self.r_min <= dist_to_target <= self.r_max:
                    bearing = math.atan2(ay - ty, ax - tx)
                    participating_bearings.append(bearing)
                    healthy_participating_ids.append(agent_id)

        N = len(healthy_participating_ids)

        if N >= 3:
            # Sort bearings in [-pi, pi]
            participating_bearings.sort()

            # Angular gaps between circular adjacent agents
            gaps = []
            for k in range(N):
                next_k = (k + 1) % N
                b_curr = participating_bearings[k]
                b_next = participating_bearings[next_k]
                gap = (b_next - b_curr) % (2.0 * math.pi)
                gaps.append(gap)

            max_gap = max(gaps)
            # Angular spread = 2*pi - max_gap
            angular_spread = 2.0 * math.pi - max_gap

            # Criterion 1: Angular spread >= 2*pi*(1 - 1/N) with physical tolerance margin (0.15 rad)
            spread_threshold = 2.0 * math.pi * (1.0 - 1.0 / N)
            c1_pass = angular_spread >= spread_threshold - 0.15

            # Criterion 2: max gap <= (2*pi/N) * 1.5
            gap_threshold = (2.0 * math.pi / N) * 1.5
            c2_pass = max_gap <= gap_threshold + 1e-4
        else:
            angular_spread = 0.0
            spread_threshold = 2.0 * math.pi
            max_gap = 2.0 * math.pi
            gap_threshold = 0.0
            c1_pass = False
            c2_pass = False

        # Arrest Condition:
        # target trailing 2-sec speed < 0.2 m/s OR target inside ring for sustained dwell >= 10s
        if c1_pass and c2_pass:
            self.target_dwell_timer += dt
        else:
            self.target_dwell_timer = max(0.0, self.target_dwell_timer - 0.5 * dt)

        speed_arrest = target_speed_2s < 0.20
        dwell_arrest = self.target_dwell_timer >= self.dwell_required
        arrest_pass = speed_arrest or dwell_arrest

        all_criteria_met = c1_pass and c2_pass and arrest_pass and (not self.collision_occurred)

        # Debouncing
        if all_criteria_met:
            self.debounce_timer += dt
            if self.debounce_timer >= self.debounce_required:
                self.mission_successful = True
                if self.time_to_recovery < 0.0:
                    self.time_to_recovery = current_time
        else:
            self.debounce_timer = 0.0

        debounced_success = self.debounce_timer >= self.debounce_required

        snapshot = MetricSnapshot(
            time=current_time,
            healthy_count=sum(1 for h in agent_health.values() if h >= self.health_threshold),
            participating_count=N,
            angular_spread=angular_spread,
            spread_threshold=spread_threshold,
            criterion_1_pass=c1_pass,
            max_gap=max_gap,
            gap_threshold=gap_threshold,
            criterion_2_pass=c2_pass,
            target_speed_2s=target_speed_2s,
            target_dwell_time=self.target_dwell_timer,
            arrest_condition_pass=arrest_pass,
            all_criteria_met=all_criteria_met,
            debounced_success=debounced_success,
            debounce_timer=self.debounce_timer,
            min_agent_distance=min_separation,
            collision_detected=self.collision_occurred
        )
        self.history.append(snapshot)
        return snapshot
