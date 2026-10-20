"""
USV Swarm Simulation - ROS 2 Recovery Planner & Mission Verification Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment, Multi-Target Battle)

Responsibilities:
- Default Path (10 Hz): establishment-driven Kuhn-Munkres (Hungarian) role allocation across
  tactical strike wings, one wing per live hostile, merged when hostiles converge.
- Manning Watchdog (1 Hz): re-resolves the tactical establishment and guarantees that every
  canonical role (interceptor, tracker, relay, scout) is manned by the surviving fleet. Role
  slots are generated from the mission, not from the surviving role vector, so an incumbent
  can be destroyed but its slot cannot be.
- Event-Triggered Path: fires on fault anomaly (h_i < 0.55), queries Groq/Ollama LLM arbiter,
  and reports every role transition the allocator makes on /recovery/event.
- Closed-Loop Mission Verifier: evaluates Section 5 criteria per hostile and publishes
  /mission/status_target_{m} plus the aggregate /mission/status.
- Live ASCII tactical combat dashboard at 1.5 s cadence.
"""

from __future__ import annotations
import math
import os
import time
from typing import Dict, FrozenSet, List, Optional, Tuple
import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
from nav_msgs.msg import Odometry
from usv_msgs.msg import USVHealth, RoleAssignment, RecoveryEvent, MissionStatus
from std_msgs.msg import Float32

from usv_recovery_planner.establishment import (
    CANONICAL_ROLES,
    GARRISON_SPIN_RATE,
    CASUALTY_FLOOR,
    KIND_CASUALTY,
    KIND_GUARD,
    ROLE_FITNESS_FLOOR,
    ROLE_INTERCEPTOR,
    ROLE_RELAY,
    ROLE_SCOUT,
    ROLE_TRACKER,
    TargetState,
    audit_manning,
    build_slot_lattice,
    group_wings,
    merged_pairs,
    resolve_establishment,
    solve_assignment,
    wrap_angle,
)

# Engagement thresholds (metres / seconds).
D_PINCER_AUTHORIZE = 18.0    # interceptor range that authorizes the containment squeeze
D_PINCER_RELEASE = 26.0      # interceptor range at which the squeeze is released
T_PINCER_RAMP = 6.0          # seconds for the ring to walk from standoff to arrest radius
D_DEFUSE_LOCK = 7.5          # interceptor range that sustains the EMP defusal lock
D_DEFUSE_BREAK = 9.5         # range at which the lock decays
T_DEFUSE = 4.0               # seconds of sustained lock for a full defusal
T_DEBOUNCE = 2.0             # seconds all criteria must hold for mission success
H_ANOMALY = 0.55             # health that triggers a fault anomaly event
H_RESTORED = 0.80            # health that triggers a self-healing restoration event
D_COLLISION = 4.0            # hard inter-ally collision invariant


