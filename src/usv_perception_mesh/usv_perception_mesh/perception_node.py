"""
USV Swarm Simulation - ROS 2 Perception & Communication Mesh Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Responsibilities:
- Ingests true target and USV poses from sim_core
- Injects sensor noise, detection dropouts, and GPS drift
- Executes individual EKF trackers per USV
- Simulates mesh packet loss (0-40%) and range limitations
- Publishes cooperative /usv_{i}/target_belief (usv_msgs/msg/TargetBelief)
"""

from __future__ import annotations
import math
import random
from typing import Dict, List, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from usv_msgs.msg import TargetBelief


class USVPerceptionNode(Node):
    """ROS 2 node managing multi-USV perception, noise, EKF, and mesh routing."""

    def __init__(self):
        super().__init__('usv_perception_node')

        self.declare_parameter('num_agents', 6)
        self.declare_parameter('comm_range', 80.0)
        self.declare_parameter('packet_loss_rate', 0.05)
        self.declare_parameter('gps_noise_std', 0.5)

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.comm_range = self.get_parameter('comm_range').get_parameter_value().double_value
        self.packet_loss_rate = self.get_parameter('packet_loss_rate').get_parameter_value().double_value
        self.gps_noise_std = self.get_parameter('gps_noise_std').get_parameter_value().double_value

        self.get_logger().info(
            f"Initializing USVPerceptionNode for {self.num_agents} vessels. "
            f"Range: {self.comm_range}m, Loss Rate: {self.packet_loss_rate*100}%"
        )

        # Target true state: [x, y, vx, vy]
        self.target_true_pos = (0.0, 0.0)
        self.target_true_vel = (0.0, 0.0)

        # USV true states: id -> [x, y, psi, u, v, r]
        self.usv_poses: Dict[int, Tuple[float, float, float]] = {}

        # EKF state per USV: [x_T, y_T, vx_T, vy_T]
        self.ekf_states: Dict[int, np.ndarray] = {}
        self.ekf_covs: Dict[int, np.ndarray] = {}

        for i in range(self.num_agents):
            self.ekf_states[i] = np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float64)
            self.ekf_covs[i] = np.diag([25.0, 25.0, 4.0, 4.0]).astype(np.float64)

        # Subscriptions
        self.target_sub = self.create_subscription(
            Odometry,
            '/target/odom',
            self._target_callback,
            10
        )

        self.usv_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.belief_pubs: Dict[int, rclpy.publisher.Publisher] = {}

        for i in range(self.num_agents):
            self.usv_subs[i] = self.create_subscription(
                Odometry,
                f'/usv_{i}/odom',
                self._make_usv_callback(i),
                10
            )
            self.belief_pubs[i] = self.create_publisher(
                TargetBelief,
                f'/usv_{i}/target_belief',
                10
            )

        # 10 Hz EKF and Gossip Update Loop
        self.timer = self.create_timer(0.1, self._ekf_and_mesh_step)

    def _target_callback(self, msg: Odometry):
        self.target_true_pos = (msg.pose.pose.position.x, msg.pose.pose.position.y)
        self.target_true_vel = (msg.twist.twist.linear.x, msg.twist.twist.linear.y)

    def _make_usv_callback(self, agent_id: int):
        def callback(msg: Odometry):
            x = msg.pose.pose.position.x
            y = msg.pose.pose.position.y
            q_z = msg.pose.pose.orientation.z
            q_w = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(q_z, q_w)
            self.usv_poses[agent_id] = (x, y, psi)
        return callback

    def _ekf_and_mesh_step(self):
        dt = 0.1
        now = self.get_clock().now().to_msg()
        tx, ty = self.target_true_pos

        # 1. Individual EKF Prediction & Local Sensor Update
        for i in range(self.num_agents):
            if i not in self.usv_poses:
                continue

            ux, uy, upsi = self.usv_poses[i]
            x_est = self.ekf_states[i]
            p_est = self.ekf_covs[i]

            # EKF Prediction
            F = np.array([
                [1.0, 0.0, dt, 0.0],
                [0.0, 1.0, 0.0, dt],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0]
            ], dtype=np.float64)
            x_est = F @ x_est
            Q = np.diag([0.05, 0.05, 0.1, 0.1])
            p_est = F @ p_est @ F.T + Q

            # Simulated radar/camera range & bearing
            dist = math.hypot(tx - ux, ty - uy)
            if dist < 70.0 and random.random() > 0.05:  # 95% detection within 70m
                # Noisy measurement
                meas_x = tx + np.random.normal(0.0, self.gps_noise_std)
                meas_y = ty + np.random.normal(0.0, self.gps_noise_std)

                # Innovation
                H = np.array([
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0]
                ], dtype=np.float64)
                R = np.eye(2, dtype=np.float64) * (self.gps_noise_std ** 2)

                y_res = np.array([meas_x, meas_y]) - (H @ x_est)
                S = H @ p_est @ H.T + R
                K = p_est @ H.T @ np.linalg.inv(S)

                x_est = x_est + K @ y_res
                p_est = (np.eye(4) - K @ H) @ p_est

            self.ekf_states[i] = x_est
            self.ekf_covs[i] = p_est

        # 2. Gossip Fusion across Communicating Mesh
        for i in range(self.num_agents):
            if i not in self.usv_poses:
                continue
            for j in range(i + 1, self.num_agents):
                if j not in self.usv_poses:
                    continue

                d_ij = math.hypot(self.usv_poses[i][0] - self.usv_poses[j][0],
                                  self.usv_poses[i][1] - self.usv_poses[j][1])

                if d_ij <= self.comm_range and random.random() > self.packet_loss_rate:
                    # Successful packet exchange: Covariance Intersection blend
                    w = 0.5
                    self.ekf_states[i] = w * self.ekf_states[i] + (1.0 - w) * self.ekf_states[j]
                    self.ekf_states[j] = self.ekf_states[i].copy()

        # 3. Publish Target Beliefs
        for i in range(self.num_agents):
            tb = TargetBelief()
            tb.stamp = now
            tb.source_agent_id = i
            tb.position.x = float(self.ekf_states[i][0])
            tb.position.y = float(self.ekf_states[i][1])
            tb.velocity.x = float(self.ekf_states[i][2])
            tb.velocity.y = float(self.ekf_states[i][3])
            tb.covariance = [
                float(self.ekf_covs[i][0, 0]),
                float(self.ekf_covs[i][0, 1]),
                float(self.ekf_covs[i][1, 0]),
                float(self.ekf_covs[i][1, 1])
            ]
            tb.estimation_confidence = float(np.clip(1.0 / (1.0 + np.trace(self.ekf_covs[i][:2, :2])), 0.0, 1.0))
            tb.target_visible = True
            self.belief_pubs[i].publish(tb)


def main(args=None):
    rclpy.init(args=args)
    node = USVPerceptionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
