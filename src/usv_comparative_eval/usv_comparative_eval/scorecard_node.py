"""
USV Swarm Simulation - ROS 2 Impartial Comparative Scorecard Node
Author: Principal Enterprise Engineer
Stage: 2 (Architecture Ablation Study)

Responsibilities:
- Runs IDENTICALLY alongside both the full TritonSwarm stack and the ablated baseline swarm
- Subscribes only to ground-truth kinematics, ground-truth health, published roles and
  published beliefs, so neither architecture can influence how it is judged
- Accumulates the comparative failure-mode metrics in failure_metrics.SwarmScorecard
- Prints a live scorecard block and a single machine-parseable [SCORECARD] line per period,
  which run_comparison.sh extracts to build the side-by-side table

Standardized ROS 2 Interfaces (all read-only):
- Subscribes: /usv_{i}/odom, /usv_{i}/health, /usv_{i}/role
- Subscribes: /target_{m}/odom, /target_{m}/defuse_status
- Subscribes: /usv_{i}/target_{m}/belief
"""

from __future__ import annotations
import math
from typing import Dict, Optional, Tuple

import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32
from usv_msgs.msg import RoleAssignment, TargetBelief, USVHealth

from usv_comparative_eval.failure_metrics import (
    AgentSample,
    SwarmScorecard,
    TargetSample,
)


class USVScorecardNode(Node):
    """Impartial comparative scorer for the architecture ablation study."""

    def __init__(self):
        super().__init__('usv_scorecard_node')

        self.declare_parameter('architecture', 'FULL_ARCHITECTURE')
        self.declare_parameter('num_agents', 12)
        self.declare_parameter('num_targets', 3)
        self.declare_parameter('score_period', 10.0)
        self.declare_parameter('d_safe', 4.0)

        self.architecture = self.get_parameter('architecture').get_parameter_value().string_value
        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.num_targets = max(1, self.get_parameter('num_targets').get_parameter_value().integer_value)
        self.score_period = self.get_parameter('score_period').get_parameter_value().double_value
        self.d_safe = self.get_parameter('d_safe').get_parameter_value().double_value

        self.get_logger().info(
            f"Initializing USVScorecardNode: architecture='{self.architecture}', "
            f"{self.num_agents} vessels vs {self.num_targets} hostiles, "
            f"reporting every {self.score_period:.1f}s"
        )

        self.scorecard = SwarmScorecard(
            architecture=self.architecture,
            num_agents=self.num_agents,
            num_targets=self.num_targets,
            d_safe=self.d_safe,
        )

        # Ground-truth caches
        self.agent_pos: Dict[int, Tuple[float, float]] = {}
        self.agent_speed: Dict[int, float] = {}
        self.agent_health: Dict[int, float] = {i: 1.0 for i in range(self.num_agents)}
        self.agent_role: Dict[int, str] = {i: "tracker" for i in range(self.num_agents)}
        self.agent_target: Dict[int, int] = {i: 0 for i in range(self.num_agents)}

        self.target_pos: Dict[int, Tuple[float, float]] = {}
        self.target_speed: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.target_defusal: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.target_neutralized: Dict[int, bool] = {m: False for m in range(self.num_targets)}

        self.beliefs: Dict[Tuple[int, int], Tuple[float, float]] = {}

        for i in range(self.num_agents):
            self.create_subscription(Odometry, f'/usv_{i}/odom', self._make_odom_cb(i), 10)
            self.create_subscription(USVHealth, f'/usv_{i}/health', self._make_health_cb(i), 10)
            self.create_subscription(RoleAssignment, f'/usv_{i}/role', self._make_role_cb(i), 10)
            for m in range(self.num_targets):
                self.create_subscription(
                    TargetBelief, f'/usv_{i}/target_{m}/belief',
                    self._make_belief_cb(i, m), 10
                )

        for m in range(self.num_targets):
            self.create_subscription(Odometry, f'/target_{m}/odom', self._make_target_cb(m), 10)
            self.create_subscription(
                Float32, f'/target_{m}/defuse_status', self._make_defuse_cb(m), 10
            )

        self.elapsed_since_report = 0.0
        self.dt = 0.1
        self.timer = self.create_timer(self.dt, self._score_step)

    # ------------------------------------------------------------------
    def _make_odom_cb(self, agent_id: int):
        def callback(msg: Odometry):
            self.agent_pos[agent_id] = (msg.pose.pose.position.x, msg.pose.pose.position.y)
            self.agent_speed[agent_id] = abs(msg.twist.twist.linear.x)
        return callback

    def _make_health_cb(self, agent_id: int):
        def callback(msg: USVHealth):
            self.agent_health[agent_id] = float(msg.health_index)
        return callback

    def _make_role_cb(self, agent_id: int):
        def callback(msg: RoleAssignment):
            self.agent_role[agent_id] = msg.role.lower()
            self.agent_target[agent_id] = int(msg.assigned_target_id)
        return callback

    def _make_belief_cb(self, agent_id: int, target_id: int):
        def callback(msg: TargetBelief):
            self.beliefs[(agent_id, target_id)] = (msg.position.x, msg.position.y)
        return callback

    def _make_target_cb(self, target_id: int):
        def callback(msg: Odometry):
            self.target_pos[target_id] = (msg.pose.pose.position.x, msg.pose.pose.position.y)
            self.target_speed[target_id] = abs(msg.twist.twist.linear.x)
        return callback

    def _make_defuse_cb(self, target_id: int):
        def callback(msg: Float32):
            self.target_defusal[target_id] = float(msg.data)
            if msg.data >= 100.0:
                # An arrest is permanent, so latch it rather than tracking a decaying bus.
                self.target_neutralized[target_id] = True
        return callback

    # ------------------------------------------------------------------
    def _score_step(self):
        # Wait until the world is fully observable, so the baseline and the full stack are
        # scored over the same window.
        if len(self.agent_pos) < self.num_agents or len(self.target_pos) < self.num_targets:
            return

        agents = [
            AgentSample(
                agent_id=i,
                x=self.agent_pos[i][0],
                y=self.agent_pos[i][1],
                speed=self.agent_speed.get(i, 0.0),
                health=self.agent_health.get(i, 1.0),
                role=self.agent_role.get(i, "tracker"),
                target_id=self.agent_target.get(i, 0),
            )
            for i in sorted(self.agent_pos.keys())
        ]
        targets = [
            TargetSample(
                target_id=m,
                x=self.target_pos[m][0],
                y=self.target_pos[m][1],
                speed=self.target_speed.get(m, 0.0),
                neutralized=self.target_neutralized.get(m, False),
                defusal_progress=self.target_defusal.get(m, 0.0),
            )
            for m in sorted(self.target_pos.keys())
        ]

        verdicts = self.scorecard.update(
            dt=self.dt,
            agents=agents,
            targets=targets,
            beliefs=self.beliefs if self.beliefs else None,
        )

        self.elapsed_since_report += self.dt
        if self.elapsed_since_report >= self.score_period:
            self.elapsed_since_report = 0.0
            self.get_logger().info(self.scorecard.render(verdicts))
            # Single parseable line for run_comparison.sh.
            self.get_logger().info(self.scorecard.scorecard_line())


def main(args=None):
    rclpy.init(args=args)
    node = USVScorecardNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
