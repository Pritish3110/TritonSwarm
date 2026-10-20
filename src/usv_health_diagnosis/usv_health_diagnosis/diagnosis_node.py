"""
USV Swarm Simulation - ROS 2 Health Monitoring & Fault Diagnosis Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Responsibilities:
- Ingests USV odometry and commanded velocities
- Computes temporal sliding-window telemetry residuals (mean, variance, trend)
- Runs frozen XGBoost classifier (or heuristic fallback)
- Publishes /usv_{i}/health (usv_msgs/msg/USVHealth) containing h_i(t)
"""

from __future__ import annotations
import math
import os
from collections import deque
from typing import Dict, List, Optional, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
from usv_msgs.msg import USVHealth


class USVDiagnosisNode(Node):
    """ROS 2 Node for real-time fault diagnosis and health belief estimation."""

    CLASS_NAMES = ["HEALTHY", "GPS_FAULT", "THRUSTER_FAULT", "LINK_LOSS"]

    def __init__(self):
        super().__init__('usv_diagnosis_node')

        self.declare_parameter('num_agents', 6)
        self.declare_parameter('model_path', '')
        self.declare_parameter('window_size', 10)

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        model_path_param = self.get_parameter('model_path').get_parameter_value().string_value
        self.window_size = self.get_parameter('window_size').get_parameter_value().integer_value

        # Locate model checkpoint
        self.model = None
        if not model_path_param:
            candidates = [
                os.path.join(os.getcwd(), 'training', 'checkpoints', 'xgb_diagnosis.json'),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'training', 'checkpoints', 'xgb_diagnosis.json'),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'training', 'checkpoints', 'xgb_diagnosis.json')
            ]
            for c in candidates:
                if os.path.exists(c):
                    model_path_param = c
                    break

        self.model = None
        self.json_trees = None
        self.json_tree_info = None

        if model_path_param and os.path.exists(model_path_param):
            try:
                import xgboost as xgb
                self.model = xgb.XGBClassifier()
                self.model.load_model(model_path_param)
                self.get_logger().info(f"Loaded frozen XGBoost model via xgboost library from: {model_path_param}")
            except Exception as e:
                try:
                    import json
                    with open(model_path_param, 'r') as f:
                        m_data = json.load(f)
                    self.json_trees = m_data["learner"]["gradient_booster"]["model"]["trees"]
                    self.json_tree_info = m_data["learner"]["gradient_booster"]["model"]["tree_info"]
                    self.get_logger().info(f"Loaded frozen XGBoost model via native JSON tree evaluator ({len(self.json_trees)} trees) from: {model_path_param}")
                except Exception as e2:
                    self.get_logger().warning(f"Failed to load XGBoost model ({e} / {e2}), using heuristic diagnosis.")
        else:
            self.get_logger().info("No model checkpoint provided; operating in heuristic diagnosis mode.")

        # Telemetry sliding windows per agent
        self.telemetry_buffers: Dict[int, deque] = {i: deque(maxlen=self.window_size) for i in range(self.num_agents)}
        self.latest_cmds: Dict[int, Tuple[float, float]] = {i: (0.0, 0.0) for i in range(self.num_agents)}
        self.prev_poses: Dict[int, Tuple[float, float, float]] = {}
        self.diag_step_count: int = 0

        # Publishers & Subscriptions
        self.health_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.odom_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.cmd_subs: Dict[int, rclpy.subscription.Subscription] = {}

        for i in range(self.num_agents):
            self.health_pubs[i] = self.create_publisher(USVHealth, f'/usv_{i}/health', 10)
            self.odom_subs[i] = self.create_subscription(
                Odometry,
                f'/usv_{i}/odom',
                self._make_odom_callback(i),
                10
            )
            self.cmd_subs[i] = self.create_subscription(
                Twist,
                f'/usv_{i}/cmd_vel',
                self._make_cmd_callback(i),
                10
            )

        # 10 Hz Diagnosis timer
        self.timer = self.create_timer(0.1, self._diagnosis_step)

    def _make_cmd_callback(self, agent_id: int):
        def callback(msg: Twist):
            self.latest_cmds[agent_id] = (msg.linear.x, msg.angular.z)
        return callback

    def _make_odom_callback(self, agent_id: int):
        def callback(msg: Odometry):
            x = msg.pose.pose.position.x
            y = msg.pose.pose.position.y
            q_z = msg.pose.pose.orientation.z
            q_w = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(q_z, q_w)
            u = msg.twist.twist.linear.x
            r = msg.twist.twist.angular.z

            u_cmd, r_cmd = self.latest_cmds[agent_id]
            dt = 0.05

            if agent_id in self.prev_poses:
                px, py, ppsi = self.prev_poses[agent_id]
                expected_x = px + u_cmd * math.cos(ppsi) * dt
                expected_y = py + u_cmd * math.sin(ppsi) * dt
                pos_res = math.hypot(x - expected_x, y - expected_y)
            else:
                pos_res = 0.0

            self.prev_poses[agent_id] = (x, y, psi)
            u_err = abs(u_cmd - u)
            r_err = abs(r_cmd - r)

            self.telemetry_buffers[agent_id].append({
                'pos_res': pos_res,
                'u_err': u_err,
                'r_err': r_err,
                'u_meas': u
            })
        return callback

    def _diagnosis_step(self):
        self.diag_step_count += 1
        now = self.get_clock().now().to_msg()

        for i in range(self.num_agents):
            buf = self.telemetry_buffers[i]
            if len(buf) < 5 or self.diag_step_count < 30:
                # Default healthy state while filling buffer and during 3.0s initial acceleration
                pred_class = 0
                confidence = 0.99
                h_i = 1.0
                mean_u_err = 0.0
            else:
                pos_res = [s['pos_res'] for s in buf]
                u_err = [s['u_err'] for s in buf]
                r_err = [s['r_err'] for s in buf]

                mean_pos_res = float(np.mean(pos_res))
                mean_u_err = float(np.mean(u_err))
                mean_r_err = float(np.mean(r_err))

                features = np.array([
                    mean_pos_res, float(np.var(pos_res)), float(np.max(pos_res)), float(pos_res[-1] - pos_res[0]),
                    mean_u_err, float(np.var(u_err)), float(np.max(u_err)),
                    mean_r_err, float(np.var(r_err)),
                    0.02, 0.05, 0.0001, 0.98, buf[-1]['u_meas']
                ], dtype=np.float32).reshape(1, -1)

                probs = None
                if self.model is not None:
                    try:
                        probs = self.model.predict_proba(features)[0]
                    except Exception:
                        pass

                if probs is None and self.json_trees is not None:
                    try:
                        raw_margins = np.zeros(4, dtype=np.float64)
                        for tree, cls_id in zip(self.json_trees, self.json_tree_info):
                            left = tree["left_children"]
                            right = tree["right_children"]
                            s_idx = tree["split_indices"]
                            s_cond = tree["split_conditions"]
                            weights = tree["base_weights"]
                            nid = 0
                            while left[nid] != -1:
                                val = features[0, s_idx[nid]]
                                thresh = s_cond[nid]
                                nid = left[nid] if val < thresh else right[nid]
                            raw_margins[cls_id] += weights[nid]
                        exp_m = np.exp(raw_margins - np.max(raw_margins))
                        probs = exp_m / np.sum(exp_m)
                    except Exception:
                        probs = None

                if probs is not None:
                    pred_class = int(np.argmax(probs))
                    confidence = float(probs[pred_class])
                    # Kinematic residual validation: surge velocity error > 0.6 m/s AND speed capped below 0.95 m/s confirms actuator failure
                    if mean_u_err > 0.6 and buf[-1]['u_meas'] < 0.95:
                        pred_class = 2
                        confidence = max(confidence, 0.95)
                    elif mean_pos_res > 3.0 and float(np.var(pos_res)) > 0.3:
                        pred_class = 1
                        confidence = max(confidence, 0.90)

                    thrust_factor = max(0.2, 1.0 - min(1.0, mean_u_err / 2.0))
                    if pred_class == 2:
                        h_i = float(np.clip(thrust_factor, 0.05, 0.45))
                    elif pred_class == 1:
                        h_i = 0.50
                    elif pred_class == 3:
                        h_i = 0.40
                    else:
                        h_i = float(np.clip(probs[0], 0.85, 1.0))
                else:
                    pred_class, confidence, h_i = self._heuristic(mean_pos_res, mean_u_err)

            # Publish USVHealth
            health_msg = USVHealth()
            health_msg.stamp = now
            health_msg.agent_id = i
            health_msg.health_index = float(h_i)
            health_msg.fault_class = pred_class
            health_msg.fault_confidence = float(confidence)
            health_msg.fault_description = self.CLASS_NAMES[pred_class]
            health_msg.thrust_efficiency = float(max(0.2, 1.0 - min(1.0, mean_u_err / 2.0))) if len(buf) >= 3 else 1.0
            health_msg.battery_level = 0.95
            health_msg.packet_loss_rate = 0.05
            self.health_pubs[i].publish(health_msg)

    def _heuristic(self, mean_pos_res: float, mean_u_err: float) -> Tuple[int, float, float]:
        if mean_u_err > 0.8:
            return 2, 0.88, 0.35  # THRUSTER_FAULT
        elif mean_pos_res > 1.5:
            return 1, 0.85, 0.50  # GPS_FAULT
        else:
            return 0, 0.95, 1.0   # HEALTHY


def main(args=None):
    rclpy.init(args=args)
    node = USVDiagnosisNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