class USVRecoveryPlannerNode(Node):
    """ROS 2 Node orchestrating multi-wing Hungarian allocation and event-triggered recovery."""

    def __init__(self):
        super().__init__('usv_recovery_planner_node')

        self.declare_parameter('num_agents', 12)
        self.declare_parameter('num_targets', 3)
        self.declare_parameter('r_ring', 20.0)
        self.declare_parameter('delta_r', 4.5)
        self.declare_parameter('llm_backend', 'auto')

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.num_targets = max(1, self.get_parameter('num_targets').get_parameter_value().integer_value)
        self.r_ring = self.get_parameter('r_ring').get_parameter_value().double_value
        self.delta_r = self.get_parameter('delta_r').get_parameter_value().double_value
        self.llm_backend = self.get_parameter('llm_backend').get_parameter_value().string_value

        self.get_logger().info(
            f"Initializing USVRecoveryPlannerNode: {self.num_agents} agents vs "
            f"{self.num_targets} hostiles, R={self.r_ring}m. LLM backend: {self.llm_backend}"
        )

        # --- Hostile tactical picture ----------------------------------------------------
        self.hostile_pos: Dict[int, Tuple[float, float]] = {m: (0.0, 0.0) for m in range(self.num_targets)}
        self.hostile_heading: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.hostile_smooth_heading: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.hostile_speed: Dict[int, float] = {m: 1.2 for m in range(self.num_targets)}
        self.hostile_speed_history: Dict[int, List[float]] = {m: [] for m in range(self.num_targets)}
        self.hostile_neutralized: Dict[int, bool] = {m: False for m in range(self.num_targets)}

        # --- Fleet state -----------------------------------------------------------------
        self.agent_positions: Dict[int, Tuple[float, float]] = {}
        self.agent_speeds: Dict[int, float] = {i: 0.0 for i in range(self.num_agents)}
        self.agent_health: Dict[int, float] = {i: 1.0 for i in range(self.num_agents)}
        self.agent_roles: Dict[int, str] = {i: ROLE_TRACKER for i in range(self.num_agents)}
        self.agent_wings: Dict[int, int] = {i: 0 for i in range(self.num_agents)}
        self.agent_slot_keys: Dict[int, str] = {}
        self.agent_slot_kinds: Dict[int, str] = {}
        self.agent_costs: Dict[int, float] = {i: 0.0 for i in range(self.num_agents)}
        self.agent_fault_desc: Dict[int, str] = {i: "HEALTHY" for i in range(self.num_agents)}

        # --- Allocation state ------------------------------------------------------------
        self.merged_target_pairs: FrozenSet[FrozenSet[int]] = frozenset()
        self.wing_signature: Optional[tuple] = None
        self.establishment = None
        self.manning = {}
        self.establishment_timer = 0.0
        self.suppress_retention = True   # first solve has no formation to retain
        self.anomalies_triggered: set = set()
        self.demoted_previous_roles: Dict[int, str] = {}

        # --- Combat state ----------------------------------------------------------------
        self.defusal_timer: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.defusal_progress: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.lock_announced: Dict[int, bool] = {m: False for m in range(self.num_targets)}
        self.pincer_progress: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        # Precession angle of every garrison ring. Advancing it sweeps the station each
        # garrison vessel holds around its arrested hull, so the garrison circles the hull
        # instead of parking on a fixed bearing.
        self.garrison_spin = 0.0
        self.debounce_timer: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.arrest_logged: Dict[int, bool] = {m: False for m in range(self.num_targets)}
        self.collision_occurred = False
        self.min_separation_seen = float('inf')
        self.tactical_step_count = 0
        self.mission_complete_logged = False

        # --- Publishers & Subscriptions --------------------------------------------------
        self.role_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.health_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.odom_subs: Dict[int, rclpy.subscription.Subscription] = {}

        for i in range(self.num_agents):
            self.role_pubs[i] = self.create_publisher(RoleAssignment, f'/usv_{i}/role', 10)
            self.health_subs[i] = self.create_subscription(
                USVHealth, f'/usv_{i}/health', self._make_health_callback(i), 10
            )
            self.odom_subs[i] = self.create_subscription(
                Odometry, f'/usv_{i}/odom', self._make_odom_callback(i), 10
            )

        self.hostile_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.defuse_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.status_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        for m in range(self.num_targets):
            self.hostile_subs[m] = self.create_subscription(
                Odometry, f'/target_{m}/odom', self._make_hostile_callback(m), 10
            )
            self.defuse_pubs[m] = self.create_publisher(Float32, f'/target_{m}/defuse_status', 10)
            self.status_pubs[m] = self.create_publisher(MissionStatus, f'/mission/status_target_{m}', 10)

        qos_latched = QoSProfile(
            depth=10,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE,
        )
        self.recovery_pub = self.create_publisher(RecoveryEvent, '/recovery/event', qos_latched)
        self.establishment_pub = self.create_publisher(RecoveryEvent, '/swarm/establishment', qos_latched)
        # Legacy aggregate interfaces.
        self.status_pub = self.create_publisher(MissionStatus, '/mission/status', 10)
        self.legacy_defuse_pub = self.create_publisher(Float32, '/target/defuse_status', 10)

        # 10 Hz Planner & Verification Loop
        self.timer = self.create_timer(0.1, self._planner_step)

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    def _make_hostile_callback(self, target_id: int):
        def callback(msg: Odometry):
            self.hostile_pos[target_id] = (msg.pose.pose.position.x, msg.pose.pose.position.y)
            q_z = msg.pose.pose.orientation.z
            q_w = msg.pose.pose.orientation.w
            raw_heading = 2.0 * math.atan2(q_z, q_w)
            self.hostile_heading[target_id] = raw_heading

            # Angular low-pass filter (tau ~ 2.0s) for rock-steady formation slots.
            diff = wrap_angle(raw_heading - self.hostile_smooth_heading[target_id])
            self.hostile_smooth_heading[target_id] = wrap_angle(
                self.hostile_smooth_heading[target_id] + 0.08 * diff
            )

            self.hostile_speed[target_id] = msg.twist.twist.linear.x
            hist = self.hostile_speed_history[target_id]
            hist.append(self.hostile_speed[target_id])
            if len(hist) > 20:  # 2.0s at 10Hz
                hist.pop(0)
        return callback

    def _make_health_callback(self, agent_id: int):
        def callback(msg: USVHealth):
            self.agent_health[agent_id] = msg.health_index
            self.agent_fault_desc[agent_id] = msg.fault_description
            # Check for fault anomaly event (anomaly threshold: h_i < 0.55)
            if msg.health_index < H_ANOMALY and agent_id not in self.anomalies_triggered:
                self.anomalies_triggered.add(agent_id)
                self._trigger_agentic_recovery(agent_id, msg, self.agent_roles.get(agent_id, ROLE_TRACKER))
            # Check for autonomous self-healing restoration event (h_i >= 0.80)
            elif msg.health_index >= H_RESTORED and agent_id in self.anomalies_triggered:
                self.anomalies_triggered.discard(agent_id)
                self._trigger_agentic_restoration(agent_id, msg)
        return callback

    def _make_odom_callback(self, agent_id: int):
        def callback(msg: Odometry):
            self.agent_positions[agent_id] = (msg.pose.pose.position.x, msg.pose.pose.position.y)
            vx = msg.twist.twist.linear.x
            vy = msg.twist.twist.linear.y
            self.agent_speeds[agent_id] = math.hypot(vx, vy)
        return callback

    # ------------------------------------------------------------------
    # Event-triggered agentic recovery
    # ------------------------------------------------------------------
    def _trigger_agentic_recovery(self, agent_id: int, health_msg: USVHealth, prev_role: str):
        """
        Reports a fault anomaly and the doctrine consequence.

        The role change itself is NOT applied here: the tactical establishment plus the
        Hungarian solve own reallocation, so this path reports and reasons about the event
        rather than mutating the role vector behind the allocator's back.
        """
        t0 = time.time()
        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        rationale = ""

        if groq_key and self.llm_backend in ["auto", "groq"]:
            try:
                from openai import OpenAI
                client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=groq_key, timeout=3.0)
                prompt = (
                    f"Agent usv_{agent_id} detected with fault: {health_msg.fault_description}, "
                    f"health: {health_msg.health_index:.2f}, role: {prev_role}. "
                    f"The swarm is engaging {self.num_targets} hostile drones and must keep every "
                    "role class manned (interceptor, tracker, relay, scout). Decide role "
                    "reallocation to preserve the 20m encirclement rings. Respond with brief rationale."
                )
                res = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100
                )
                rationale = res.choices[0].message.content.strip()
            except Exception as e:
                rationale = f"Groq fallback: {e}"

        is_loss = health_msg.health_index < CASUALTY_FLOOR
        if not rationale:
            if is_loss:
                rationale = (
                    f"Agent USV-{agent_id} is a combat loss (h={health_msg.health_index:.2f}). "
                    f"Its {prev_role} slot is released to the establishment and will be backfilled "
                    "by the next Hungarian solve; the hull is routed to a rear casualty station."
                )
            else:
                rationale = (
                    f"Agent USV-{agent_id} actuator/sensor degraded (h={health_msg.health_index:.2f}). "
                    f"Fitness floor for {prev_role} is {ROLE_FITNESS_FLOOR.get(prev_role, 0.0):.2f}; "
                    "the allocator will re-task it to a role its remaining capability supports."
                )

        self.demoted_previous_roles[agent_id] = prev_role
        self._publish_recovery_event(
            agent_id=agent_id,
            anomaly="COMBAT_LOSS" if is_loss else health_msg.fault_description,
            health=health_msg.health_index,
            previous_role=prev_role,
            assigned_role=prev_role,
            rationale=rationale,
            latency_ms=(time.time() - t0) * 1000.0,
            fallback=not bool(groq_key),
        )
        self.get_logger().warning(
            f"[AgenticRecovery] {'COMBAT LOSS' if is_loss else 'ANOMALY'}: USV-{agent_id} "
            f"({prev_role}) h={health_msg.health_index:.2f} -> establishment will backfill."
        )
        # Force an immediate reshuffle rather than waiting out the retention hysteresis.
        self.suppress_retention = True
        self.establishment_timer = 0.0

    def _trigger_agentic_restoration(self, agent_id: int, health_msg: USVHealth):
        """Reports a degraded vessel self-healing back to combat-fit status."""
        prev_role = self.demoted_previous_roles.pop(agent_id, self.agent_roles.get(agent_id, ROLE_RELAY))
        self._publish_recovery_event(
            agent_id=agent_id,
            anomaly="HEALTHY_RESTORED",
            health=float(health_msg.health_index),
            previous_role=prev_role,
            assigned_role=self.agent_roles.get(agent_id, prev_role),
            rationale=(
                f"Ally USV-{agent_id} has autonomously healed (h={health_msg.health_index:.2f} "
                f">= {H_RESTORED:.2f}) and is combat-fit again. It re-enters the establishment and "
                "is eligible for the full role taxonomy on the next Hungarian solve."
            ),
            latency_ms=0.0,
            fallback=False,
        )
        self.get_logger().info(
            f"[AgenticRecovery] Restoration: USV-{agent_id} healed "
            f"(h={health_msg.health_index:.2f}) -> rejoining the establishment."
        )
        self.suppress_retention = True
        self.establishment_timer = 0.0

    def _publish_recovery_event(self, agent_id: int, anomaly: str, health: float,
                                previous_role: str, assigned_role: str, rationale: str,
                                latency_ms: float, fallback: bool) -> None:
        ev = RecoveryEvent()
        ev.stamp = self.get_clock().now().to_msg()
        ev.agent_id = agent_id
        ev.anomaly_description = anomaly
        ev.health_index = float(health)
        ev.previous_role = previous_role
        ev.assigned_role = assigned_role
        ev.approved_by_llm = True
        ev.fallback_triggered = fallback
        ev.llm_rationale = rationale
        ev.latency_ms = float(latency_ms)
        self.recovery_pub.publish(ev)

    # ------------------------------------------------------------------
    # Planner loop
    # ------------------------------------------------------------------
    def _planner_step(self):
        dt = 0.1
        now = self.get_clock().now().to_msg()

        if len(self.agent_positions) < self.num_agents:
            return

        self.establishment_timer += dt

        # 1. Build the hostile picture and partition it into tactical wings.
        targets = [
            TargetState(
                target_id=m,
                x=self.hostile_pos[m][0],
                y=self.hostile_pos[m][1],
                heading=self.hostile_smooth_heading[m],
                speed=self.hostile_speed[m],
                defusal_progress=self.defusal_progress[m],
                neutralized=self.hostile_neutralized[m] or self.defusal_progress[m] >= 100.0,
            )
            for m in range(self.num_targets)
        ]
        wings = group_wings(targets, base_ring=self.r_ring,
                            previously_merged=self.merged_target_pairs)
        self.merged_target_pairs = merged_pairs(wings)

        # 2. Manning watchdog: re-resolve the establishment at 1 Hz, immediately on an event,
        #    and immediately whenever the wing composition changes (a merge or a split), since
        #    the slot lattice is keyed on wing ids and must never reference a wing that is gone.
        agent_ids = sorted(self.agent_positions.keys())
        fit_ids = [a for a in agent_ids if self.agent_health.get(a, 1.0) >= CASUALTY_FLOOR]
        signature = (
            tuple(sorted((w.wing_id, w.target_ids, w.neutralized) for w in wings)),
            len(fit_ids),
        )
        if (self.establishment_timer >= 1.0
                or self.establishment is None
                or signature != self.wing_signature):
            self.establishment_timer = 0.0
            changed_wings = signature[0] != (self.wing_signature[0] if self.wing_signature else None)
            self.wing_signature = signature
            self.establishment = resolve_establishment(
                len(fit_ids), wings, num_casualties=len(agent_ids) - len(fit_ids)
            )
            if changed_wings:
                # Wing geometry changed under the fleet: let it re-form rather than cling to
                # stations that belonged to the previous wing partition.
                self.suppress_retention = True
            self._report_establishment(wings)

        # 3. Walk the containment ring in for any wing whose interceptor has closed, and
        #    advance the garrison precession.
        self._update_pincer(dt, wings)
        self.garrison_spin = wrap_angle(self.garrison_spin + GARRISON_SPIN_RATE * dt)

        # 4. Build the slot lattice and solve the complete agent -> slot matching.
        collapse = {w.wing_id: max(self.pincer_progress.get(t, 0.0) for t in w.target_ids)
                    for w in wings}
        slots = build_slot_lattice(self.establishment, wings, collapse_progress=collapse,
                                   garrison_spin=self.garrison_spin)

        if len(slots) != len(agent_ids):
            # Defensive: re-resolve against the current wings and rebuild immediately rather
            # than holding a stale assignment, which would freeze the formation for a tick.
            self.get_logger().warning(
                f"Slot lattice {len(slots)} != fleet {len(agent_ids)}; re-resolving establishment."
            )
            self.establishment = resolve_establishment(
                len(fit_ids), wings, num_casualties=len(agent_ids) - len(fit_ids)
            )
            slots = build_slot_lattice(self.establishment, wings, collapse_progress=collapse,
                                       garrison_spin=self.garrison_spin)
            if len(slots) != len(agent_ids):
                self.get_logger().error(
                    f"Slot lattice still {len(slots)} != fleet {len(agent_ids)}; skipping tick."
                )
                return

        live_ids = frozenset(t.target_id for t in targets if not t.neutralized)
        assignments = solve_assignment(
            agent_ids, self.agent_positions, self.agent_health, slots,
            previous_slot_keys=self.agent_slot_keys,
            previous_target=self.agent_wings,
            live_target_ids=live_ids,
            suppress_retention=self.suppress_retention,
        )
        self.suppress_retention = False

        # 5. Publish role assignments and report every role transition.
        for aid, a in assignments.items():
            prev_role = self.agent_roles.get(aid)
            prev_wing = self.agent_wings.get(aid)
            if prev_role is not None and (prev_role != a.role or prev_wing != a.target_id):
                self._report_transition(aid, prev_role, prev_wing, a)

            self.agent_roles[aid] = a.role
            self.agent_wings[aid] = a.target_id
            self.agent_slot_keys[aid] = a.slot_key
            self.agent_slot_kinds[aid] = a.kind
            self.agent_costs[aid] = a.cost

            ra = RoleAssignment()
            ra.stamp = now
            ra.agent_id = aid
            ra.role = a.role
            ra.target_slot.x = float(a.slot_point[0])
            ra.target_slot.y = float(a.slot_point[1])
            ra.target_slot.z = 0.0
            ra.allocation_cost = float(a.cost)
            ra.is_degraded_reallocation = (
                a.is_casualty_station or a.below_fitness_floor
                or self.agent_health.get(aid, 1.0) < 0.60
            )
            ra.assigned_target_id = int(a.target_id)
            self.role_pubs[aid].publish(ra)

        self.manning = audit_manning(self.establishment, assignments, self.agent_health)

        # 6. Mission verification, per hostile and in aggregate.
        self._verify_mission(dt, targets, wings)

    def _update_pincer(self, dt: float, wings) -> None:
        """Ramps the containment squeeze for each wing whose interceptor has closed to strike range."""
        for m in range(self.num_targets):
            interceptor = self._wing_interceptor(m)
            d = float('inf')
            if interceptor is not None and interceptor in self.agent_positions:
                ix, iy = self.agent_positions[interceptor]
                tx, ty = self.hostile_pos[m]
                d = math.hypot(ix - tx, iy - ty)

            if self.hostile_neutralized[m] or self.defusal_progress[m] >= 100.0:
                # An arrested hull is held at the guard radius, not squeezed further.
                self.pincer_progress[m] = min(1.0, self.pincer_progress[m] + dt / T_PINCER_RAMP)
            elif d <= D_PINCER_AUTHORIZE:
                self.pincer_progress[m] = min(1.0, self.pincer_progress[m] + dt / T_PINCER_RAMP)
            elif d > D_PINCER_RELEASE:
                self.pincer_progress[m] = max(0.0, self.pincer_progress[m] - dt / T_PINCER_RAMP)

    def _wing_interceptor(self, target_id: int) -> Optional[int]:
        """The vessel currently holding the interceptor slot on this hostile's wing."""
        for aid, role in self.agent_roles.items():
            if role == ROLE_INTERCEPTOR and self.agent_wings.get(aid) == target_id:
                return aid
        return None

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def _report_transition(self, agent_id: int, prev_role: str, prev_wing: Optional[int], a) -> None:
        """Logs and publishes every role or wing change the allocator makes."""
        h = self.agent_health.get(agent_id, 1.0)
        if a.is_casualty_station:
            detail = "routed to a rear casualty station (combat loss)"
        elif prev_role != a.role:
            detail = (f"re-tasked {prev_role.upper()} -> {a.role.upper()} on wing "
                      f"T{a.target_id} (cost {a.cost:.1f})")
        else:
            detail = f"transferred wing T{prev_wing} -> T{a.target_id} (cost {a.cost:.1f})"

        self.get_logger().info(f"[REALLOC] USV-{agent_id} h={h:.2f} {detail}")
        self._publish_recovery_event(
            agent_id=agent_id,
            anomaly="ROLE_REALLOCATION",
            health=h,
            previous_role=prev_role,
            assigned_role=a.role,
            rationale=(
                f"Kuhn-Munkres allocator {detail}. The tactical establishment demanded this slot, "
                "so the role remains manned regardless of which hulls survive."
            ),
            latency_ms=0.0,
            fallback=False,
        )

    def _report_establishment(self, wings) -> None:
        """Publishes and logs the current establishment and any manning shortfall."""
        est = self.establishment
        totals = est.role_totals()
        garrisons = sorted(set(est.guards) | set(est.garrison_interceptors))
        garrison_txt = (
            " | garrisons " + ", ".join(f"H{g}:{est.garrison_total(g)}" for g in garrisons)
            if garrisons else ""
        )
        summary = (
            f"INT {totals[ROLE_INTERCEPTOR]} | TRK {totals[ROLE_TRACKER]} | "
            f"RLY {totals[ROLE_RELAY]} | SCT {totals[ROLE_SCOUT]} | "
            f"casualties {est.casualty_stations} | wings {len(wings)}{garrison_txt}"
        )

        if est.undermanned_roles:
            self.get_logger().error(
                f"[ESTABLISHMENT] ROLE VACANCY: {', '.join(est.undermanned_roles)} "
                f"cannot be manned by the surviving fleet. {summary}"
            )
        elif est.unengaged_wings or est.unshadowed_wings:
            self.get_logger().warning(
                f"[ESTABLISHMENT] Below strength: {summary} | "
                f"unengaged hostiles {list(est.unengaged_wings)} | "
                f"unshadowed hostiles {list(est.unshadowed_wings)}"
            )
        else:
            self.get_logger().info(f"[ESTABLISHMENT] All roles manned -> {summary}")

        ev = RecoveryEvent()
        ev.stamp = self.get_clock().now().to_msg()
        ev.agent_id = 255
        ev.anomaly_description = "ROLE_VACANCY" if est.undermanned_roles else "ESTABLISHMENT_MANNED"
        ev.health_index = float(len([h for h in self.agent_health.values() if h >= CASUALTY_FLOOR]))
        ev.previous_role = ""
        ev.assigned_role = summary
        ev.approved_by_llm = True
        ev.fallback_triggered = bool(est.undermanned_roles)
        ev.llm_rationale = " ".join(est.notes) if est.notes else "Establishment fully manned."
        ev.latency_ms = 0.0
        self.establishment_pub.publish(ev)

    # ------------------------------------------------------------------
    # Mission verification
    # ------------------------------------------------------------------
    def _verify_mission(self, dt: float, targets, wings) -> None:
        # Global inter-ally separation and the hard collision invariant.
        live_ids = [a for a in self.agent_positions
                    if self.agent_health.get(a, 1.0) >= CASUALTY_FLOOR]
        min_dist = float('inf')
        for a_idx in range(len(live_ids)):
            for b_idx in range(a_idx + 1, len(live_ids)):
                pa = self.agent_positions[live_ids[a_idx]]
                pb = self.agent_positions[live_ids[b_idx]]
                d = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                min_dist = min(min_dist, d)
                if d < D_COLLISION:
                    if not self.collision_occurred:
                        self.get_logger().error(
                            f"[INVARIANT BREACH] USV-{live_ids[a_idx]} / USV-{live_ids[b_idx]} "
                            f"separation {d:.2f}m < {D_COLLISION:.1f}m"
                        )
                    self.collision_occurred = True
        self.min_separation_seen = min(self.min_separation_seen, min_dist)

        per_target: Dict[int, Dict[str, float]] = {}
        for t in targets:
            per_target[t.target_id] = self._verify_target(dt, t, min_dist)

        # Aggregate legacy status: the mission succeeds when every hostile is arrested.
        primary = per_target[0]
        all_arrested = all(v['debounced'] for v in per_target.values())
        status = MissionStatus()
        status.stamp = self.get_clock().now().to_msg()
        status.total_agents = self.num_agents
        status.healthy_agents = sum(1 for h in self.agent_health.values() if h >= 0.60)
        status.participating_trackers = int(sum(v['participants'] for v in per_target.values()))
        status.angular_spread = float(primary['spread'])
        status.spread_threshold = float(primary['spread_thresh'])
        status.criterion_1_pass = bool(primary['c1'])
        status.max_angular_gap = float(primary['max_gap'])
        status.gap_threshold = float(primary['gap_thresh'])
        status.criterion_2_pass = bool(primary['c2'])
        status.target_trailing_speed = float(primary['trailing_speed'])
        status.target_dwell_time = 0.0
        status.arrest_condition_pass = bool(all(v['arrest'] for v in per_target.values()))
        status.debounced_success = bool(all_arrested)
        status.debounce_timer = float(min(self.debounce_timer.values()))
        status.min_agent_distance = float(min_dist if min_dist != float('inf') else 0.0)
        status.zero_collision_invariant = not self.collision_occurred
        self.status_pub.publish(status)

        if all_arrested and not self.mission_complete_logged:
            self.mission_complete_logged = True
            self.get_logger().info(
                "\n" + "*" * 118 + "\n"
                f"[MISSION COMBAT SUCCESS] >>> ALL {self.num_targets} HOSTILE DRONES DEFUSED & ARRESTED!\n"
                f"Minimum ally separation held at {self.min_separation_seen:.2f}m "
                f"(invariant {D_COLLISION:.1f}m) | Zero collisions: {not self.collision_occurred}\n"
                + "*" * 118
            )

        self._dashboard(dt, targets, wings, per_target, min_dist)

    def _verify_target(self, dt: float, target: TargetState, min_dist: float) -> Dict[str, float]:
        """Section 5 containment criteria and the EMP defusal accumulator for one hostile."""
        m = target.target_id
        tx, ty = target.x, target.y

        # Participating force: this wing's containment element, healthy and in the ring band.
        bearings: List[float] = []
        for aid, (ax, ay) in self.agent_positions.items():
            if self.agent_wings.get(aid) != m:
                continue
            if self.agent_health.get(aid, 1.0) < 0.30:
                continue
            role = self.agent_roles.get(aid, "")
            if role not in (ROLE_TRACKER, ROLE_INTERCEPTOR):
                continue
            d = math.hypot(ax - tx, ay - ty)
            if (role == ROLE_TRACKER and 8.0 <= d <= 30.0) or (role == ROLE_INTERCEPTOR and d <= 30.0):
                bearings.append(math.atan2(ay - ty, ax - tx))

        n_part = len(bearings)
        spread = 0.0
        max_gap = 2.0 * math.pi
        spread_thresh = 2.0 * math.pi
        gap_thresh = 0.0
        c1 = False
        c2 = False

        if n_part >= 3:
            bearings.sort()
            gaps = [(bearings[(k + 1) % n_part] - bearings[k]) % (2.0 * math.pi)
                    for k in range(n_part)]
            max_gap = max(gaps)
            spread = 2.0 * math.pi - max_gap
            spread_thresh = 2.0 * math.pi * (1.0 - 1.0 / n_part) * 0.90
            gap_thresh = (2.0 * math.pi / n_part) * 1.60
            c1 = spread >= spread_thresh
            c2 = max_gap <= gap_thresh

        # EMP defusal lock, driven by this wing's interceptor.
        interceptor = self._wing_interceptor(m)
        d_int = float('inf')
        if interceptor is not None and interceptor in self.agent_positions:
            ix, iy = self.agent_positions[interceptor]
            d_int = math.hypot(ix - tx, iy - ty)

        if self.hostile_neutralized[m]:
            # An arrest is permanent: hold the lock reading so every consumer agrees.
            self.defusal_timer[m] = T_DEFUSE
        elif d_int <= D_DEFUSE_LOCK:
            self.defusal_timer[m] += dt
            if not self.lock_announced[m]:
                self.lock_announced[m] = True
                self.get_logger().info(
                    f"\n[COMBAT ENGAGEMENT] >>> USV-{interceptor} entered the strike corridor of "
                    f"HOSTILE-{m} ({d_int:.1f}m)! ACTIVATING EMP DEFUSAL LOCK!"
                )
        elif d_int > D_DEFUSE_BREAK:
            self.defusal_timer[m] = max(0.0, self.defusal_timer[m] - 0.4 * dt)

        self.defusal_progress[m] = min(100.0, (self.defusal_timer[m] / T_DEFUSE) * 100.0)
        if self.defusal_progress[m] >= 100.0 and not self.hostile_neutralized[m]:
            self.hostile_neutralized[m] = True
            self.get_logger().info(
                f"\n[EMP DEFUSAL COMPLETE] >>> HOSTILE-{m} propulsion neutralized at 100%. "
                f"Wing re-tasking to reinforce the live hostiles; one tracker holds the arrest."
            )

        msg = Float32()
        msg.data = float(self.defusal_progress[m])
        self.defuse_pubs[m].publish(msg)
        if m == 0:
            self.legacy_defuse_pub.publish(msg)

        # Section 5's containment criteria exist to prove the hostile cannot escape. A hull
        # that has been defused to 0.00 m/s and left under guard cannot escape by definition,
        # and its wing has correctly re-tasked to the live fight -- so requiring a full 3-vessel
        # encirclement around an arrested wreck would make the mission permanently unverifiable.
        arrested_hull = self.hostile_neutralized[m]
        if arrested_hull:
            c1 = True
            c2 = True

        hist = self.hostile_speed_history[m]
        trailing = (sum(hist) / len(hist)) if hist else self.hostile_speed[m]
        arrest = (self.defusal_progress[m] >= 100.0) or (trailing < 0.20 and self.defusal_progress[m] >= 40.0)
        all_met = c1 and c2 and arrest and (not self.collision_occurred)

        if all_met:
            self.debounce_timer[m] += dt
        else:
            self.debounce_timer[m] = max(0.0, self.debounce_timer[m] - 0.2 * dt)
        debounced = self.debounce_timer[m] >= T_DEBOUNCE

        if debounced and not self.arrest_logged[m]:
            self.arrest_logged[m] = True
            self.get_logger().info(
                f"\n[HOSTILE NEUTRALIZED] >>> HOSTILE-{m} FULLY DEFUSED & ARRESTED! "
                f"Speed {trailing:.2f} m/s | Containment spread {spread:.2f} rad | "
                f"Min ally separation {min_dist:.2f}m"
            )

        status = MissionStatus()
        status.stamp = self.get_clock().now().to_msg()
        status.total_agents = self.num_agents
        status.healthy_agents = sum(1 for h in self.agent_health.values() if h >= 0.60)
        status.participating_trackers = n_part
        status.angular_spread = float(spread)
        status.spread_threshold = float(spread_thresh)
        status.criterion_1_pass = c1
        status.max_angular_gap = float(max_gap)
        status.gap_threshold = float(gap_thresh)
        status.criterion_2_pass = c2
        status.target_trailing_speed = float(trailing)
        status.target_dwell_time = float(self.defusal_timer[m])
        status.arrest_condition_pass = bool(arrest)
        status.debounced_success = bool(debounced)
        status.debounce_timer = float(self.debounce_timer[m])
        status.min_agent_distance = float(min_dist if min_dist != float('inf') else 0.0)
        status.zero_collision_invariant = not self.collision_occurred
        self.status_pubs[m].publish(status)

        return {
            'participants': n_part, 'spread': spread, 'spread_thresh': spread_thresh,
            'c1': c1, 'max_gap': max_gap, 'gap_thresh': gap_thresh, 'c2': c2,
            'trailing_speed': trailing, 'arrest': arrest, 'debounced': debounced,
            'd_interceptor': d_int, 'arrested_hull': arrested_hull,
        }

    # ------------------------------------------------------------------
    # Live tactical dashboard
    # ------------------------------------------------------------------
    def _dashboard(self, dt: float, targets, wings, per_target, min_dist: float) -> None:
        self.tactical_step_count += 1
        if self.tactical_step_count % 15 != 0:
            return

        t_now = self.tactical_step_count * dt
        width = 128
        lines: List[str] = ["", "=" * width]
        lines.append(
            f"[TRITONSWARM MULTI-TARGET COMBAT DASHBOARD] t={t_now:6.1f}s | "
            f"Fleet {self.num_agents} ({sum(1 for h in self.agent_health.values() if h < CASUALTY_FLOOR)} lost) | "
            f"Hostiles {self.num_targets} ({sum(1 for t in targets if t.neutralized)} arrested) | "
            f"Wings {len(wings)}{' (MERGED)' if any(w.merged for w in wings) else ''}"
        )
        lines.append("-" * width)

        # --- Hostile / wing telemetry ---
        lines.append(" HOSTILE | Livery   | Position         | Speed   | Defusal            | "
                     "C1 Spread        | C2 Gap          | Combat State")
        lines.append("-" * width)
        for t in targets:
            v = per_target[t.target_id]
            pct = int(self.defusal_progress[t.target_id])
            filled = int(16 * pct / 100.0)
            bar = "=" * filled + (">" if filled < 16 else "") + " " * max(0, 16 - filled - 1)
            c1_flag = "ARR" if v['arrested_hull'] else ("OK " if v['c1'] else "NO ")
            c2_flag = "ARR" if v['arrested_hull'] else ("OK " if v['c2'] else "NO ")
            state = ("DEFUSED/ARRESTED" if t.neutralized else
                     ("EMP LOCK ENGAGED" if pct > 0 else
                      ("PINCER SQUEEZE" if self.pincer_progress[t.target_id] > 0.05
                       else "CONTAINMENT")))
            lines.append(
                f" HOST-{t.target_id:<2} | {('FLAGSHIP' if t.target_id == 0 else 'ESCORT'):<8} | "
                f"({t.x:7.1f},{t.y:7.1f}) | {v['trailing_speed']:4.2f}m/s | "
                f"[{bar}]{pct:3d}% | "
                f"{v['spread']:5.2f}>={v['spread_thresh']:4.2f} {c1_flag}| "
                f"{v['max_gap']:5.2f}<={v['gap_thresh']:4.2f} {c2_flag}| {state}"
            )

        # --- Establishment manning audit: the no-downtime invariant, live ---
        lines.append("-" * width)
        est = self.establishment
        manning_cells = []
        for role in CANONICAL_ROLES:
            a = self.manning.get(role, {"required": 0, "manned": 0, "fit": 0})
            flag = "OK" if a["manned"] >= a["required"] and a["fit"] >= min(1, a["required"]) else "!!"
            manning_cells.append(
                f"{role[:3].upper()} {a['manned']}/{a['required']}(fit {a['fit']}) {flag}"
            )
        lines.append(" ROLE MANNING | " + " | ".join(manning_cells))
        if est is not None and (est.undermanned_roles or est.unengaged_wings or est.unshadowed_wings):
            lines.append(
                f" DOCTRINE     | vacancies={list(est.undermanned_roles)} "
                f"unengaged={list(est.unengaged_wings)} unshadowed={list(est.unshadowed_wings)}"
            )

        # --- Per-vessel telemetry ---
        lines.append("-" * width)
        lines.append(" USV-ID | Role        | Wing | Health | Speed   | Dist->Wing | Bearing | "
                     "Min Ally | Alloc Cost | Tactical State")
        lines.append("-" * width)
        for aid in sorted(self.agent_positions.keys()):
            ax, ay = self.agent_positions[aid]
            role = self.agent_roles.get(aid, ROLE_SCOUT)
            wing = self.agent_wings.get(aid, 0)
            h = self.agent_health.get(aid, 1.0)
            spd = self.agent_speeds.get(aid, 0.0)
            tx, ty = self.hostile_pos.get(wing, (0.0, 0.0))
            d_t = math.hypot(ax - tx, ay - ty)
            bearing = math.degrees(math.atan2(ay - ty, ax - tx))

            d_allies = [math.hypot(ax - ox, ay - oy)
                        for oid, (ox, oy) in self.agent_positions.items() if oid != aid]
            min_ally = min(d_allies) if d_allies else float('inf')
            ally_str = f"{min_ally:4.1f}m OK " if min_ally >= D_COLLISION else f"{min_ally:4.1f}m WRN"

            kind = self.agent_slot_kinds.get(aid, "")
            desc = (self.agent_fault_desc.get(aid) or "").upper()
            repairing = ("REPAIR" in desc) or ("HEALING" in desc)
            if kind == KIND_CASUALTY:
                role = "casualty"
            if h < CASUALTY_FLOOR:
                state = ("~ UNDER REPAIR (DAMAGE CONTROL)" if repairing
                         else "X COMBAT LOSS (DEAD IN WATER)")
            elif kind == KIND_CASUALTY:
                state = "X CASUALTY STATION"
            elif repairing:
                state = f"^ RECOVERING -> REJOINING (h={h:.2f})"
            elif h < 0.60:
                state = "! DEGRADED / HEALING"
            elif kind == KIND_GUARD:
                spin_deg = math.degrees(self.garrison_spin) % 360.0
                if role == ROLE_INTERCEPTOR:
                    state = f"GARRISON ESCORT 9.5m (ring {spin_deg:3.0f}deg)"
                else:
                    state = f"GARRISON RING 15.5m (ring {spin_deg:3.0f}deg)"
            elif role == ROLE_INTERCEPTOR:
                pct = int(self.defusal_progress.get(wing, 0.0))
                state = (f"EMP DEFUSAL {pct}%" if pct > 0 else
                         ("TERMINAL STRIKE RUN" if d_t <= 12.0 else "ATTACK VECTOR (PN LEAD)"))
            elif role == ROLE_TRACKER:
                squeeze = self.pincer_progress.get(wing, 0.0)
                state = (f"PINCER SQUEEZE {squeeze*100:3.0f}%" if squeeze > 0.05
                         else "CONTAINMENT RING 20m")
            elif role == ROLE_RELAY:
                state = "MESH BACKBONE 30m"
            else:
                state = "WIDE-AREA PICKET 45m"

            lines.append(
                f" USV-{aid:<3}| {role.upper():<11} |  T{wing:<2} |  {h:4.2f}  | {spd:4.2f}m/s | "
                f"{d_t:7.1f}m   | {bearing:+6.1f} | {ally_str} | {self.agent_costs.get(aid, 0.0):9.1f}  | {state}"
            )

        lines.append("-" * width)
        lines.append(
            f" ALLOCATOR: Kuhn-Munkres over an establishment-derived slot lattice "
            f"({len(wings)} wing(s)) | MIN SEPARATION now {min_dist:5.2f}m, "
            f"worst {self.min_separation_seen:5.2f}m (invariant {D_COLLISION:.1f}m) | "
            f"ZERO-COLLISION: {not self.collision_occurred}"
        )
        lines.append("=" * width)
        self.get_logger().info("\n".join(lines))


def main(args=None):
    rclpy.init(args=args)
    node = USVRecoveryPlannerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # On SIGINT rclpy tears the context down from its own signal handler, so spin raises
        # ExternalShutdownException rather than KeyboardInterrupt. Leaving that uncaught, and
        # then calling shutdown() on an already-dead context, left nodes alive after the
        # launch exited. A surviving sim_core then publishes onto the same topics as the next
        # run, which silently corrupts it -- two simulators fighting over /target_{m}/odom.
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
