"""
USV Swarm Simulation - High-Fidelity 3-DOF Multi-USV & Multi-Target Simulation Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment, Multi-Target Battle)

Standardized ROS 2 Interfaces:
- Subscribes: /usv_{i}/cmd_vel (geometry_msgs/msg/Twist)
- Subscribes: /usv_{i}/injected_fault (usv_msgs/msg/USVHealth)
- Subscribes: /usv_{i}/health (usv_msgs/msg/USVHealth)
- Subscribes: /usv_{i}/role (usv_msgs/msg/RoleAssignment)
- Subscribes: /target_{m}/defuse_status (std_msgs/msg/Float32)
- Subscribes: /target/defuse_status (std_msgs/msg/Float32)   [legacy alias -> target 0]
- Subscribes: /mission/status (usv_msgs/msg/MissionStatus)
- Publishes:  /usv_{i}/odom (nav_msgs/msg/Odometry)
- Publishes:  /target_{m}/odom (nav_msgs/msg/Odometry)
- Publishes:  /target/odom (nav_msgs/msg/Odometry)           [legacy alias <- target 0]
- Publishes:  /usv_swarm/markers (visualization_msgs/msg/MarkerArray)

Hostile behaviour lives in hostile_ai.py and the RViz presentation layer in visuals.py, so
this node owns only parameterisation, ROS plumbing, vessel dynamics and telemetry.
"""

from __future__ import annotations
import math
from collections import deque
from typing import Dict, List, Optional, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from visualization_msgs.msg import MarkerArray
from usv_msgs.msg import USVHealth, RoleAssignment, MissionStatus
from std_msgs.msg import Float32

from usv_sim_core.hostile_ai import (
    HostileTactic,
    spawn_hostile_formation,
    step_hostile,
)
from usv_sim_core.visuals import (
    AgentVisual,
    BattlefieldVisualizer,
    HostileVisual,
)

# Spawn doctrine presets.
SPAWN_LAUNCH_CORRIDOR = "launch_corridor"
SPAWN_PERIMETER_RING = "perimeter_ring"
SPAWN_DISPERSED_PATROL = "dispersed_patrol"

CASUALTY_HEALTH = 0.25


