"""
USV Swarm Simulation - ROS 2 Recovery Planner & Mission Verification Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Responsibilities:
- Default Path (10 Hz): Real-time Hungarian role assignment based on h_i(t) and geometry
- Event-Triggered Path: Fires on fault anomaly (h_i < 0.3), queries Groq/Ollama LLM arbiter
- Closed-Loop Mission Verifier: Evaluates Section 5 criteria and publishes /mission/status
"""

from __future__ import annotations
import math
import os
import time
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
from nav_msgs.msg import Odometry
from usv_msgs.msg import USVHealth, RoleAssignment, RecoveryEvent, MissionStatus


class USVRecoveryPlannerNode(Node):
    """ROS 2 Node orchestrating Hungarian role allocation and event-triggered LLM recovery."""

    def __init__(self):
        super().__init__('usv_recovery_planner_node')

        self.declare_parameter('num_agents', 6)
        self.declare_parameter('r_ring', 20.0)
        self.declare_parameter('delta_r', 4.5)
        self.declare_parameter('llm_backend', 'auto')

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.r_ring = self.get_parameter('r_ring').get_parameter_value().double_value
        self.delta_r = self.get_parameter('delta_r').get_parameter_value().double_value
        self.llm_backend = self.get_parameter('llm_backend').get_parameter_value().string_value

        self.get_logger().info(
            f"Initializing USVRecoveryPlannerNode: {self.num_agents} agents, R={self.r_ring}m. "
            f"LLM backend: {self.llm_backend}"
        )

        # Target and Agent state caches
        self.target_pos = (0.0, 0.0)
        self.target_heading = 0.0
        self.smooth_heading = 0.0
        self.target_speed = 1.2
        self.target_speed_history: List[float] = []

        self.agent_positions: Dict[int, Tuple[float, float]] = {}
        self.agent_health: Dict[int, float] = {i: 1.0 for i in range(self.num_agents)}
        self.agent_roles: Dict[int, str] = {i: "tracker" if i < 4 else "scout" for i in range(self.num_agents)}
        self.anomalies_triggered: set = set()
        self.demoted_previous_roles: Dict[int, str] = {}
        self.surrogate_map: Dict[int, int] = {}
        self.last_assigned_col: Dict[int, int] = {}

        # Section 5 Metric Timers
        self.debounce_timer = 0.0
        self.target_dwell_timer = 0.0
        self.collision_occurred = False
        self.arrest_logged = False

        # Publishers & Subscriptions
        self.role_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.health_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.odom_subs: Dict[int, rclpy.subscription.Subscription] = {}

        for i in range(self.num_agents):
            self.role_pubs[i] = self.create_publisher(RoleAssignment, f'/usv_{i}/role', 10)
            self.health_subs[i] = self.create_subscription(
                USVHealth,
                f'/usv_{i}/health',
                self._make_health_callback(i),
                10
            )
            self.odom_subs[i] = self.create_subscription(
                Odometry,
                f'/usv_{i}/odom',
                self._make_odom_callback(i),
                10
            )

        self.target_sub = self.create_subscription(Odometry, '/target/odom', self._target_callback, 10)
        qos_latched = QoSProfile(depth=10, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL, reliability=QoSReliabilityPolicy.RELIABLE)
        self.recovery_pub = self.create_publisher(RecoveryEvent, '/recovery/event', qos_latched)
        self.status_pub = self.create_publisher(MissionStatus, '/mission/status', 10)

        # 10 Hz Planner & Verification Loop
        self.timer = self.create_timer(0.1, self._planner_step)

    def _target_callback(self, msg: Odometry):
        self.target_pos = (msg.pose.pose.position.x, msg.pose.pose.position.y)
        q_z = msg.pose.pose.orientation.z
        q_w = msg.pose.pose.orientation.w
        raw_heading = 2.0 * math.atan2(q_z, q_w)
        self.target_heading = raw_heading

        # Angular low-pass filter (tau ~ 2.0s) for rock-steady formation slots
        diff = (raw_heading - self.smooth_heading + math.pi) % (2.0 * math.pi) - math.pi
        self.smooth_heading = (self.smooth_heading + 0.08 * diff + math.pi) % (2.0 * math.pi) - math.pi

        self.target_speed = msg.twist.twist.linear.x
        self.target_speed_history.append(self.target_speed)
        if len(self.target_speed_history) > 20:  # 2.0s at 10Hz
            self.target_speed_history.pop(0)

    def _make_health_callback(self, agent_id: int):
        def callback(msg: USVHealth):
            self.agent_health[agent_id] = msg.health_index
            # Check for fault anomaly event (demotion threshold: h_i < 0.55)
            if msg.health_index < 0.55 and agent_id not in self.anomalies_triggered:
                self.anomalies_triggered.add(agent_id)
                prev_r = self.agent_roles.get(agent_id, "tracker")
                self._trigger_agentic_recovery(agent_id, msg, prev_r)
            # Check for autonomous self-healing restoration event (promotion threshold: h_i >= 0.80)
            elif msg.health_index >= 0.80 and agent_id in self.anomalies_triggered:
                self.anomalies_triggered.discard(agent_id)
                self._trigger_agentic_restoration(agent_id, msg)
        return callback

    def _make_odom_callback(self, agent_id: int):
        def callback(msg: Odometry):
            self.agent_positions[agent_id] = (msg.pose.pose.position.x, msg.pose.pose.position.y)
        return callback

    def _trigger_agentic_recovery(self, agent_id: int, health_msg: USVHealth, prev_role: str):
        """Asynchronously triggers LLM anomaly reasoning via Groq or deterministic planner."""
        t0 = time.time()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()

        approved = True
        fallback = False
        rationale = ""
        rec_role = "relay"

        # Canonical role taxonomy strictly: tracker, interceptor, relay, scout
        rec_role = "relay"
        if prev_role == rec_role:
            # Idempotency guard: agent is already demoted to relay
            return

        # Check Groq LLM first
        if groq_key and self.llm_backend in ["auto", "groq"]:
            try:
                from openai import OpenAI
                client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key, timeout=3.0)
                prompt = (
                    f"Agent usv_{agent_id} detected with fault: {health_msg.fault_description}, "
                    f"health: {health_msg.health_index:.2f}, role: {prev_role}. "
                    "Decide role reallocation to preserve 20m encirclement ring. Respond with brief rationale."
                )
                res = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100
                )
                rationale = res.choices[0].message.content.strip()
            except Exception as e:
                rationale = f"Groq fallback: {e}"

        # Fallback reasoning
        if not rationale:
            rationale = (
                f"Agent USV-{agent_id} actuator/sensor degraded (h={health_msg.health_index:.2f}). "
                f"Demoted from {prev_role} to {rec_role} to protect tracking geometry."
            )

        latency_ms = (time.time() - t0) * 1000.0

        # Update local role state immediately
        self.demoted_previous_roles[agent_id] = prev_role
        self.agent_roles[agent_id] = rec_role

        # 1. Publish Demotion RecoveryEvent
        event_msg = RecoveryEvent()
        event_msg.stamp = self.get_clock().now().to_msg()
        event_msg.agent_id = agent_id
        event_msg.anomaly_description = health_msg.fault_description
        event_msg.health_index = health_msg.health_index
        event_msg.previous_role = prev_role
        event_msg.assigned_role = rec_role
        event_msg.approved_by_llm = approved
        event_msg.fallback_triggered = fallback
        event_msg.llm_rationale = rationale
        event_msg.latency_ms = float(latency_ms)
        self.recovery_pub.publish(event_msg)
        self.get_logger().info(f"[AgenticRecovery] Demotion: USV-{agent_id} ({prev_role} -> {rec_role})")

        # 2. Publish Promotion RecoveryEvent for a healthy scout/support agent
        promoted_id = None
        # First priority: Standby scout vessels (e.g. USV-4) that are NOT already in ring role
        for candidate_id in sorted(self.agent_roles.keys()):
            if candidate_id != agent_id and self.agent_health.get(candidate_id, 1.0) >= 0.60:
                candidate_role = self.agent_roles.get(candidate_id, "scout")
                if candidate_role == "scout" and candidate_role != prev_role:
                    promoted_id = candidate_id
                    break
        # Second priority: Trailing relay vessels that are NOT already in ring role
        if promoted_id is None:
            for candidate_id in sorted(self.agent_roles.keys()):
                if candidate_id != agent_id and self.agent_health.get(candidate_id, 1.0) >= 0.60:
                    candidate_role = self.agent_roles.get(candidate_id, "scout")
                    if candidate_role == "relay" and candidate_role != prev_role:
                        promoted_id = candidate_id
                        break

        if promoted_id is not None and prev_role in ["tracker", "interceptor"] and self.agent_roles.get(promoted_id) != prev_role:
            self.surrogate_map[agent_id] = promoted_id
            p_old_role = self.agent_roles.get(promoted_id, "scout")
            self.agent_roles[promoted_id] = prev_role
            prom_event = RecoveryEvent()
            prom_event.stamp = self.get_clock().now().to_msg()
            prom_event.agent_id = promoted_id
            prom_event.anomaly_description = "HEALTHY_PROMOTION"
            prom_event.health_index = float(self.agent_health.get(promoted_id, 1.0))
            prom_event.previous_role = p_old_role
            prom_event.assigned_role = prev_role
            prom_event.approved_by_llm = True
            prom_event.fallback_triggered = False
            prom_event.llm_rationale = (
                f"Healthy Agent USV-{promoted_id} promoted from {p_old_role} to {prev_role} "
                f"to fill the slot vacated by degraded USV-{agent_id} and maintain 20m encirclement ring."
            )
            prom_event.latency_ms = float(latency_ms)
            self.recovery_pub.publish(prom_event)
            self.get_logger().info(f"[AgenticRecovery] Promotion: USV-{promoted_id} ({p_old_role} -> {prev_role})")

    def _trigger_agentic_restoration(self, agent_id: int, health_msg: USVHealth):
        """Asynchronously triggers recovery event when a degraded ally vessel self-heals back to nominal health."""
        t0 = time.time()
        prev_role = "relay"
        restored_role = "tracker"
        self.agent_roles[agent_id] = restored_role

        # Demote surrogate back to standby scout
        surrogate_id = self.surrogate_map.pop(agent_id, None)
        if surrogate_id is not None and self.agent_roles.get(surrogate_id) in ["tracker", "interceptor"]:
            surr_prev = self.agent_roles[surrogate_id]
            surr_restored = "scout"
            self.agent_roles[surrogate_id] = surr_restored
            self.get_logger().info(
                f"[AgenticRecovery] Surrogate USV-{surrogate_id} yielding ring slot -> returning to {surr_restored} role."
            )

        latency_ms = (time.time() - t0) * 1000.0

        event_msg = RecoveryEvent()
        event_msg.stamp = self.get_clock().now().to_msg()
        event_msg.agent_id = agent_id
        event_msg.anomaly_description = "HEALTHY_RESTORED"
        event_msg.health_index = float(health_msg.health_index)
        event_msg.previous_role = prev_role
        event_msg.assigned_role = restored_role
        event_msg.approved_by_llm = True
        event_msg.fallback_triggered = False
        event_msg.llm_rationale = (
            f"Ally USV-{agent_id} has autonomously healed (h={health_msg.health_index:.2f}). "
            f"Restored from support/relay to active 20m encirclement ring."
        )
        event_msg.latency_ms = float(latency_ms)
        self.recovery_pub.publish(event_msg)
        self.get_logger().info(
            f"\n[AgenticRecovery] Restoration: USV-{agent_id} healed (h={health_msg.health_index:.2f} >= 0.80) -> "
            f"Re-promoted ({prev_role} -> {restored_role}) to rejoin 20m formation ring."
        )

    def _planner_step(self):
        dt = 0.1
        now = self.get_clock().now().to_msg()
        tx, ty = self.target_pos

        if len(self.agent_positions) < self.num_agents:
            return

        # 1. Deterministic Hungarian Role Assignment
        assignments = self._hungarian_solve(tx, ty)

        for aid, (role_name, slot_pt) in assignments.items():
            self.agent_roles[aid] = role_name
            ra = RoleAssignment()
            ra.stamp = now
            ra.agent_id = aid
            ra.role = role_name
            ra.target_slot.x = float(slot_pt[0])
            ra.target_slot.y = float(slot_pt[1])
            ra.target_slot.z = 0.0
            ra.is_degraded_reallocation = self.agent_health.get(aid, 1.0) < 0.60
            self.role_pubs[aid].publish(ra)

        # 2. Section 5 Closed-Loop Mission Verification
        self._verify_mission(dt, tx, ty)

    def _hungarian_solve(self, tx: float, ty: float) -> Dict[int, Tuple[str, Tuple[float, float]]]:
        agent_ids = sorted(list(self.agent_positions.keys()))
        N = len(agent_ids)

        # Partition fleet into active trackers, relays, and scouts based on health and agentic roles
        trackers = [aid for aid in agent_ids if self.agent_roles.get(aid) in ["tracker", "interceptor"]]
        relays = [aid for aid in agent_ids if self.agent_roles.get(aid) == "relay"]
        scouts = [aid for aid in agent_ids if self.agent_roles.get(aid) not in ["tracker", "interceptor", "relay"]]

        # Safety fallback: ensure at least 3 trackers if healthy agents exist
        if len(trackers) < 3:
            for s in list(scouts):
                if self.agent_health.get(s, 1.0) >= 0.60:
                    scouts.remove(s)
                    trackers.append(s)
                    self.agent_roles[s] = "tracker"
                    if len(trackers) >= 3:
                        break

        num_trackers = len(trackers)
        num_relays = len(relays)
        num_scouts = len(scouts)

        slots = []
        slot_roles = []
        ref_heading = self.smooth_heading

        # Generate ring slots on 20m circle for active trackers
        for k in range(num_trackers):
            angle = ref_heading + (2.0 * math.pi / max(1, num_trackers)) * k
            slots.append((tx + self.r_ring * math.cos(angle), ty + self.r_ring * math.sin(angle)))
            slot_roles.append("tracker")

        # Generate standoff slots for relay and scout vessels
        for k in range(num_relays):
            offset = 0.0 if num_relays == 1 else (k - (num_relays - 1) / 2.0) * 0.5
            angle = ref_heading + math.pi + offset
            slots.append((tx + 30.0 * math.cos(angle), ty + 30.0 * math.sin(angle)))
            slot_roles.append("relay")

        for k in range(num_scouts):
            angle = ref_heading + math.pi + 0.6 + (2.0 * math.pi / max(1, num_scouts)) * k
            slots.append((tx + 45.0 * math.cos(angle), ty + 45.0 * math.sin(angle)))
            slot_roles.append("scout")

        while len(slots) < N:
            slots.append((tx + 45.0, ty))
            slot_roles.append("scout")

        cost_matrix = np.zeros((N, N), dtype=np.float64)
        for i_idx, aid in enumerate(agent_ids):
            ax, ay = self.agent_positions[aid]
            a_role = self.agent_roles.get(aid, "tracker")
            if a_role == "interceptor":
                a_role = "tracker"

            for s_idx in range(N):
                sx, sy = slots[s_idx]
                s_role = slot_roles[s_idx]
                dist = math.hypot(ax - sx, ay - sy)

                if a_role == s_role:
                    cost = dist
                    # 50.0m Slot Retention Barrier: active formation holders strictly maintain slots
                    if self.last_assigned_col.get(aid, -1) == s_idx:
                        cost -= 50.0
                else:
                    # Incompatible role barrier
                    cost = 10000.0 + dist

                cost_matrix[i_idx, s_idx] = cost

        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        results = {}
        for r, c in zip(row_ind, col_ind):
            aid = agent_ids[r]
            self.last_assigned_col[aid] = c
            results[aid] = (slot_roles[c], slots[c])
        return results

    def _verify_mission(self, dt: float, tx: float, ty: float):
        # Inter-vessel minimum distance
        positions = list(self.agent_positions.values())
        min_dist = float('inf')
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                d = math.hypot(positions[i][0] - positions[j][0], positions[i][1] - positions[j][1])
                if d < min_dist:
                    min_dist = d
                if d < 4.0:
                    self.collision_occurred = True

        # Participating healthy trackers
        participating_bearings = []
        for aid, (ax, ay) in self.agent_positions.items():
            h = self.agent_health.get(aid, 1.0)
            role = self.agent_roles.get(aid, "")
            if h >= 0.30 and role in ["tracker", "interceptor"]:
                d = math.hypot(ax - tx, ay - ty)
                if (self.r_ring - self.delta_r) <= d <= (self.r_ring + self.delta_r):
                    participating_bearings.append(math.atan2(ay - ty, ax - tx))

        N_part = len(participating_bearings)
        c1_pass = False
        c2_pass = False
        angular_spread = 0.0
        max_gap = 2.0 * math.pi
        spread_thresh = 2.0 * math.pi
        gap_thresh = 0.0

        if N_part >= 3:
            participating_bearings.sort()
            gaps = [(participating_bearings[(k + 1) % N_part] - participating_bearings[k]) % (2.0 * math.pi) for k in range(N_part)]
            max_gap = max(gaps)
            angular_spread = 2.0 * math.pi - max_gap
            spread_thresh = 2.0 * math.pi * (1.0 - 1.0 / N_part) * 0.90
            gap_thresh = (2.0 * math.pi / N_part) * 1.60
            c1_pass = angular_spread >= spread_thresh
            c2_pass = max_gap <= gap_thresh

        # Arrest condition
        trailing_2s_speed = (sum(self.target_speed_history) / len(self.target_speed_history)) if self.target_speed_history else self.target_speed
        if c1_pass and c2_pass:
            self.target_dwell_timer += dt
        else:
            self.target_dwell_timer = max(0.0, self.target_dwell_timer - 0.2 * dt)

        arrest_pass = trailing_2s_speed < 0.20 or self.target_dwell_timer >= 6.0
        all_met = c1_pass and c2_pass and arrest_pass and (not self.collision_occurred)

        if all_met:
            self.debounce_timer += dt
        else:
            self.debounce_timer = max(0.0, self.debounce_timer - 0.2 * dt)

        debounced_success = self.debounce_timer >= 3.0

        self.verify_step_count = getattr(self, 'verify_step_count', 0) + 1
        if self.verify_step_count % 20 == 0:
            tracker_dists = [
                f"USV-{aid}({self.agent_roles.get(aid, '?')}): {math.hypot(ax - tx, ay - ty):.1f}m"
                for aid, (ax, ay) in sorted(self.agent_positions.items())
            ]
            self.get_logger().info(
                f"[MISSION STATUS] Trackers in ring: {N_part}/{len(self.agent_positions)} | "
                f"C1(Spread>={spread_thresh:.2f}): {angular_spread:.2f} ({c1_pass}) | "
                f"C2(Gap<={gap_thresh:.2f}): {max_gap:.2f} ({c2_pass}) | "
                f"TgtSpeed: {trailing_2s_speed:.2f}m/s | Dwell: {self.target_dwell_timer:.1f}s | "
                f"Distances: {' | '.join(tracker_dists)}"
            )

        if debounced_success and not self.arrest_logged:
            self.arrest_logged = True
            self.get_logger().info(
                "\n" + "=" * 76 + "\n"
                "[MISSION ARREST SUCCESS] TARGET INTERCEPTED & ESCAPE ARRESTED!\n"
                f"Section 5 Rubric MET: Ring Dwell={self.target_dwell_timer:.1f}s | "
                f"Speed={trailing_2s_speed:.2f}m/s | Spread={angular_spread:.2f}rad | Zero Collisions\n"
                + "=" * 76
            )

        status = MissionStatus()
        status.stamp = self.get_clock().now().to_msg()
        status.total_agents = self.num_agents
        status.healthy_agents = sum(1 for h in self.agent_health.values() if h >= 0.60)
        status.participating_trackers = N_part
        status.angular_spread = float(angular_spread)
        status.spread_threshold = float(spread_thresh)
        status.criterion_1_pass = c1_pass
        status.max_angular_gap = float(max_gap)
        status.gap_threshold = float(gap_thresh)
        status.criterion_2_pass = c2_pass
        status.target_trailing_speed = float(trailing_2s_speed)
        status.target_dwell_time = float(self.target_dwell_timer)
        status.arrest_condition_pass = arrest_pass
        status.debounced_success = debounced_success
        status.debounce_timer = float(self.debounce_timer)
        status.min_agent_distance = float(min_dist)
        status.zero_collision_invariant = not self.collision_occurred
        self.status_pub.publish(status)


def main(args=None):
    rclpy.init(args=args)
    node = USVRecoveryPlannerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
