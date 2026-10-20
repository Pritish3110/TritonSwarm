"""
USV Swarm Simulation - Domain-Randomized Fault Generator
Author: Principal Enterprise Engineer
Stage: 1 & 2 (Shared Fault Generation Engine)

Simulates:
- GPS bias, drift, and noise
- IMU gyro bias and drift
- Perception detection dropouts
- Thruster power degradation (0.0 to 1.0)
- Battery sag and abnormal discharge
- Dynamic communication packet loss (0 to 40%) & mesh partitioning
- Discrete fault scheduling (Shi et al., domain randomization)
"""

from __future__ import annotations
import math
import random
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Set, Tuple
import numpy as np


class FaultType(IntEnum):
    HEALTHY = 0
    GPS_FAULT = 1
    THRUSTER_FAULT = 2
    LINK_LOSS = 3


@dataclass
class AgentFaultProfile:
    agent_id: int
    active_fault: FaultType = FaultType.HEALTHY
    fault_start_time: float = -1.0

    # Sensor faults
    gps_bias_x: float = 0.0
    gps_bias_y: float = 0.0
    gps_drift_rate: float = 0.0
    gps_noise_std: float = 0.3    # nominal 0.3m
    imu_bias_r: float = 0.0       # rad/s
    imu_noise_std: float = 0.02   # rad/s
    detection_dropout_prob: float = 0.02 # nominal 2%

    # Actuator faults
    thrust_efficiency: float = 1.0 # [0.0 = total loss, 1.0 = nominal]

    # Power/battery
    battery_level: float = 1.0    # 1.0 = 100%
    battery_drain_rate: float = 0.0001 # per second

    # Network
    packet_loss_rate: float = 0.05 # nominal 5%
    comm_range: float = 80.0       # meters


class FaultInjector:
    """Manages domain-randomized and scheduled fault injection across the USV fleet."""

    def __init__(
        self,
        num_agents: int,
        node_fault_ratio: float = 0.30,  # up to 30% degraded nodes
        max_link_loss_rate: float = 0.40, # up to 40% packet loss
        seed: Optional[int] = None
    ):
        self.num_agents = num_agents
        self.node_fault_ratio = node_fault_ratio
        self.max_link_loss_rate = max_link_loss_rate
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.profiles: Dict[int, AgentFaultProfile] = {}
        self.reset()

    def reset(self, randomize: bool = True) -> None:
        """Resets fault states for all agents, optionally applying domain randomization."""
        self.profiles.clear()
        for i in range(self.num_agents):
            self.profiles[i] = AgentFaultProfile(agent_id=i)

        if randomize:
            # Determine how many agents will experience degradation (0 up to node_fault_ratio * N)
            max_faulty = int(math.ceil(self.num_agents * self.node_fault_ratio))
            num_faulty = self.rng.randint(0, max_faulty)

            if num_faulty > 0:
                faulty_agents = self.rng.sample(range(self.num_agents), num_faulty)
                for agent_id in faulty_agents:
                    # Pick a fault type
                    fault_type = self.rng.choice([
                        FaultType.GPS_FAULT,
                        FaultType.THRUSTER_FAULT,
                        FaultType.LINK_LOSS
                    ])
                    # Schedule fault injection between t=5s and t=25s
                    fault_time = self.rng.uniform(5.0, 25.0)
                    self._schedule_fault(agent_id, fault_type, fault_time)

    def _schedule_fault(self, agent_id: int, fault_type: FaultType, start_time: float) -> None:
        profile = self.profiles[agent_id]
        profile.active_fault = fault_type
        profile.fault_start_time = start_time

    def update(self, current_time: float, dt: float) -> None:
        """Updates active fault parameters based on simulation time."""
        for agent_id, prof in self.profiles.items():
            # Update battery
            prof.battery_level = max(0.0, prof.battery_level - prof.battery_drain_rate * dt)

            # Check if scheduled fault triggers
            if prof.fault_start_time > 0 and current_time >= prof.fault_start_time:
                if prof.active_fault == FaultType.GPS_FAULT:
                    if prof.gps_bias_x == 0.0 and prof.gps_bias_y == 0.0:
                        # Inject sudden 8m-20m bias + continuous drift
                        angle = self.rng.uniform(0, 2 * math.pi)
                        magnitude = self.rng.uniform(8.0, 18.0)
                        prof.gps_bias_x = magnitude * math.cos(angle)
                        prof.gps_bias_y = magnitude * math.sin(angle)
                        prof.gps_drift_rate = self.rng.uniform(0.1, 0.4)
                        prof.gps_noise_std = 1.2
                        prof.detection_dropout_prob = 0.25
                    # Drift grows
                    prof.gps_bias_x += prof.gps_drift_rate * dt * 0.5
                    prof.gps_bias_y += prof.gps_drift_rate * dt * 0.5

                elif prof.active_fault == FaultType.THRUSTER_FAULT:
                    # 50% power degradation down to complete failure
                    prof.thrust_efficiency = 0.35
                    # Battery draws unevenly
                    prof.battery_drain_rate = 0.0005

                elif prof.active_fault == FaultType.LINK_LOSS:
                    # Packet drop elevates up to 40%
                    prof.packet_loss_rate = self.rng.uniform(0.30, self.max_link_loss_rate)
                    prof.comm_range = 35.0 # degraded transmission range

    def corrupt_gps(self, agent_id: int, true_x: float, true_y: float) -> Tuple[float, float]:
        """Applies GPS noise, bias, and drift to true position."""
        prof = self.profiles[agent_id]
        noise_x = self.np_rng.normal(0.0, prof.gps_noise_std)
        noise_y = self.np_rng.normal(0.0, prof.gps_noise_std)
        meas_x = true_x + prof.gps_bias_x + noise_x
        meas_y = true_y + prof.gps_bias_y + noise_y
        return float(meas_x), float(meas_y)

    def corrupt_imu(self, agent_id: int, true_r: float) -> float:
        """Applies IMU bias and noise to yaw rate."""
        prof = self.profiles[agent_id]
        noise_r = self.np_rng.normal(0.0, prof.imu_noise_std)
        return float(true_r + prof.imu_bias_r + noise_r)

    def is_detection_dropped(self, agent_id: int) -> bool:
        """Determines if a perception detection is dropped in this tick."""
        return self.rng.random() < self.profiles[agent_id].detection_dropout_prob

    def can_communicate(self, src_id: int, dst_id: int, dist: float) -> bool:
        """Determines if a packet is successfully transmitted over the ad-hoc mesh."""
        prof_src = self.profiles[src_id]
        prof_dst = self.profiles[dst_id]

        effective_range = min(prof_src.comm_range, prof_dst.comm_range)
        if dist > effective_range:
            return False

        # Effective packet loss rate
        drop_rate = max(prof_src.packet_loss_rate, prof_dst.packet_loss_rate)
        return self.rng.random() >= drop_rate

    def get_ground_truth_health(self, agent_id: int) -> Tuple[FaultType, float]:
        """Returns the ground-truth fault category and true health index h_i(t) in [0, 1]."""
        prof = self.profiles[agent_id]
        fault = prof.active_fault

        if fault == FaultType.HEALTHY:
            health_index = 1.0
        elif fault == FaultType.GPS_FAULT:
            # Degraded perception health
            health_index = 0.50
        elif fault == FaultType.THRUSTER_FAULT:
            # Degraded propulsion health
            health_index = prof.thrust_efficiency
        elif fault == FaultType.LINK_LOSS:
            # Degraded comms health
            health_index = max(0.2, 1.0 - prof.packet_loss_rate)
        else:
            health_index = 1.0

        return fault, float(health_index)