class USVSimNode(Node):
    """High-fidelity 3-DOF simulation and battle visualization node for the USV swarm."""

    def __init__(self):
        super().__init__("usv_sim_node")

        # Declare parameters
        self.declare_parameter("num_agents", 12)
        self.declare_parameter("num_targets", 3)
        self.declare_parameter("target_mode", "reactive")
        self.declare_parameter("hostile_tactic", "coordinated")
        self.declare_parameter("spawn_mode", SPAWN_LAUNCH_CORRIDOR)
        self.declare_parameter("launch_bearing", 2.60)     # rad, ally launch corridor bearing
        self.declare_parameter("launch_standoff", 85.0)    # m from the engagement centre
        self.declare_parameter("hostile_heading", 0.0)     # rad, hostile ingress heading
        self.declare_parameter("dt", 0.05)                 # 20 Hz simulation loop
        self.declare_parameter("drift_x", 0.0)
        self.declare_parameter("drift_y", 0.0)
        self.declare_parameter("comm_range", 55.0)
        # Which control stack is driving this run. Both the full architecture and the ablated
        # baseline publish the same marker topic, so without this the two are visually
        # indistinguishable in RViz and it is impossible to tell which one you are watching.
        self.declare_parameter("stack_label", "FULL TRITONSWARM ARCHITECTURE")

        self.num_agents = self.get_parameter("num_agents").get_parameter_value().integer_value
        self.num_targets = max(1, self.get_parameter("num_targets").get_parameter_value().integer_value)
        self.target_mode = self.get_parameter("target_mode").get_parameter_value().string_value
        self.spawn_mode = self.get_parameter("spawn_mode").get_parameter_value().string_value
        self.launch_bearing = self.get_parameter("launch_bearing").get_parameter_value().double_value
        self.launch_standoff = self.get_parameter("launch_standoff").get_parameter_value().double_value
        self.hostile_heading = self.get_parameter("hostile_heading").get_parameter_value().double_value
        self.dt = self.get_parameter("dt").get_parameter_value().double_value
        self.drift_x = self.get_parameter("drift_x").get_parameter_value().double_value
        self.drift_y = self.get_parameter("drift_y").get_parameter_value().double_value
        self.comm_range = self.get_parameter("comm_range").get_parameter_value().double_value
        self.stack_label = self.get_parameter("stack_label").get_parameter_value().string_value

        tactic_str = self.get_parameter("hostile_tactic").get_parameter_value().string_value
        try:
            self.hostile_tactic = HostileTactic(tactic_str)
        except ValueError:
            self.get_logger().warning(
                f"Unknown hostile_tactic '{tactic_str}'; falling back to 'coordinated'."
            )
            self.hostile_tactic = HostileTactic.COORDINATED

        self.get_logger().info(
            f"Initializing USVSimNode [{self.stack_label}]: "
            f"{self.num_agents} allies vs {self.num_targets} hostiles | "
            f"target_mode={self.target_mode}, hostile_tactic={self.hostile_tactic.value}, "
            f"spawn_mode={self.spawn_mode}, dt={self.dt}s"
        )

        self.sim_time = 0.0

        # --- Hostile formation -----------------------------------------------------------
        self.hostiles = spawn_hostile_formation(
            self.num_targets, origin=(0.0, 0.0), heading=self.hostile_heading
        )

        # --- Fleet states & telemetry ----------------------------------------------------
        self.usv_states: Dict[int, np.ndarray] = {}
        self.cmd_vels: Dict[int, Tuple[float, float]] = {}
        self.thrust_efficiencies: Dict[int, float] = {}
        self.health_indices: Dict[int, float] = {}
        self.roles: Dict[int, str] = {}
        self.assigned_targets: Dict[int, int] = {}
        self.allocation_costs: Dict[int, float] = {}
        self.fault_descs: Dict[int, str] = {}
        self.history_tracks: Dict[int, deque] = {}

        self.odom_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.cmd_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.fault_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.health_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.role_subs: Dict[int, rclpy.subscription.Subscription] = {}

        spawn_poses = self._spawn_fleet()
        for i in range(self.num_agents):
            x, y, psi = spawn_poses[i]
            # State vector: [x, y, psi, u, v, r]
            self.usv_states[i] = np.array([x, y, psi, 0.0, 0.0, 0.0], dtype=np.float64)
            self.cmd_vels[i] = (0.0, 0.0)
            self.thrust_efficiencies[i] = 1.0
            self.health_indices[i] = 1.0
            self.roles[i] = "tracker"
            self.assigned_targets[i] = i % self.num_targets
            self.allocation_costs[i] = 0.0
            self.fault_descs[i] = "HEALTHY"
            self.history_tracks[i] = deque(maxlen=40)

            self.odom_pubs[i] = self.create_publisher(Odometry, f"/usv_{i}/odom", 10)
            self.cmd_subs[i] = self.create_subscription(
                Twist, f"/usv_{i}/cmd_vel", self._make_cmd_callback(i), 10
            )
            # Subscribe to fault injector to throttle physics
            self.fault_subs[i] = self.create_subscription(
                USVHealth, f"/usv_{i}/injected_fault", self._make_fault_callback(i), 10
            )
            # Subscribe to diagnosis for visualization billboard
            self.health_subs[i] = self.create_subscription(
                USVHealth, f"/usv_{i}/health", self._make_health_callback(i), 10
            )
            # Subscribe to role allocator for wing affiliation and billboard telemetry
            self.role_subs[i] = self.create_subscription(
                RoleAssignment, f"/usv_{i}/role", self._make_role_callback(i), 10
            )

        # --- Hostile publishers / defusal buses ------------------------------------------
        self.target_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.defuse_subs: Dict[int, rclpy.subscription.Subscription] = {}
        for m in range(self.num_targets):
            self.target_pubs[m] = self.create_publisher(Odometry, f"/target_{m}/odom", 10)
            self.defuse_subs[m] = self.create_subscription(
                Float32, f"/target_{m}/defuse_status", self._make_defuse_callback(m), 10
            )

        # Legacy single-target interfaces, mirrored to and from hostile 0.
        self.legacy_target_pub = self.create_publisher(Odometry, "/target/odom", 10)
        self.legacy_defuse_sub = self.create_subscription(
            Float32, "/target/defuse_status", self._make_defuse_callback(0), 10
        )
        self.mission_sub = self.create_subscription(
            MissionStatus, "/mission/status", self._mission_status_callback, 10
        )

        # --- Visualization ---------------------------------------------------------------
        self.marker_pub = self.create_publisher(MarkerArray, "/usv_swarm/markers", 10)
        self.visualizer = BattlefieldVisualizer(frame_id="world")

        # 20 Hz Simulation timer loop
        self.timer = self.create_timer(self.dt, self._sim_step)

    # ------------------------------------------------------------------
    # Spawn doctrine
    # ------------------------------------------------------------------
    def _spawn_fleet(self) -> Dict[int, Tuple[float, float, float]]:
        """
        Places the swarm at mission start according to the configured spawn doctrine.

        launch_corridor (default) sorties the fleet from a single staggered double-column
        wedge standing off the engagement box, as a real swarm launched from a mother platform
        would: the formation then transits, fans out and encircles. The legacy perimeter ring
        is retained so prototype runs remain reproducible.
        """
        poses: Dict[int, Tuple[float, float, float]] = {}
        # Fixed seed: spawn jitter must be reproducible run to run.
        rng = np.random.default_rng(20251020)

        if self.spawn_mode == SPAWN_PERIMETER_RING:
            for i in range(self.num_agents):
                angle = (2.0 * math.pi / max(1, self.num_agents)) * i
                dist = 38.0
                x = dist * math.cos(angle)
                y = dist * math.sin(angle)
                poses[i] = (x, y, self._wrap(angle + math.pi))
            return poses

        if self.spawn_mode == SPAWN_DISPERSED_PATROL:
            for i in range(self.num_agents):
                angle = (2.0 * math.pi / max(1, self.num_agents)) * i + float(rng.uniform(-0.25, 0.25))
                dist = 55.0 + float(rng.uniform(-12.0, 18.0))
                x = dist * math.cos(angle)
                y = dist * math.sin(angle)
                poses[i] = (x, y, self._wrap(angle + math.pi + float(rng.uniform(-0.3, 0.3))))
            return poses

        # Default: staggered double-column launch wedge.
        approach = self._wrap(self.launch_bearing + math.pi)
        ox = self.launch_standoff * math.cos(self.launch_bearing)
        oy = self.launch_standoff * math.sin(self.launch_bearing)

        for i in range(self.num_agents):
            lane = i % 2                       # 0 = port column, 1 = starboard column
            rank = i // 2                      # position back along the column
            along = -9.0 * rank + float(rng.uniform(-1.2, 1.2))
            across = (lane * 2.0 - 1.0) * 6.5 + float(rng.uniform(-0.8, 0.8))

            x = ox + along * math.cos(approach) - across * math.sin(approach)
            y = oy + along * math.sin(approach) + across * math.cos(approach)
            poses[i] = (x, y, self._wrap(approach + float(rng.uniform(-0.08, 0.08))))

        return poses

    @staticmethod
    def _wrap(a: float) -> float:
        return (a + math.pi) % (2.0 * math.pi) - math.pi

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    def _make_defuse_callback(self, target_id: int):
        def callback(msg: Float32):
            host = self.hostiles.get(target_id)
            if host is None:
                return
            host.defusal_progress = float(np.clip(msg.data, 0.0, 100.0))
            if host.defusal_progress >= 100.0:
                host.neutralized = True
        return callback

    def _mission_status_callback(self, msg: MissionStatus):
        # Legacy contract: debounced mission success arrests the primary hostile.
        if msg.debounced_success and 0 in self.hostiles:
            self.hostiles[0].neutralized = True

    def _make_cmd_callback(self, agent_id: int):
        def callback(msg: Twist):
            u_cmd = float(np.clip(msg.linear.x, 0.0, 3.5))
            r_cmd = float(np.clip(msg.angular.z, -0.8, 0.8))
            self.cmd_vels[agent_id] = (u_cmd, r_cmd)
        return callback

    def _make_fault_callback(self, agent_id: int):
        def callback(msg: USVHealth):
            # Continuously track thrust efficiency across nominal, degraded, and self-healing phases
            self.thrust_efficiencies[agent_id] = float(np.clip(msg.thrust_efficiency, 0.0, 1.0))
        return callback

    def _make_health_callback(self, agent_id: int):
        def callback(msg: USVHealth):
            self.health_indices[agent_id] = float(msg.health_index)
            self.fault_descs[agent_id] = msg.fault_description
        return callback

    def _make_role_callback(self, agent_id: int):
        def callback(msg: RoleAssignment):
            self.roles[agent_id] = msg.role
            self.assigned_targets[agent_id] = int(msg.assigned_target_id)
            self.allocation_costs[agent_id] = float(msg.allocation_cost)
        return callback

    # ------------------------------------------------------------------
    # Simulation loop
    # ------------------------------------------------------------------
    def _sim_step(self):
        self.sim_time += self.dt
        now = self.get_clock().now().to_msg()

        pursuer_positions = [
            (float(self.usv_states[i][0]), float(self.usv_states[i][1]))
            for i in range(self.num_agents)
            if self.health_indices.get(i, 1.0) >= CASUALTY_HEALTH
        ]
        partners = list(self.hostiles.values())

        # 1. Advance each hostile under the coordinated hostile AI.
        for m in sorted(self.hostiles.keys()):
            host = self.hostiles[m]
            step_hostile(
                host=host,
                dt=self.dt,
                sim_time=self.sim_time,
                threats=pursuer_positions,
                partners=partners,
                tactic=self.hostile_tactic,
                mode=self.target_mode,
                drift=(self.drift_x, self.drift_y),
            )
            self._publish_hostile_odom(now, m, host)

        # 2. Advance USV 3-DOF vessel dynamics.
        for i in range(self.num_agents):
            x, y, psi, u, v, r = self.usv_states[i]
            u_cmd, r_cmd = self.cmd_vels[i]

            # Physical thrust degradation scaling; a combat loss has no propulsion at all.
            eff = self.thrust_efficiencies.get(i, 1.0)
            if self.health_indices.get(i, 1.0) < CASUALTY_HEALTH:
                eff = 0.0
            u_cmd_effective = u_cmd * eff
            r_cmd_effective = r_cmd * (1.0 if eff > 0.05 else 0.0)

            # Simplified body acceleration model
            u_dot = 2.0 * (u_cmd_effective - u)
            r_dot = 3.0 * (r_cmd_effective - r)

            # Kinematics
            x_dot = u * math.cos(psi) - v * math.sin(psi) + self.drift_x
            y_dot = u * math.sin(psi) + v * math.cos(psi) + self.drift_y

            # Euler integration
            x += x_dot * self.dt
            y += y_dot * self.dt
            psi = self._wrap(psi + r * self.dt)
            u += u_dot * self.dt
            r += r_dot * self.dt

            self.usv_states[i] = np.array([x, y, psi, u, v, r], dtype=np.float64)
            self.history_tracks[i].append((float(x), float(y)))

            odom = Odometry()
            odom.header.stamp = now
            odom.header.frame_id = "world"
            odom.child_frame_id = f"usv_{i}_link"
            odom.pose.pose.position.x = float(x)
            odom.pose.pose.position.y = float(y)
            odom.pose.pose.orientation.z = math.sin(psi / 2.0)
            odom.pose.pose.orientation.w = math.cos(psi / 2.0)
            odom.twist.twist.linear.x = float(u)
            odom.twist.twist.angular.z = float(r)
            self.odom_pubs[i].publish(odom)

        # 3. Publish the battle visualization.
        self._publish_battle_markers(now)

    def _publish_hostile_odom(self, now, target_id: int, host) -> None:
        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = "world"
        odom.child_frame_id = f"target_{target_id}_link"
        odom.pose.pose.position.x = float(host.x)
        odom.pose.pose.position.y = float(host.y)
        odom.pose.pose.orientation.z = math.sin(host.psi / 2.0)
        odom.pose.pose.orientation.w = math.cos(host.psi / 2.0)
        odom.twist.twist.linear.x = float(host.speed)
        odom.twist.twist.angular.z = float(host.yaw_rate)
        self.target_pubs[target_id].publish(odom)

        if target_id == 0:
            # Legacy alias: every single-target consumer keeps working unchanged.
            legacy = Odometry()
            legacy.header.stamp = now
            legacy.header.frame_id = "world"
            legacy.child_frame_id = "target_link"
            legacy.pose.pose = odom.pose.pose
            legacy.twist.twist = odom.twist.twist
            self.legacy_target_pub.publish(legacy)

    # ------------------------------------------------------------------
    # Visualization
    # ------------------------------------------------------------------
    def _agent_tactical_state(self, agent_id: int) -> str:
        """Display state for the billboard, derived from role, geometry and mission state."""
        h = self.health_indices.get(agent_id, 1.0)
        desc = (self.fault_descs.get(agent_id) or "").upper()
        repairing = ("REPAIR" in desc) or ("HEALING" in desc)

        if h < CASUALTY_HEALTH:
            return "UNDER REPAIR" if repairing else "COMBAT LOSS"
        if h < 0.55:
            return f"RECOVERING h={h:.2f}" if repairing else "WITHDRAWING"
        if repairing:
            return f"REJOINING FORMATION h={h:.2f}"

        role = self.roles.get(agent_id, "tracker").lower()
        tid = self.assigned_targets.get(agent_id, 0)
        host = self.hostiles.get(tid)
        if host is None:
            return "UNASSIGNED"

        x, y = self.usv_states[agent_id][0], self.usv_states[agent_id][1]
        d = math.hypot(x - host.x, y - host.y)

        if "interceptor" in role:
            if host.neutralized:
                return "ARREST ESCORT"
            if host.defusal_progress > 0.0 and d <= 7.5:
                return f"EMP LOCK {int(host.defusal_progress)}%"
            if d <= 12.0:
                return "TERMINAL RUN"
            if d > 45.0:
                return "TRANSIT"
            return "ATTACK VECTOR"

        if "tracker" in role:
            if host.neutralized:
                return "ARREST GUARD"
            if d > 45.0:
                return "TRANSIT"
            if d <= 26.0:
                return "CONTAINMENT RING"
            return "DEPLOYING"

        if "relay" in role:
            return "MESH BACKBONE" if d <= 45.0 else "TRANSIT"
        return "WIDE PICKET" if d <= 60.0 else "TRANSIT"

    def _battle_hud(self) -> List[str]:
        """Fleet-level scoreboard rendered above the engagement box."""
        arrested = sum(1 for h in self.hostiles.values() if h.neutralized)
        losses = sum(1 for i in range(self.num_agents)
                     if self.health_indices.get(i, 1.0) < CASUALTY_HEALTH)

        role_counts: Dict[str, int] = {}
        for i in range(self.num_agents):
            if self.health_indices.get(i, 1.0) < CASUALTY_HEALTH:
                continue
            role_counts[self.roles.get(i, "tracker").lower()] = \
                role_counts.get(self.roles.get(i, "tracker").lower(), 0) + 1

        min_sep = float('inf')
        live = [i for i in range(self.num_agents)
                if self.health_indices.get(i, 1.0) >= CASUALTY_HEALTH]
        for a_idx in range(len(live)):
            for b_idx in range(a_idx + 1, len(live)):
                ia, ib = live[a_idx], live[b_idx]
                min_sep = min(min_sep, math.hypot(
                    self.usv_states[ia][0] - self.usv_states[ib][0],
                    self.usv_states[ia][1] - self.usv_states[ib][1]))

        lines = [
            f">>> {self.stack_label} <<<",
            f"t={self.sim_time:6.1f}s   "
            f"HOSTILES {self.num_targets} ({arrested} ARRESTED)   "
            f"FLEET {self.num_agents} ({losses} LOST)"
        ]
        for m in sorted(self.hostiles.keys()):
            h = self.hostiles[m]
            pct = int(h.defusal_progress)
            filled = int(14 * pct / 100.0)
            bar = "#" * filled + "-" * (14 - filled)
            lines.append(
                f"H{m} {h.role_label:<9} {h.speed:4.2f} m/s  [{bar}] {pct:3d}%  {h.combat_state}"
            )
        role_line = (
            f"ROLES  INT {role_counts.get('interceptor', 0)} | "
            f"TRK {role_counts.get('tracker', 0)} | "
            f"RLY {role_counts.get('relay', 0)} | "
            f"SCT {role_counts.get('scout', 0)}"
        )
        if min_sep != float('inf'):
            role_line += f"    MIN ALLY SEP {min_sep:5.2f} m"
        lines.append(role_line)
        return lines

    def _publish_battle_markers(self, now) -> None:
        hostile_visuals = [
            HostileVisual(
                target_id=h.target_id,
                x=h.x, y=h.y, psi=h.psi, speed=h.speed,
                health=h.health,
                defusal_progress=h.defusal_progress,
                neutralized=h.neutralized,
                combat_state=h.combat_state,
                tactic=h.tactic_note,
                history=tuple(h.history),
            )
            for h in (self.hostiles[m] for m in sorted(self.hostiles.keys()))
        ]

        agent_visuals = [
            AgentVisual(
                agent_id=i,
                x=float(self.usv_states[i][0]),
                y=float(self.usv_states[i][1]),
                psi=float(self.usv_states[i][2]),
                speed=float(self.usv_states[i][3]),
                role=self.roles.get(i, "tracker"),
                health=self.health_indices.get(i, 1.0),
                thrust_efficiency=self.thrust_efficiencies.get(i, 1.0),
                assigned_target_id=self.assigned_targets.get(i, 0),
                allocation_cost=self.allocation_costs.get(i, 0.0),
                tactical_state=self._agent_tactical_state(i),
                history=tuple(self.history_tracks[i]),
            )
            for i in range(self.num_agents)
        ]

        centre_x = sum(h.x for h in hostile_visuals) / len(hostile_visuals)
        centre_y = sum(h.y for h in hostile_visuals) / len(hostile_visuals)

        markers = self.visualizer.build(
            stamp=now,
            hostiles=hostile_visuals,
            agents=agent_visuals,
            hud_lines=self._battle_hud(),
            hud_anchor=(centre_x, centre_y),
            comm_range=self.comm_range,
        )
        self.marker_pub.publish(markers)


def main(args=None):
    rclpy.init(args=args)
    node = USVSimNode()
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


if __name__ == "__main__":
    main()
