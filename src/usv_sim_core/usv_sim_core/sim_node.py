"""
USV Swarm Simulation - High-Fidelity 3-DOF Multi-USV & Target Simulation Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Standardized ROS 2 Interfaces:
- Subscribes: /usv_{i}/cmd_vel (geometry_msgs/msg/Twist)
- Subscribes: /usv_{i}/injected_fault (usv_msgs/msg/USVHealth)
- Subscribes: /usv_{i}/health (usv_msgs/msg/USVHealth)
- Subscribes: /usv_{i}/role (usv_msgs/msg/RoleAssignment)
- Publishes:  /usv_{i}/odom (nav_msgs/msg/Odometry)
- Publishes:  /target/odom (nav_msgs/msg/Odometry)
- Publishes:  /usv_swarm/markers (visualization_msgs/msg/MarkerArray)
"""

from __future__ import annotations
import math
from collections import deque
from typing import Dict, List, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, Point, Quaternion
from nav_msgs.msg import Odometry
from visualization_msgs.msg import Marker, MarkerArray
from usv_msgs.msg import USVHealth, RoleAssignment, MissionStatus
from std_msgs.msg import Float32


class USVSimNode(Node):
    """High-fidelity 3-DOF simulation and rich 3D visualization node for USV swarm."""

    def __init__(self):
        super().__init__("usv_sim_node")

        # Declare parameters
        self.declare_parameter("num_agents", 6)
        self.declare_parameter("target_mode", "reactive")
        self.declare_parameter("dt", 0.05)  # 20 Hz simulation loop
        self.declare_parameter("drift_x", 0.0)
        self.declare_parameter("drift_y", 0.0)

        self.num_agents = self.get_parameter("num_agents").get_parameter_value().integer_value
        self.target_mode = self.get_parameter("target_mode").get_parameter_value().string_value
        self.dt = self.get_parameter("dt").get_parameter_value().double_value
        self.drift_x = self.get_parameter("drift_x").get_parameter_value().double_value
        self.drift_y = self.get_parameter("drift_y").get_parameter_value().double_value

        self.get_logger().info(
            f"Initializing USVSimNode with {self.num_agents} vessels, "
            f"target_mode={self.target_mode}, dt={self.dt}s"
        )

        # Target vessel state: [x, y, psi, speed, yaw_rate]
        self.target_x = 0.0
        self.target_y = 0.0
        self.target_psi = 0.0
        self.target_speed = 1.2
        self.target_yaw_rate = 0.0
        self.target_time = 0.0

        # Fleet states & telemetry
        self.usv_states: Dict[int, np.ndarray] = {}
        self.cmd_vels: Dict[int, Tuple[float, float]] = {}
        self.thrust_efficiencies: Dict[int, float] = {}
        self.health_indices: Dict[int, float] = {}
        self.roles: Dict[int, str] = {}
        self.fault_descs: Dict[int, str] = {}
        self.history_tracks: Dict[int, deque] = {}
        self.target_history: deque = deque(maxlen=60)

        # Publishers and Subscribers per USV
        self.odom_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.cmd_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.fault_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.health_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.role_subs: Dict[int, rclpy.subscription.Subscription] = {}

        # Initialize fleet positions in a dispersed perimeter
        for i in range(self.num_agents):
            angle = (2.0 * math.pi / self.num_agents) * i
            dist = 38.0
            x = self.target_x + dist * math.cos(angle)
            y = self.target_y + dist * math.sin(angle)
            psi = (angle + math.pi) % (2 * math.pi) - math.pi

            # State vector: [x, y, psi, u, v, r]
            self.usv_states[i] = np.array([x, y, psi, 0.0, 0.0, 0.0], dtype=np.float64)
            self.cmd_vels[i] = (0.0, 0.0)
            self.thrust_efficiencies[i] = 1.0
            self.health_indices[i] = 1.0
            self.roles[i] = "tracker" if i < 2 else ("interceptor" if i < 4 else "scout")
            self.fault_descs[i] = "HEALTHY"
            self.history_tracks[i] = deque(maxlen=40)

            # Publishers & Subscriptions
            self.odom_pubs[i] = self.create_publisher(Odometry, f"/usv_{i}/odom", 10)
            self.cmd_subs[i] = self.create_subscription(
                Twist,
                f"/usv_{i}/cmd_vel",
                self._make_cmd_callback(i),
                10
            )
            # Subscribe to fault injector to throttle physics
            self.fault_subs[i] = self.create_subscription(
                USVHealth,
                f"/usv_{i}/injected_fault",
                self._make_fault_callback(i),
                10
            )
            # Subscribe to diagnosis for visualization billboard
            self.health_subs[i] = self.create_subscription(
                USVHealth,
                f"/usv_{i}/health",
                self._make_health_callback(i),
                10
            )
            # Subscribe to role allocator for visualization billboard
            self.role_subs[i] = self.create_subscription(
                RoleAssignment,
                f"/usv_{i}/role",
                self._make_role_callback(i),
                10
            )

        # Target publisher, swarm visualizer & mission status subscriber
        self.target_pub = self.create_publisher(Odometry, "/target/odom", 10)
        self.marker_pub = self.create_publisher(MarkerArray, "/usv_swarm/markers", 10)
        self.target_arrested = False
        self.target_defused = False
        self.defusal_progress = 0.0
        self.target_combat_state = "COMBAT_PATROL"
        self.mission_sub = self.create_subscription(
            MissionStatus,
            "/mission/status",
            self._mission_status_callback,
            10
        )
        self.defuse_sub = self.create_subscription(
            Float32,
            "/target/defuse_status",
            self._defuse_status_callback,
            10
        )

        # 20 Hz Simulation timer loop
        self.timer = self.create_timer(self.dt, self._sim_step)

    def _defuse_status_callback(self, msg: Float32):
        self.defusal_progress = float(msg.data)
        if self.defusal_progress >= 100.0:
            self.target_defused = True
            self.target_arrested = True

    def _mission_status_callback(self, msg: MissionStatus):
        self.target_arrested = bool(msg.debounced_success)

    def _make_cmd_callback(self, agent_id: int):
        def callback(msg: Twist):
            u_cmd = float(np.clip(msg.linear.x, 0.0, 3.5))
            r_cmd = float(np.clip(msg.angular.z, -0.8, 0.8))
            self.cmd_vels[agent_id] = (u_cmd, r_cmd)
        return callback

    def _make_fault_callback(self, agent_id: int):
        def callback(msg: USVHealth):
            # Continuously track thrust efficiency across nominal, degraded, and self-healing phases
            self.thrust_efficiencies[agent_id] = float(np.clip(msg.thrust_efficiency, 0.1, 1.0))
        return callback

    def _make_health_callback(self, agent_id: int):
        def callback(msg: USVHealth):
            self.health_indices[agent_id] = float(msg.health_index)
            self.fault_descs[agent_id] = msg.fault_description
        return callback

    def _make_role_callback(self, agent_id: int):
        def callback(msg: RoleAssignment):
            self.roles[agent_id] = msg.role
        return callback

    def _sim_step(self):
        self.target_time += self.dt

        # 1. Update Target Vessel Kinematics
        tracker_positions = [
            (self.usv_states[i][0], self.usv_states[i][1])
            for i in range(self.num_agents)
        ]

        if self.target_mode == "nominal":
            self.target_speed = 1.2
            self.target_yaw_rate = 0.0
        elif self.target_mode == "sinusoidal":
            self.target_speed = 1.5
            omega = 2.0 * math.pi / 15.0
            self.target_yaw_rate = float(np.clip(4.0 * omega * math.cos(omega * self.target_time) * 0.1, -0.4, 0.4))
        else:  # reactive combat mode
            if self.target_defused or self.target_arrested:
                # Fully neutralized and defused by interceptor
                self.target_speed = max(0.0, self.target_speed - 1.2 * self.dt)
                self.target_yaw_rate = 0.0
                self.target_combat_state = "DEFUSED_AND_ARRESTED"
            elif self.defusal_progress > 0.0:
                # Under active electronic/EMP defusal lock: propulsion stutters and decays
                self.target_combat_state = f"UNDER_DEFUSAL ({int(self.defusal_progress)}%)"
                self.target_speed = max(0.1, 1.4 * (1.0 - self.defusal_progress / 100.0))
                self.target_yaw_rate = 0.25 * math.sin(5.0 * self.target_time)
            else:
                close_usvs = []
                for ux, uy in tracker_positions:
                    d = math.hypot(ux - self.target_x, uy - self.target_y)
                    if d <= 35.0:
                        close_usvs.append((ux, uy, d))

                if close_usvs:
                    self.target_combat_state = "HOSTILE_EVASION"
                    min_d = min(d for _, _, d in close_usvs)
                    # Hostile evasion AI: evaluate perimeter angles and sprint through largest gap
                    bearings = sorted([math.atan2(uy - self.target_y, ux - self.target_x) for ux, uy, _ in close_usvs])
                    M = len(bearings)
                    max_gap = 0.0
                    escape_mid = self.target_psi

                    for k in range(M):
                        gap = (bearings[(k + 1) % M] - bearings[k]) % (2.0 * math.pi)
                        if gap > max_gap:
                            max_gap = gap
                            escape_mid = (bearings[k] + gap / 2.0 + math.pi) % (2.0 * math.pi) - math.pi

                    yaw_err = (escape_mid - self.target_psi + math.pi) % (2.0 * math.pi) - math.pi
                    weaving = 0.20 * math.sin(2.0 * self.target_time)
                    self.target_yaw_rate = float(np.clip(yaw_err * 2.0 + weaving, -0.6, 0.6))
                    
                    # If USVs are flanking within 16m, evasion is contested/choked down
                    top_speed = 1.15 if min_d < 16.0 else 1.65
                    self.target_speed = min(top_speed, self.target_speed + 0.3 * self.dt)
                else:
                    self.target_combat_state = "COMBAT_PATROL"
                    self.target_speed = min(1.2, self.target_speed + 0.2 * self.dt)
                    self.target_yaw_rate = 0.1 * math.sin(0.5 * self.target_time)

        # Integrate target pose
        self.target_psi = (self.target_psi + self.target_yaw_rate * self.dt + math.pi) % (2 * math.pi) - math.pi
        self.target_x += (self.target_speed * math.cos(self.target_psi) + self.drift_x) * self.dt
        self.target_y += (self.target_speed * math.sin(self.target_psi) + self.drift_y) * self.dt
        self.target_history.append((float(self.target_x), float(self.target_y)))

        # Publish Target Odometry
        target_odom = Odometry()
        target_odom.header.stamp = self.get_clock().now().to_msg()
        target_odom.header.frame_id = "world"
        target_odom.child_frame_id = "target_link"
        target_odom.pose.pose.position.x = float(self.target_x)
        target_odom.pose.pose.position.y = float(self.target_y)
        target_odom.pose.pose.orientation.z = math.sin(self.target_psi / 2.0)
        target_odom.pose.pose.orientation.w = math.cos(self.target_psi / 2.0)
        target_odom.twist.twist.linear.x = float(self.target_speed)
        target_odom.twist.twist.angular.z = float(self.target_yaw_rate)
        self.target_pub.publish(target_odom)

        # 2. Advance USV 3-DOF Vessel Dynamics
        for i in range(self.num_agents):
            st = self.usv_states[i]
            x, y, psi, u, v, r = st
            u_cmd, r_cmd = self.cmd_vels[i]

            # Physical thrust degradation scaling
            eff = self.thrust_efficiencies.get(i, 1.0)
            u_cmd_effective = u_cmd * eff

            # Simplified body acceleration model
            u_dot = 2.0 * (u_cmd_effective - u)
            r_dot = 3.0 * (r_cmd - r)

            # Kinematics
            x_dot = u * math.cos(psi) - v * math.sin(psi) + self.drift_x
            y_dot = u * math.sin(psi) + v * math.cos(psi) + self.drift_y
            psi_dot = r

            # Euler integration
            x += x_dot * self.dt
            y += y_dot * self.dt
            psi = (psi + psi_dot * self.dt + math.pi) % (2 * math.pi) - math.pi
            u += u_dot * self.dt
            r += r_dot * self.dt

            self.usv_states[i] = np.array([x, y, psi, u, v, r], dtype=np.float64)
            self.history_tracks[i].append((float(x), float(y)))

            # Publish USV Odometry
            odom = Odometry()
            odom.header.stamp = self.get_clock().now().to_msg()
            odom.header.frame_id = "world"
            odom.child_frame_id = f"usv_{i}_link"
            odom.pose.pose.position.x = float(x)
            odom.pose.pose.position.y = float(y)
            odom.pose.pose.orientation.z = math.sin(psi / 2.0)
            odom.pose.pose.orientation.w = math.cos(psi / 2.0)
            odom.twist.twist.linear.x = float(u)
            odom.twist.twist.angular.z = float(r)
            self.odom_pubs[i].publish(odom)

        # 3. Publish High-Fidelity RViz 3D Markers
        self._publish_rviz_markers()

    def _publish_rviz_markers(self):
        markers = MarkerArray()
        now = self.get_clock().now().to_msg()
        marker_id = 0

        # =====================================================================
        # 1. TARGET VESSEL COMPOUND 3D VISUALIZATION
        # =====================================================================
        # 1.1 Target Hull (Crimson Red Cutter)
        th = Marker()
        th.header.frame_id = "world"
        th.header.stamp = now
        th.ns = "target_hull"
        th.id = 0
        th.type = Marker.CUBE
        th.action = Marker.ADD
        th.pose.position.x = float(self.target_x)
        th.pose.position.y = float(self.target_y)
        th.pose.position.z = 0.5
        th.pose.orientation.z = math.sin(self.target_psi / 2.0)
        th.pose.orientation.w = math.cos(self.target_psi / 2.0)
        th.scale.x = 4.6
        th.scale.y = 2.0
        th.scale.z = 1.0
        th.color.r = 0.95
        th.color.g = 0.12
        th.color.b = 0.12
        th.color.a = 0.95
        markers.markers.append(th)

        # 1.2 Target Deckhouse / Cabin (Dark Charcoal)
        tc = Marker()
        tc.header.frame_id = "world"
        tc.header.stamp = now
        tc.ns = "target_cabin"
        tc.id = 0
        tc.type = Marker.CUBE
        tc.action = Marker.ADD
        # Cabin slightly offset aft
        c_x = self.target_x - 0.5 * math.cos(self.target_psi)
        c_y = self.target_y - 0.5 * math.sin(self.target_psi)
        tc.pose.position.x = float(c_x)
        tc.pose.position.y = float(c_y)
        tc.pose.position.z = 1.25
        tc.pose.orientation.z = math.sin(self.target_psi / 2.0)
        tc.pose.orientation.w = math.cos(self.target_psi / 2.0)
        tc.scale.x = 1.8
        tc.scale.y = 1.4
        tc.scale.z = 0.7
        tc.color.r = 0.20
        tc.color.g = 0.22
        tc.color.b = 0.28
        tc.color.a = 0.98
        markers.markers.append(tc)

        # 1.3 Target Radar Mast & Dome
        tmast = Marker()
        tmast.header.frame_id = "world"
        tmast.header.stamp = now
        tmast.ns = "target_mast"
        tmast.id = 0
        tmast.type = Marker.CYLINDER
        tmast.action = Marker.ADD
        tmast.pose.position.x = float(c_x)
        tmast.pose.position.y = float(c_y)
        tmast.pose.position.z = 1.85
        tmast.scale.x = 0.45
        tmast.scale.y = 0.45
        tmast.scale.z = 0.5
        tmast.color.r = 1.0
        tmast.color.g = 0.95
        tmast.color.b = 0.95
        tmast.color.a = 1.0
        markers.markers.append(tmast)

        # 1.4 Target Bow Direction Arrow
        tarrow = Marker()
        tarrow.header.frame_id = "world"
        tarrow.header.stamp = now
        tarrow.ns = "target_heading"
        tarrow.id = 0
        tarrow.type = Marker.ARROW
        tarrow.action = Marker.ADD
        tarrow.pose.position.x = float(self.target_x)
        tarrow.pose.position.y = float(self.target_y)
        tarrow.pose.position.z = 0.5
        tarrow.pose.orientation.z = math.sin(self.target_psi / 2.0)
        tarrow.pose.orientation.w = math.cos(self.target_psi / 2.0)
        tarrow.scale.x = 3.2
        tarrow.scale.y = 0.45
        tarrow.scale.z = 0.45
        tarrow.color.r = 1.0
        tarrow.color.g = 0.8
        tarrow.color.b = 0.1
        tarrow.color.a = 0.95
        markers.markers.append(tarrow)

        # 1.5 Target 3D Floating Billboard
        tbill = Marker()
        tbill.header.frame_id = "world"
        tbill.header.stamp = now
        tbill.ns = "target_billboard"
        tbill.id = 0
        tbill.type = Marker.TEXT_VIEW_FACING
        tbill.action = Marker.ADD
        tbill.pose.position.x = float(self.target_x)
        tbill.pose.position.y = float(self.target_y)
        tbill.pose.position.z = 3.2
        tbill.scale.z = 1.1
        if self.target_defused or self.target_arrested:
            tbill.color.r = 0.1
            tbill.color.g = 1.0
            tbill.color.b = 0.2
            tbill.color.a = 1.0
            tbill.text = f"★ ENEMY TARGET DEFUSED & ARRESTED ★\nSpeed: 0.00 m/s | Neutralized by Interceptor"
        elif self.defusal_progress > 0.0:
            tbill.color.r = 1.0
            tbill.color.g = 0.9
            tbill.color.b = 0.1
            tbill.color.a = 1.0
            tbill.text = f"⚡ UNDER ACTIVE EMP DEFUSAL ({int(self.defusal_progress)}%) ⚡\nSpeed: {self.target_speed:.2f} m/s"
        else:
            tbill.color.r = 1.0
            tbill.color.g = 0.2
            tbill.color.b = 0.2
            tbill.color.a = 1.0
            tbill.text = f"HOSTILE ENEMY DRONE [{self.target_combat_state}]\nSpeed: {self.target_speed:.2f} m/s | Evasive Breakout"
        markers.markers.append(tbill)

        # 1.6 Electronic / EMP Neutralization Beam (From Interceptor to Target)
        if self.defusal_progress > 0.0:
            for aid, role_str in self.roles.items():
                if "interceptor" in role_str.lower() and aid in self.usv_states:
                    ix, iy = self.usv_states[aid][0], self.usv_states[aid][1]
                    beam = Marker()
                    beam.header.frame_id = "world"
                    beam.header.stamp = now
                    beam.ns = "emp_defusal_beam"
                    beam.id = 0
                    beam.type = Marker.LINE_LIST
                    beam.action = Marker.ADD
                    beam.scale.x = 0.35  # Line thickness
                    beam.color.r = 0.2
                    beam.color.g = 0.8
                    beam.color.b = 1.0
                    beam.color.a = 0.95
                    p_i = Point()
                    p_i.x, p_i.y, p_i.z = float(ix), float(iy), 1.2
                    p_t = Point()
                    p_t.x, p_t.y, p_t.z = float(self.target_x), float(self.target_y), 1.5
                    beam.points = [p_i, p_t]
                    markers.markers.append(beam)
                    break

        # 1.7 Target Intercept Containment Net Cage (Visual Proof of Arrest)
        if self.target_arrested or self.target_defused:
            cage = Marker()
            cage.header.frame_id = "world"
            cage.header.stamp = now
            cage.ns = "containment_cage"
            cage.id = 0
            cage.type = Marker.CYLINDER
            cage.action = Marker.ADD
            cage.pose.position.x = float(self.target_x)
            cage.pose.position.y = float(self.target_y)
            cage.pose.position.z = 1.5
            cage.scale.x = 12.0
            cage.scale.y = 12.0
            cage.scale.z = 3.0
            cage.color.r = 0.2
            cage.color.g = 1.0
            cage.color.b = 0.3
            cage.color.a = 0.35
            markers.markers.append(cage)

        # =====================================================================
        # 2. CONCENTRIC TACTICAL PERIMETER RINGS
        # =====================================================================
        # 2.1 20m Encirclement Standoff Ring (Glowing Emerald Green)
        ring_20m = Marker()
        ring_20m.header.frame_id = "world"
        ring_20m.header.stamp = now
        ring_20m.ns = "rings"
        ring_20m.id = 0
        ring_20m.type = Marker.LINE_STRIP
        ring_20m.action = Marker.ADD
        ring_20m.scale.x = 0.35  # Line thickness
        ring_20m.color.r = 0.10
        ring_20m.color.g = 0.95
        ring_20m.color.b = 0.55
        ring_20m.color.a = 0.85
        for deg in range(0, 365, 5):
            rad = math.radians(deg)
            p = Point()
            p.x = self.target_x + 20.0 * math.cos(rad)
            p.y = self.target_y + 20.0 * math.sin(rad)
            p.z = 0.1
            ring_20m.points.append(p)
        markers.markers.append(ring_20m)

        # 2.2 6m Non-Destructive Arrest Zone (Translucent Crimson/Amber)
        ring_6m = Marker()
        ring_6m.header.frame_id = "world"
        ring_6m.header.stamp = now
        ring_6m.ns = "rings"
        ring_6m.id = 1
        ring_6m.type = Marker.LINE_STRIP
        ring_6m.action = Marker.ADD
        ring_6m.scale.x = 0.25
        ring_6m.color.r = 0.95
        ring_6m.color.g = 0.35
        ring_6m.color.b = 0.10
        ring_6m.color.a = 0.75
        for deg in range(0, 365, 10):
            rad = math.radians(deg)
            p = Point()
            p.x = self.target_x + 6.0 * math.cos(rad)
            p.y = self.target_y + 6.0 * math.sin(rad)
            p.z = 0.15
            ring_6m.points.append(p)
        markers.markers.append(ring_6m)

        # 2.3 30m Relay Standoff Perimeter (Subtle Gold/Amber)
        ring_30m = Marker()
        ring_30m.header.frame_id = "world"
        ring_30m.header.stamp = now
        ring_30m.ns = "rings"
        ring_30m.id = 2
        ring_30m.type = Marker.LINE_STRIP
        ring_30m.action = Marker.ADD
        ring_30m.scale.x = 0.20
        ring_30m.color.r = 1.0
        ring_30m.color.g = 0.75
        ring_30m.color.b = 0.15
        ring_30m.color.a = 0.40
        for deg in range(0, 365, 10):
            rad = math.radians(deg)
            p = Point()
            p.x = self.target_x + 30.0 * math.cos(rad)
            p.y = self.target_y + 30.0 * math.sin(rad)
            p.z = 0.05
            ring_30m.points.append(p)
        markers.markers.append(ring_30m)

        # =====================================================================
        # 3. HIGH-FIDELITY USV FLEET COMPOUND 3D MODELS
        # =====================================================================
        for i in range(self.num_agents):
            st = self.usv_states[i]
            x, y, psi, u, v, r = st
            role = self.roles.get(i, "tracker").lower()
            h_i = self.health_indices.get(i, 1.0)
            eff = self.thrust_efficiencies.get(i, 1.0)
            is_degraded = (h_i < 0.60) or (eff < 0.8)

            # Palette assignment based on role and operational health
            if is_degraded:
                # Flashing Hazard Orange/Red
                r_c, g_c, b_c = 1.0, 0.25, 0.05
            elif "tracker" in role:
                # Electric Cyan / Sapphire
                r_c, g_c, b_c = 0.05, 0.75, 1.0
            elif "interceptor" in role:
                # Royal Azure
                r_c, g_c, b_c = 0.15, 0.35, 0.95
            elif "relay" in role:
                # Amber Gold
                r_c, g_c, b_c = 1.0, 0.75, 0.05
            else:  # scout
                # Emerald Green
                r_c, g_c, b_c = 0.10, 0.90, 0.35

            # 3.1 Main USV Catamaran Hull
            uh = Marker()
            uh.header.frame_id = "world"
            uh.header.stamp = now
            uh.ns = "usv_hull"
            uh.id = i
            uh.type = Marker.CUBE
            uh.action = Marker.ADD
            uh.pose.position.x = float(x)
            uh.pose.position.y = float(y)
            uh.pose.position.z = 0.35
            uh.pose.orientation.z = math.sin(psi / 2.0)
            uh.pose.orientation.w = math.cos(psi / 2.0)
            uh.scale.x = 3.6
            uh.scale.y = 1.5
            uh.scale.z = 0.6
            uh.color.r = r_c
            uh.color.g = g_c
            uh.color.b = b_c
            uh.color.a = 0.95
            markers.markers.append(uh)

            # 3.2 USV Wheelhouse / Cabin (Marine White/Silver)
            uc = Marker()
            uc.header.frame_id = "world"
            uc.header.stamp = now
            uc.ns = "usv_cabin"
            uc.id = i
            uc.type = Marker.CUBE
            uc.action = Marker.ADD
            cab_x = x - 0.4 * math.cos(psi)
            cab_y = y - 0.4 * math.sin(psi)
            uc.pose.position.x = float(cab_x)
            uc.pose.position.y = float(cab_y)
            uc.pose.position.z = 0.85
            uc.pose.orientation.z = math.sin(psi / 2.0)
            uc.pose.orientation.w = math.cos(psi / 2.0)
            uc.scale.x = 1.5
            uc.scale.y = 1.0
            uc.scale.z = 0.55
            uc.color.r = 0.92
            uc.color.g = 0.94
            uc.color.b = 0.98
            uc.color.a = 0.98
            markers.markers.append(uc)

            # 3.3 USV Communication Mast & Radome
            umast = Marker()
            umast.header.frame_id = "world"
            umast.header.stamp = now
            umast.ns = "usv_mast"
            umast.id = i
            umast.type = Marker.CYLINDER
            umast.action = Marker.ADD
            umast.pose.position.x = float(cab_x)
            umast.pose.position.y = float(cab_y)
            umast.pose.position.z = 1.45
            umast.scale.x = 0.3
            umast.scale.y = 0.3
            umast.scale.z = 0.7
            if is_degraded:
                # Pulsing / Warning Beacon
                umast.color.r = 1.0
                umast.color.g = 0.1
                umast.color.b = 0.1
            else:
                umast.color.r = 0.1
                umast.color.g = 0.8
                umast.color.b = 1.0
            umast.color.a = 1.0
            markers.markers.append(umast)

            # 3.4 Directional Heading Bow Arrow
            ua = Marker()
            ua.header.frame_id = "world"
            ua.header.stamp = now
            ua.ns = "usv_arrow"
            ua.id = i
            ua.type = Marker.ARROW
            ua.action = Marker.ADD
            ua.pose.position.x = float(x)
            ua.pose.position.y = float(y)
            ua.pose.position.z = 0.4
            ua.pose.orientation.z = math.sin(psi / 2.0)
            ua.pose.orientation.w = math.cos(psi / 2.0)
            ua.scale.x = 2.8
            ua.scale.y = 0.35
            ua.scale.z = 0.35
            ua.color.r = r_c
            ua.color.g = g_c
            ua.color.b = b_c
            ua.color.a = 0.9
            markers.markers.append(ua)

            # 3.5 3D Floating Telemetry Billboard
            ubill = Marker()
            ubill.header.frame_id = "world"
            ubill.header.stamp = now
            ubill.ns = "usv_billboard"
            ubill.id = i
            ubill.type = Marker.TEXT_VIEW_FACING
            ubill.action = Marker.ADD
            ubill.pose.position.x = float(x)
            ubill.pose.position.y = float(y)
            ubill.pose.position.z = 2.6
            ubill.scale.z = 0.85
            ubill.color.r = 1.0 if is_degraded else 0.9
            ubill.color.g = 0.2 if is_degraded else 0.95
            ubill.color.b = 0.1 if is_degraded else 1.0
            ubill.color.a = 1.0

            if is_degraded:
                ubill.text = f"USV-{i} [{role.upper()}]\n⚠️ DEGRADED h={h_i:.2f} (Eff: {eff*100:.0f}%)"
            elif "interceptor" in role:
                if self.defusal_progress > 0.0:
                    ubill.text = f"USV-{i} [INTERCEPTOR]\n⚡ EMP DEFUSAL LOCK: {int(self.defusal_progress)}% | {u:.1f} m/s"
                else:
                    ubill.text = f"USV-{i} [INTERCEPTOR]\n⚡ STRIKE READY | {u:.1f} m/s"
            else:
                ubill.text = f"USV-{i} [{role.upper()}]\nHealth: {h_i:.2f} | {u:.1f} m/s"
            markers.markers.append(ubill)

            # 3.6 Hydrodynamic Stern Wake Trails
            track = self.history_tracks[i]
            if len(track) >= 2:
                wake = Marker()
                wake.header.frame_id = "world"
                wake.header.stamp = now
                wake.ns = "usv_wake"
                wake.id = i
                wake.type = Marker.LINE_STRIP
                wake.action = Marker.ADD
                wake.scale.x = 0.20
                wake.color.r = 0.65
                wake.color.g = 0.85
                wake.color.b = 1.0
                wake.color.a = 0.45
                for wx, wy in track:
                    wp = Point()
                    wp.x = float(wx)
                    wp.y = float(wy)
                    wp.z = 0.02
                    wake.points.append(wp)
                markers.markers.append(wake)

        # =====================================================================
        # 4. DYNAMIC INTER-AGENT GOSSIP MESH LINES
        # =====================================================================
        mesh_lines = Marker()
        mesh_lines.header.frame_id = "world"
        mesh_lines.header.stamp = now
        mesh_lines.ns = "comms_mesh"
        mesh_lines.id = 0
        mesh_lines.type = Marker.LINE_LIST
        mesh_lines.action = Marker.ADD
        mesh_lines.scale.x = 0.12  # Fine beam width
        mesh_lines.color.r = 0.15
        mesh_lines.color.g = 0.85
        mesh_lines.color.b = 0.70
        mesh_lines.color.a = 0.35

        for i in range(self.num_agents):
            for j in range(i + 1, self.num_agents):
                xi, yi = self.usv_states[i][0], self.usv_states[i][1]
                xj, yj = self.usv_states[j][0], self.usv_states[j][1]
                dist = math.hypot(xi - xj, yi - yj)
                if dist <= 55.0:  # Within radio line-of-sight
                    pi = Point()
                    pi.x, pi.y, pi.z = float(xi), float(yi), 0.7
                    pj = Point()
                    pj.x, pj.y, pj.z = float(xj), float(yj), 0.7
                    mesh_lines.points.extend([pi, pj])

        markers.markers.append(mesh_lines)

        self.marker_pub.publish(markers)


def main(args=None):
    rclpy.init(args=args)
    node = USVSimNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
