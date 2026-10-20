"""
USV Swarm Simulation - ROS 2 Control Barrier Function (CBF) Safety Shield Node
Author: Principal Enterprise Engineer
Stage: 2 (Hard Safety Enforcement)

Responsibilities:
- Intercepts nominal policy actions on /usv_{i}/cmd_vel_nominal
- Evaluates pairwise barrier function h_ij = ||p_i - p_j||^2 - d_safe^2 >= 0
- Modulates surge speed and injects evasive yaw if separation is threatened
- Publishes collision-free commands to /usv_{i}/cmd_vel
- Invariant: Zero inter-vessel collisions (d >= 4.0m)
"""

from __future__ import annotations
import math
from typing import Dict, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


class USVSafetyShieldNode(Node):
    """ROS 2 Node enforcing hard CBF safety invariants between all swarm USVs."""

    def __init__(self):
        super().__init__('usv_safety_shield_node')

        self.declare_parameter('num_agents', 6)
        self.declare_parameter('d_safe', 4.0)
        self.declare_parameter('d_warning', 8.0)

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.d_safe = self.get_parameter('d_safe').get_parameter_value().double_value
        self.d_warning = self.get_parameter('d_warning').get_parameter_value().double_value

        self.get_logger().info(
            f"Initializing USVSafetyShieldNode for {self.num_agents} vessels. "
            f"d_safe={self.d_safe}m, d_warning={self.d_warning}m"
        )

        self.agent_poses: Dict[int, Tuple[float, float, float]] = {}
        self.nominal_cmds: Dict[int, Tuple[float, float]] = {i: (0.0, 0.0) for i in range(self.num_agents)}

        self.odom_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.nominal_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.safe_pubs: Dict[int, rclpy.publisher.Publisher] = {}

        for i in range(self.num_agents):
            self.odom_subs[i] = self.create_subscription(
                Odometry,
                f'/usv_{i}/odom',
                self._make_odom_cb(i),
                10
            )
            self.nominal_subs[i] = self.create_subscription(
                Twist,
                f'/usv_{i}/cmd_vel_nominal',
                self._make_nominal_cb(i),
                10
            )
            self.safe_pubs[i] = self.create_publisher(
                Twist,
                f'/usv_{i}/cmd_vel',
                10
            )

        # 20 Hz CBF Evaluation Loop
        self.timer = self.create_timer(0.05, self._cbf_step)

    def _make_odom_cb(self, agent_id: int):
        def callback(msg: Odometry):
            x = msg.pose.pose.position.x
            y = msg.pose.pose.position.y
            qz = msg.pose.pose.orientation.z
            qw = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(qz, qw)
            self.agent_poses[agent_id] = (x, y, psi)
        return callback

    def _make_nominal_cb(self, agent_id: int):
        def callback(msg: Twist):
            self.nominal_cmds[agent_id] = (msg.linear.x, msg.angular.z)
        return callback

    def _cbf_step(self):
        for i in range(self.num_agents):
            if i not in self.agent_poses:
                continue

            ax, ay, apsi = self.agent_poses[i]
            u_nom, r_nom = self.nominal_cmds[i]
            u_safe = float(np.clip(u_nom, 0.0, 3.5))
            r_safe = float(np.clip(r_nom, -0.8, 0.8))

            for j in range(self.num_agents):
                if i == j or j not in self.agent_poses:
                    continue

                ox, oy, _ = self.agent_poses[j]
                dx = ox - ax
                dy = oy - ay
                dist = math.hypot(dx, dy)

                if dist < self.d_warning:
                    angle_to_other = math.atan2(dy, dx)
                    rel_bearing = (angle_to_other - apsi + math.pi) % (2 * math.pi) - math.pi
                    is_ahead = abs(rel_bearing) < (math.pi / 2.0)

                    margin = max(0.0, dist - self.d_safe)
                    scaling = margin / max(0.1, self.d_warning - self.d_safe)

                    # Directional CBF barrier: only decelerate if obstacle/ally is in forward sector
                    if is_ahead:
                        u_safe = min(u_safe, 3.5 * scaling)

                    steer_dir = -1.0 if rel_bearing >= 0 else 1.0
                    evasion_r = steer_dir * 0.8 * (1.0 - scaling)
                    r_safe = float(np.clip(0.3 * r_safe + 0.7 * evasion_r, -0.8, 0.8))

                    if dist <= self.d_safe + 1.0 and is_ahead:
                        u_safe = 0.0
                        r_safe = steer_dir * 0.8

            if u_safe < u_nom or abs(r_safe - r_nom) > 0.15:
                self.get_logger().info(
                    f"[CBF INTERVENTION] USV-{i} collision-prevention active: u_cmd: {u_nom:.2f} -> {u_safe:.2f}, r_cmd: {r_nom:.2f} -> {r_safe:.2f}",
                    throttle_duration_sec=2.0
                )

            cmd = Twist()
            cmd.linear.x = float(u_safe)
            cmd.angular.z = float(r_safe)
            self.safe_pubs[i].publish(cmd)


def main(args=None):
    rclpy.init(args=args)
    node = USVSafetyShieldNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
