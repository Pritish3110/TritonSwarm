"""
USV Swarm Simulation - Deterministic Hungarian Role Assigner
Author: Principal Enterprise Engineer
Stage: 1 & 2 (Shared Role Management Engine)

Implements Section 4 specifications:
- Real-time Hungarian algorithm (scipy.optimize.linear_sum_assignment) over cost matrix.
- Evaluates health belief h_i(t), agent positions, and slot geometries.
- Allocates roles: Tracker / Interceptor (on 20m ring), Relay (mesh bridge), and Scout (perimeter).
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment


class HungarianRoleManager:
    """Computes real-time optimal agent-to-role assignment using the Hungarian algorithm."""

    def __init__(self, r_ring: float = 20.0, r_perimeter: float = 45.0):
        self.r_ring = r_ring
        self.r_perimeter = r_perimeter
        self.prev_slots: Dict[int, int] = {}

    def assign_roles(
        self,
        agent_positions: Dict[int, Tuple[float, float]],
        agent_health: Dict[int, float],
        target_pos: Tuple[float, float],
        target_heading: float
    ) -> Dict[int, Tuple[str, Tuple[float, float]]]:
        """
        Returns mapping: agent_id -> (role_name, target_slot_coordinates)
        """
        agent_ids = sorted(list(agent_positions.keys()))
        N = len(agent_ids)
        if N == 0:
            return {}

        tx, ty = target_pos

        # Determine number of tracker/interceptor slots needed (typically 3 to 4)
        num_trackers = min(4, max(3, sum(1 for h in agent_health.values() if h >= 0.3)))
        num_relays = 1 if N > 4 else 0
        num_scouts = max(0, N - num_trackers - num_relays)

        # Generate target geometric slots
        slots = []
        slot_roles = []

        # 1. Encirclement Ring Slots (Trackers / Interceptors)
        for k in range(num_trackers):
            angle = target_heading + (2.0 * math.pi / num_trackers) * k
            sx = tx + self.r_ring * math.cos(angle)
            sy = ty + self.r_ring * math.sin(angle)
            slots.append((sx, sy))
            slot_roles.append("tracker")

        # 2. Relay Slot (bridge between 20m ring and outer perimeter)
        for _ in range(num_relays):
            # Standoff at intermediate radius (30m) opposite to target heading
            angle = target_heading + math.pi
            sx = tx + 30.0 * math.cos(angle)
            sy = ty + 30.0 * math.sin(angle)
            slots.append((sx, sy))
            slot_roles.append("relay")

        # 3. Scout Slots (Outer perimeter 45m)
        for k in range(num_scouts):
            angle = target_heading + math.pi + 0.6 + (2.0 * math.pi / max(1, num_scouts)) * k
            sx = tx + self.r_perimeter * math.cos(angle)
            sy = ty + self.r_perimeter * math.sin(angle)
            slots.append((sx, sy))
            slot_roles.append("scout")

        # Ensure slot count matches agent count
        while len(slots) < N:
            slots.append((tx + self.r_perimeter, ty))
            slot_roles.append("scout")

        # Build Cost Matrix [N_agents x N_slots]
        cost_matrix = np.zeros((N, N), dtype=np.float64)

        for i_idx, aid in enumerate(agent_ids):
            ax, ay = agent_positions[aid]
            h = agent_health.get(aid, 1.0)

            for s_idx in range(N):
                sx, sy = slots[s_idx]
                role = slot_roles[s_idx]
                dist = math.hypot(ax - sx, ay - sy)

                hysteresis = -15.0 if self.prev_slots.get(aid) == s_idx else 0.0

                if role in ["tracker", "interceptor"]:
                    # Degraded agents (h < 0.4) are heavily penalized for primary tracking slots
                    health_penalty = (1.0 - h) * 150.0 if h < 0.5 else (1.0 - h) * 20.0
                    cost = dist + health_penalty + hysteresis

                elif role == "relay":
                    # Degraded agents with reduced propulsion are favored for relay duty
                    propulsion_favor = (1.0 - h) * 40.0
                    cost = dist - propulsion_favor + hysteresis

                else:  # scout
                    cost = dist + (1.0 - h) * 10.0 + hysteresis

                cost_matrix[i_idx, s_idx] = cost

        # Solve optimal bipartite matching
        row_ind, col_ind = linear_sum_assignment(cost_matrix)

        assignments: Dict[int, Tuple[str, Tuple[float, float]]] = {}
        for r, c in zip(row_ind, col_ind):
            aid = agent_ids[r]
            assignments[aid] = (slot_roles[c], slots[c])
            self.prev_slots[aid] = c

        return assignments
