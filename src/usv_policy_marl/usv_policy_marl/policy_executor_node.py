"""
USV Swarm Simulation - ROS 2 Frozen FA-MAPPO Policy Executor Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Responsibilities:
- Subscribes to local target belief, health belief h_i(t), assigned role, and odometry
- Assembles 35-dimensional local observation vector matching training specification
- Evaluates frozen FA-MAPPO neural network policy (weights from fa_mappo_actor.npz/.pt)
- Logs fleet convergence during spawn -> approach phase
- Emits /usv_{i}/cmd_vel_nominal for the CBF safety shield to filter
"""

from __future__ import annotations
import math
import os
from typing import Dict, List, Optional, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from usv_msgs.msg import TargetBelief, USVHealth, RoleAssignment


class USVPolicyExecutorNode(Node):
    """ROS 2 Node executing the frozen FA-MAPPO policy across the fleet."""

    ROLE_MAP = {"tracker": 0, "interceptor": 1, "relay": 2, "scout": 3}

    def __init__(self):
        super().__init__('usv_policy_executor_node')

        self.declare_parameter('num_agents', 6)
        self.declare_parameter('model_path', '')

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        model_path = self.get_parameter('model_path').get_parameter_value().string_value

        self.get_logger().info(f"Initializing USVPolicyExecutorNode for {self.num_agents} vessels.")

        # Locate and load frozen FA-MAPPO policy weights
        self.policy_weights: Optional[Dict[str, np.ndarray]] = None
        candidates = [
            model_path,
            os.path.join(os.getcwd(), 'training', 'checkpoints', 'fa_mappo_actor.npz'),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'training', 'checkpoints', 'fa_mappo_actor.npz'),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'training', 'checkpoints', 'fa_mappo_actor.npz')
        ]
        for c in candidates:
            if c and os.path.exists(c):
                try:
                    self.policy_weights = dict(np.load(c))
                    self.get_logger().info(f"Loaded frozen FA-MAPPO actor policy from: {c}")
                    break
                except Exception as e:
                    self.get_logger().warning(f"Could not load checkpoint from {c}: {e}")

        if self.policy_weights is None:
            self.get_logger().warning("No FA-MAPPO checkpoint loaded; operating in proportional guidance mode.")

        # State caches
        self.usv_poses: Dict[int, Tuple[float, float, float, float, float, float]] = {}
        self.target_beliefs: Dict[int, Tuple[float, float, float, float]] = {}
        self.health_beliefs: Dict[int, float] = {i: 1.0 for i in range(self.num_agents)}
        self.assigned_roles: Dict[int, str] = {i: "tracker" for i in range(self.num_agents)}
        self.target_slots: Dict[int, Tuple[float, float]] = {}

        # Telemetry & approach logging
        self.sim_step_count = 0

        # Publishers & Subscriptions
        self.cmd_nominal_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.odom_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.belief_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.health_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.role_subs: Dict[int, rclpy.subscription.Subscription] = {}

        for i in range(self.num_agents):
            self.cmd_nominal_pubs[i] = self.create_publisher(Twist, f'/usv_{i}/cmd_vel_nominal', 10)
            self.odom_subs[i] = self.create_subscription(
                Odometry,
                f'/usv_{i}/odom',
                self._make_odom_cb(i),
                10
            )
            self.belief_subs[i] = self.create_subscription(
                TargetBelief,
                f'/usv_{i}/target_belief',
                self._make_belief_cb(i),
                10
            )
            self.health_subs[i] = self.create_subscription(
                USVHealth,
                f'/usv_{i}/health',
                self._make_health_cb(i),
                10
            )
            self.role_subs[i] = self.create_subscription(
                RoleAssignment,
                f'/usv_{i}/role',
                self._make_role_cb(i),
                10
            )

        # 10 Hz Policy Inference Loop
        self.timer = self.create_timer(0.1, self._policy_step)

    def _make_odom_cb(self, agent_id: int):
        def callback(msg: Odometry):
            x = msg.pose.pose.position.x
            y = msg.pose.pose.position.y
            qz = msg.pose.pose.orientation.z
            qw = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(qz, qw)
            u = msg.twist.twist.linear.x
            v = msg.twist.twist.linear.y
            r = msg.twist.twist.angular.z
            self.usv_poses[agent_id] = (x, y, psi, u, v, r)
        return callback

    def _make_belief_cb(self, agent_id: int):
        def callback(msg: TargetBelief):
            self.target_beliefs[agent_id] = (
                msg.position.x,
                msg.position.y,
                msg.velocity.x,
                msg.velocity.y
            )
        return callback

    def _make_health_cb(self, agent_id: int):
        def callback(msg: USVHealth):
            self.health_beliefs[agent_id] = msg.health_index
        return callback

    def _make_role_cb(self, agent_id: int):
        def callback(msg: RoleAssignment):
            self.assigned_roles[agent_id] = msg.role
            self.target_slots[agent_id] = (msg.target_slot.x, msg.target_slot.y)
        return callback

    def _forward_marl_policy(self, obs: np.ndarray) -> Tuple[float, float]:
        """Evaluates frozen 3-layer FA-MAPPO Actor with LayerNorm and Tanh activations."""
        w = self.policy_weights
        h = obs @ w['net.0.weight'].T + w['net.0.bias']
        var = np.maximum(h.var(axis=-1, keepdims=True), 1e-8)
        h = (h - h.mean(axis=-1, keepdims=True)) / np.sqrt(var + 1e-5) * w['net.1.weight'] + w['net.1.bias']
        h = np.tanh(h)

        h = h @ w['net.3.weight'].T + w['net.3.bias']
        var = np.maximum(h.var(axis=-1, keepdims=True), 1e-8)
        h = (h - h.mean(axis=-1, keepdims=True)) / np.sqrt(var + 1e-5) * w['net.4.weight'] + w['net.4.bias']
        h = np.tanh(h)

        mean = h @ w['net.6.weight'].T + w['net.6.bias']
        u_cmd = float(np.tanh(mean[0]) * 1.25 + 1.25)
        r_cmd = float(np.tanh(mean[1]) * 0.8)
        return u_cmd, r_cmd

    def _policy_step(self):
        self.sim_step_count += 1
        t_sec = self.sim_step_count * 0.1

        # Calculate mean approach distance across the fleet
        dists_to_target = []
        for i in range(self.num_agents):
            if i in self.usv_poses and i in self.target_beliefs:
                ax, ay, _, _, _, _ = self.usv_poses[i]
                tx, ty, _, _ = self.target_beliefs[i]
                dists_to_target.append(math.hypot(ax - tx, ay - ty))

        if dists_to_target:
            mean_dist = float(np.mean(dists_to_target))
            # Approach telemetry logged periodically during convergence
            if self.sim_step_count % 15 == 0 and t_sec <= 14.0:
                self.get_logger().info(
                    f"[FA-MAPPO CONVERGENCE] t={t_sec:4.1f}s | Fleet Mean Distance: {mean_dist:5.1f}m -> 20m ring | "
                    f"Active Policy: {'FA-MAPPO (fa_mappo_actor.npz)' if self.policy_weights else 'Proportional Guidance'}"
                )

        # Periodically log per-agent distinct geometric setpoints to verify plumbing
        if self.sim_step_count % 20 == 0:
            setpoints_str = " | ".join([
                f"USV-{aid}({self.assigned_roles.get(aid, '?')}): ({self.target_slots.get(aid, (0.0, 0.0))[0]:.1f}, {self.target_slots.get(aid, (0.0, 0.0))[1]:.1f})"
                for aid in range(self.num_agents)
            ])
            self.get_logger().info(f"[SETPOINTS] {setpoints_str}")

        for i in range(self.num_agents):
            if i not in self.usv_poses:
                continue

            x, y, psi, u, v, r = self.usv_poses[i]
            slot = self.target_slots.get(i)
            role = self.assigned_roles.get(i, "tracker")
            h_i = self.health_beliefs.get(i, 1.0)

            # Target goal point: assigned slot (from Hungarian allocator) or target belief
            if slot is not None:
                gx, gy = slot
            elif i in self.target_beliefs:
                tx, ty, _, _ = self.target_beliefs[i]
                gx, gy = tx, ty
            else:
                gx, gy = x, y

            dx = gx - x
            dy = gy - y
            dist = math.hypot(dx, dy)
            target_heading = math.atan2(dy, dx)
            heading_err = (target_heading - psi + math.pi) % (2 * math.pi) - math.pi

            # Proportional slot speed profile
            if dist > 8.0:
                speed_des = 2.4
            elif dist < 1.0:
                speed_des = max(0.0, 0.5 * dist)
            else:
                speed_des = max(0.3, 2.4 * (dist / 8.0))

            slot_r = float(np.clip(heading_err * 1.8, -0.8, 0.8))

            if self.policy_weights is not None:
                # Assemble 35-dimensional FA-MAPPO observation vector
                tx, ty, tvx, tvy = self.target_beliefs.get(i, (gx, gy, 0.0, 0.0))
                dx_t = tx - x
                dy_t = ty - y
                dist_t = math.hypot(dx_t, dy_t)
                bearing_t = (math.atan2(dy_t, dx_t) - psi + math.pi) % (2 * math.pi) - math.pi

                fault_one_hot = [1.0, 0.0, 0.0, 0.0] if h_i >= 0.60 else [0.0, 0.0, 1.0, 0.0]
                role_one_hot = [0.0, 0.0, 0.0, 0.0]
                role_one_hot[self.ROLE_MAP.get(role.lower(), 3)] = 1.0

                # 4 nearest neighbors
                neighbors = []
                for other_id in range(self.num_agents):
                    if other_id != i and other_id in self.usv_poses:
                        ox, oy, _, _, _, _ = self.usv_poses[other_id]
                        ndist = math.hypot(ox - x, oy - y)
                        neighbors.append((ndist, (ox - x) / 50.0, (oy - y) / 50.0, self.health_beliefs.get(other_id, 1.0)))
                neighbors.sort(key=lambda item: item[0])
                neighbor_features = []
                for k in range(4):
                    if k < len(neighbors):
                        ndist, ndx, ndy, nh = neighbors[k]
                        neighbor_features.extend([ndx, ndy, ndist / 50.0, nh])
                    else:
                        neighbor_features.extend([0.0, 0.0, 2.0, 0.0])

                obs = np.array([
                    dx_t / 50.0, dy_t / 50.0, dist_t / 50.0, bearing_t / math.pi,
                    tvx / 2.0, tvy / 2.0,
                    u / 2.5, v / 1.0, r / 0.8, psi / math.pi,
                    h_i,
                    *fault_one_hot,
                    *role_one_hot,
                    *neighbor_features
                ], dtype=np.float32)

                marl_u, marl_r = self._forward_marl_policy(obs)

                # Blend FA-MAPPO velocity & coordination with geometric slot heading guidance
                if dist < 1.0:
                    u_cmd = float(max(0.0, 0.5 * dist))
                    r_cmd = float(np.clip(heading_err * 0.8, -0.3, 0.3))
                elif dist < 4.0:
                    blend = dist / 4.0
                    u_cmd = float(blend * (0.5 * marl_u + 0.5 * speed_des * min(1.0, h_i * 1.2)) + (1.0 - blend) * max(0.2, 0.6 * dist))
                    r_cmd = float(np.clip(blend * (0.8 * slot_r + 0.2 * marl_r) + (1.0 - blend) * heading_err * 1.0, -0.6, 0.6))
                else:
                    u_cmd = float(0.5 * marl_u + 0.5 * speed_des * min(1.0, h_i * 1.2))
                    r_cmd = float(np.clip(0.8 * slot_r + 0.2 * marl_r, -0.8, 0.8))
            else:
                # Proportional guidance fallback
                u_cmd = float(speed_des * min(1.0, h_i * 1.2))
                r_cmd = slot_r

            # Publish nominal command
            cmd = Twist()
            cmd.linear.x = float(u_cmd)
            cmd.angular.z = float(r_cmd)
            self.cmd_nominal_pubs[i].publish(cmd)


def main(args=None):
    rclpy.init(args=args)
    node = USVPolicyExecutorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
