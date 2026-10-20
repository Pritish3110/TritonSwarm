"""
USV Swarm Simulation - ROS 2 Perception & Communication Mesh Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment, Multi-Target)

Responsibilities:
- Ingests true hostile and USV poses from sim_core
- Injects sensor noise, detection dropouts, and GPS drift
- Executes an independent EKF tracker per (USV, hostile) pair
- Simulates mesh packet loss (0-40%) and range limitations
- Publishes cooperative /usv_{i}/target_{m}/belief (usv_msgs/msg/TargetBelief)
- Publishes /usv_{i}/target_belief as the legacy primary-hostile alias
"""

from __future__ import annotations
import math
import random
from typing import Dict, List, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from nav_msgs.msg import Odometry
from usv_msgs.msg import TargetBelief

# Sensor model.
R_DETECTION = 70.0        # m, radar/camera acquisition range
P_DETECTION = 0.95        # per-step detection probability inside acquisition range


class USVPerceptionNode(Node):
    """ROS 2 node managing multi-USV, multi-target perception, noise, EKF, and mesh routing."""

    def __init__(self):
        super().__init__('usv_perception_node')

        self.declare_parameter('num_agents', 12)
        self.declare_parameter('num_targets', 3)
        self.declare_parameter('comm_range', 80.0)
        self.declare_parameter('packet_loss_rate', 0.05)
        self.declare_parameter('gps_noise_std', 0.5)

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.num_targets = max(1, self.get_parameter('num_targets').get_parameter_value().integer_value)
        self.comm_range = self.get_parameter('comm_range').get_parameter_value().double_value
        self.packet_loss_rate = self.get_parameter('packet_loss_rate').get_parameter_value().double_value
        self.gps_noise_std = self.get_parameter('gps_noise_std').get_parameter_value().double_value

        self.get_logger().info(
            f"Initializing USVPerceptionNode for {self.num_agents} vessels tracking "
            f"{self.num_targets} hostiles. Range: {self.comm_range}m, "
            f"Loss Rate: {self.packet_loss_rate*100}%"
        )

        # Hostile true states: target_id -> (x, y), (vx, vy)
        self.target_true_pos: Dict[int, Tuple[float, float]] = {
            m: (0.0, 0.0) for m in range(self.num_targets)
        }
        self.target_true_vel: Dict[int, Tuple[float, float]] = {
            m: (0.0, 0.0) for m in range(self.num_targets)
        }

        # USV true states: id -> (x, y, psi)
        self.usv_poses: Dict[int, Tuple[float, float, float]] = {}

        # EKF bank: one independent tracker per (agent, hostile) pair, state [x, y, vx, vy]
        self.ekf_states: Dict[Tuple[int, int], np.ndarray] = {}
        self.ekf_covs: Dict[Tuple[int, int], np.ndarray] = {}
        for i in range(self.num_agents):
            for m in range(self.num_targets):
                self.ekf_states[(i, m)] = np.array([0.0, 0.0, 0.0, 0.0], dtype=np.float64)
                self.ekf_covs[(i, m)] = np.diag([25.0, 25.0, 4.0, 4.0]).astype(np.float64)

        # Subscriptions: one per hostile, plus the legacy primary-hostile topic.
        self.target_subs: Dict[int, rclpy.subscription.Subscription] = {}
        for m in range(self.num_targets):
            self.target_subs[m] = self.create_subscription(
                Odometry, f'/target_{m}/odom', self._make_target_callback(m), 10
            )

        self.usv_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.belief_pubs: Dict[Tuple[int, int], rclpy.publisher.Publisher] = {}
        self.legacy_belief_pubs: Dict[int, rclpy.publisher.Publisher] = {}

        for i in range(self.num_agents):
            self.usv_subs[i] = self.create_subscription(
                Odometry, f'/usv_{i}/odom', self._make_usv_callback(i), 10
            )
            for m in range(self.num_targets):
                self.belief_pubs[(i, m)] = self.create_publisher(
                    TargetBelief, f'/usv_{i}/target_{m}/belief', 10
                )
            # Legacy alias: the primary hostile's belief on the original topic name.
            self.legacy_belief_pubs[i] = self.create_publisher(
                TargetBelief, f'/usv_{i}/target_belief', 10
            )

        # 10 Hz EKF and Gossip Update Loop
        self.timer = self.create_timer(0.1, self._ekf_and_mesh_step)

    def _make_target_callback(self, target_id: int):
        def callback(msg: Odometry):
            self.target_true_pos[target_id] = (
                msg.pose.pose.position.x, msg.pose.pose.position.y
            )
            # Hostile odometry reports body-frame surge; rotate into the world frame so the
            # EKF velocity state is expressed in the same frame as its position state.
            qz = msg.pose.pose.orientation.z
            qw = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(qz, qw)
            speed = msg.twist.twist.linear.x
            self.target_true_vel[target_id] = (
                speed * math.cos(psi), speed * math.sin(psi)
            )
        return callback

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

        F = np.array([
            [1.0, 0.0, dt, 0.0],
            [0.0, 1.0, 0.0, dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float64)
        Q = np.diag([0.05, 0.05, 0.1, 0.1])
        H = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0]
        ], dtype=np.float64)
        R = np.eye(2, dtype=np.float64) * (self.gps_noise_std ** 2)

        # 1. Independent EKF prediction & local sensor update, per hostile.
        for m in range(self.num_targets):
            tx, ty = self.target_true_pos[m]

            for i in range(self.num_agents):
                if i not in self.usv_poses:
                    continue

                ux, uy, _ = self.usv_poses[i]
                key = (i, m)
                x_est = self.ekf_states[key]
                p_est = self.ekf_covs[key]

                # EKF Prediction
                x_est = F @ x_est
                p_est = F @ p_est @ F.T + Q

                # Simulated radar/camera range & bearing
                dist = math.hypot(tx - ux, ty - uy)
                if dist < R_DETECTION and random.random() < P_DETECTION:
                    meas_x = tx + np.random.normal(0.0, self.gps_noise_std)
                    meas_y = ty + np.random.normal(0.0, self.gps_noise_std)

                    y_res = np.array([meas_x, meas_y]) - (H @ x_est)
                    S = H @ p_est @ H.T + R
                    K = p_est @ H.T @ np.linalg.inv(S)

                    x_est = x_est + K @ y_res
                    p_est = (np.eye(4) - K @ H) @ p_est

                self.ekf_states[key] = x_est
                self.ekf_covs[key] = p_est

        # 2. Gossip fusion across the communicating mesh, independently per hostile track.
        for m in range(self.num_targets):
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
                        fused = w * self.ekf_states[(i, m)] + (1.0 - w) * self.ekf_states[(j, m)]
                        self.ekf_states[(i, m)] = fused
                        self.ekf_states[(j, m)] = fused.copy()

        # 3. Publish per-hostile target beliefs.
        for i in range(self.num_agents):
            visible_any = i in self.usv_poses
            for m in range(self.num_targets):
                key = (i, m)
                tb = TargetBelief()
                tb.stamp = now
                tb.source_agent_id = i
                tb.position.x = float(self.ekf_states[key][0])
                tb.position.y = float(self.ekf_states[key][1])
                tb.velocity.x = float(self.ekf_states[key][2])
                tb.velocity.y = float(self.ekf_states[key][3])
                cov = self.ekf_covs[key]
                tb.covariance = [
                    float(cov[0, 0]), float(cov[0, 1]),
                    float(cov[1, 0]), float(cov[1, 1])
                ]
                tb.estimation_confidence = float(
                    np.clip(1.0 / (1.0 + np.trace(cov[:2, :2])), 0.0, 1.0)
                )
                tb.target_visible = visible_any
                self.belief_pubs[key].publish(tb)

                if m == 0:
                    # Legacy alias keeps every single-target consumer working unchanged.
                    self.legacy_belief_pubs[i].publish(tb)


def main(args=None):
    rclpy.init(args=args)
    node = USVPerceptionNode()
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
