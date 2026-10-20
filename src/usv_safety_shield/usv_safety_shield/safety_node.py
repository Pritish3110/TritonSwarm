"""
USV Swarm Simulation - ROS 2 Control Barrier Function (CBF) Safety Shield Node
Author: Principal Enterprise Engineer
Stage: 2 (Hard Safety Enforcement, Multi-Target)

Responsibilities:
- Intercepts nominal policy actions on /usv_{i}/cmd_vel_nominal
- Evaluates the tiered barrier set in barriers.py against every hostile and every ally
- Modulates surge speed and injects evasive yaw if separation is threatened
- Publishes collision-free commands to /usv_{i}/cmd_vel
- Invariants:
    * Zero inter-vessel collisions (d >= 4.0 m), never relaxed for any role or phase
    * Non-interceptors hold 14.0 m off EVERY hostile
    * An interceptor's 4.2 m capture corridor applies only to its ASSIGNED hostile
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from usv_msgs.msg import RoleAssignment

from usv_safety_shield.barriers import (
    D_CAPTURE,
    D_EXCLUSION,
    TargetObstacle,
    filter_command,
)


class USVSafetyShieldNode(Node):
    """ROS 2 Node enforcing hard CBF safety invariants across the swarm and all hostiles."""

    def __init__(self):
        super().__init__('usv_safety_shield_node')

        self.declare_parameter('num_agents', 12)
        self.declare_parameter('num_targets', 3)
        self.declare_parameter('d_safe', 4.0)
        self.declare_parameter('d_warning', 8.0)

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.num_targets = max(1, self.get_parameter('num_targets').get_parameter_value().integer_value)
        self.d_safe = self.get_parameter('d_safe').get_parameter_value().double_value
        self.d_warning = self.get_parameter('d_warning').get_parameter_value().double_value

        self.get_logger().info(
            f"Initializing USVSafetyShieldNode for {self.num_agents} vessels and "
            f"{self.num_targets} hostiles. d_safe={self.d_safe}m, d_warning={self.d_warning}m, "
            f"exclusion={D_EXCLUSION}m, capture_corridor={D_CAPTURE}m"
        )

        self.agent_poses: Dict[int, Tuple[float, float, float]] = {}
        self.agent_vels: Dict[int, Tuple[float, float]] = {}
        self.agent_roles: Dict[int, str] = {i: "tracker" for i in range(self.num_agents)}
        self.agent_targets: Dict[int, int] = {i: 0 for i in range(self.num_agents)}
        self.nominal_cmds: Dict[int, Tuple[float, float]] = {i: (0.0, 0.0) for i in range(self.num_agents)}
        self.target_poses: Dict[int, Tuple[float, float]] = {}
        self.target_vels: Dict[int, Tuple[float, float]] = {}

        # Worst-case telemetry for the invariant audit.
        self.worst_ally_sep = float('inf')
        self.worst_envelope = float('inf')
        self.breach_logged = False

        self.odom_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.nominal_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.role_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.safe_pubs: Dict[int, rclpy.publisher.Publisher] = {}

        for i in range(self.num_agents):
            self.odom_subs[i] = self.create_subscription(
                Odometry, f'/usv_{i}/odom', self._make_odom_cb(i), 10
            )
            self.nominal_subs[i] = self.create_subscription(
                Twist, f'/usv_{i}/cmd_vel_nominal', self._make_nominal_cb(i), 10
            )
            self.role_subs[i] = self.create_subscription(
                RoleAssignment, f'/usv_{i}/role', self._make_role_cb(i), 10
            )
            self.safe_pubs[i] = self.create_publisher(Twist, f'/usv_{i}/cmd_vel', 10)

        self.target_subs: Dict[int, rclpy.subscription.Subscription] = {}
        for m in range(self.num_targets):
            self.target_subs[m] = self.create_subscription(
                Odometry, f'/target_{m}/odom', self._make_target_cb(m), 10
            )

        # 20 Hz CBF Evaluation Loop
        self.timer = self.create_timer(0.05, self._cbf_step)

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    def _make_target_cb(self, target_id: int):
        def callback(msg: Odometry):
            self.target_poses[target_id] = (
                msg.pose.pose.position.x, msg.pose.pose.position.y
            )
            # Odometry carries body-frame surge; the barrier needs world-frame velocity so it
            # can tell a hostile running the vessel down from one opening the range.
            qz = msg.pose.pose.orientation.z
            qw = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(qz, qw)
            speed = msg.twist.twist.linear.x
            self.target_vels[target_id] = (speed * math.cos(psi), speed * math.sin(psi))
        return callback

    def _make_role_cb(self, agent_id: int):
        def callback(msg: RoleAssignment):
            self.agent_roles[agent_id] = msg.role.lower()
            self.agent_targets[agent_id] = int(msg.assigned_target_id)
        return callback

    def _make_odom_cb(self, agent_id: int):
        def callback(msg: Odometry):
            x = msg.pose.pose.position.x
            y = msg.pose.pose.position.y
            qz = msg.pose.pose.orientation.z
            qw = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(qz, qw)
            self.agent_poses[agent_id] = (x, y, psi)
            surge = msg.twist.twist.linear.x
            self.agent_vels[agent_id] = (surge * math.cos(psi), surge * math.sin(psi))
        return callback

    def _make_nominal_cb(self, agent_id: int):
        def callback(msg: Twist):
            self.nominal_cmds[agent_id] = (msg.linear.x, msg.angular.z)
        return callback

    # ------------------------------------------------------------------
    # Barrier evaluation
    # ------------------------------------------------------------------
    def _cbf_step(self):
        obstacles: List[TargetObstacle] = [
            TargetObstacle(
                target_id=m, x=pos[0], y=pos[1],
                vx=self.target_vels.get(m, (0.0, 0.0))[0],
                vy=self.target_vels.get(m, (0.0, 0.0))[1],
            )
            for m, pos in sorted(self.target_poses.items())
        ]

        for i in range(self.num_agents):
            if i not in self.agent_poses:
                continue

            ax, ay, apsi = self.agent_poses[i]
            role = self.agent_roles.get(i, "tracker")
            assigned = self.agent_targets.get(i, 0)
            u_nom, r_nom = self.nominal_cmds[i]

            allies = {
                j: (self.agent_poses[j][0], self.agent_poses[j][1])
                for j in range(self.num_agents)
                if j != i and j in self.agent_poses
            }
            ally_vels = {
                j: self.agent_vels.get(j, (0.0, 0.0))
                for j in allies
            }

            result = filter_command(
                own_pos=(ax, ay),
                own_psi=apsi,
                u_nominal=u_nom,
                r_nominal=r_nom,
                role=role,
                assigned_target_id=assigned,
                targets=obstacles,
                ally_positions=allies,
                ally_velocities=ally_vels,
                d_safe=self.d_safe,
                d_warning=self.d_warning,
            )

            if result.min_ally_dist != float('inf'):
                self.worst_ally_sep = min(self.worst_ally_sep, result.min_ally_dist)
            if result.min_target_dist != float('inf'):
                self.worst_envelope = min(self.worst_envelope, result.min_target_dist)

            if result.min_ally_dist < self.d_safe and not self.breach_logged:
                self.breach_logged = True
                self.get_logger().error(
                    f"[INVARIANT BREACH] USV-{i} inter-ally separation "
                    f"{result.min_ally_dist:.2f}m < d_safe {self.d_safe:.1f}m"
                )

            if result.interventions:
                self.get_logger().info(
                    f"[CBF] USV-{i}({role}->T{assigned}) u: {u_nom:.2f}->{result.u_safe:.2f}, "
                    f"r: {r_nom:.2f}->{result.r_safe:.2f} | "
                    f"ally {result.min_ally_dist:.1f}m tgt {result.min_target_dist:.1f}m | "
                    f"{','.join(result.interventions[:3])}",
                    throttle_duration_sec=2.0
                )

            cmd = Twist()
            cmd.linear.x = float(result.u_safe)
            cmd.angular.z = float(result.r_safe)
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
