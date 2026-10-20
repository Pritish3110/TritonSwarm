"""
USV Swarm Simulation - ROS 2 Frozen FA-MAPPO Policy Executor Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment, Multi-Target Battle)

Responsibilities:
- Subscribes to per-hostile target beliefs, health belief h_i(t), assigned role + wing, odometry
- Assembles the 35-dimensional local observation vector matching the training specification
- Evaluates the frozen FA-MAPPO neural network policy (fa_mappo_actor.npz/.pt)
- Flies the analytic tactical guidance laws from guidance.py (proportional navigation for
  interceptors, Lyapunov standoff-orbit fields for containment trackers)
- Blends MARL and analytic guidance by engagement phase, and logs that split so the division
  of labour between the learned policy and the geometric controller is observable
- Emits /usv_{i}/cmd_vel_nominal for the CBF safety shield to filter

How MAPPO and the Hungarian allocator work together:
  the allocator (usv_recovery_planner) decides WHICH hostile each vessel is committed to and
  WHICH station it must hold; FA-MAPPO and the guidance laws decide HOW the vessel flies there.
  The learned policy is given most authority where it was trained -- holding formation on the
  containment ring -- and none at all on the terminal strike leg, which is safety-critical and
  is owned by deterministic proportional navigation.
"""

from __future__ import annotations
import math
import os
from typing import Dict, List, Optional, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32
from usv_msgs.msg import TargetBelief, USVHealth, RoleAssignment

from usv_policy_marl.guidance import (
    MAX_SURGE,
    MAX_YAW_RATE,
    GuidancePhase,
    facing_factor,
    heading_rate_cmd,
    proportional_navigation,
    select_phase,
    speed_profile,
    standoff_orbit_command,
    transit_waypoint,
    wrap_angle,
)

# Authority granted to the frozen FA-MAPPO actor, per engagement phase. The learned policy is
# trusted where it was trained (formation holding) and excluded from the terminal strike leg.
MARL_AUTHORITY: Dict[GuidancePhase, float] = {
    GuidancePhase.LAUNCH: 0.10,
    GuidancePhase.TRANSIT: 0.15,
    GuidancePhase.DEPLOY: 0.30,
    GuidancePhase.SHADOW: 0.45,
    GuidancePhase.PINCER: 0.35,
    GuidancePhase.TERMINAL: 0.00,
    GuidancePhase.EMP_LOCK: 0.00,
    GuidancePhase.ARREST_GUARD: 0.25,
    GuidancePhase.RECOVERY: 0.00,
    GuidancePhase.COMBAT_LOSS: 0.00,
}

# All containment trackers orbit the same way. Counter-rotating traffic on a shared ring is a
# standing collision hazard, so circulation is uniform and station error is trimmed with speed.
RING_CIRCULATION = 1.0


class USVPolicyExecutorNode(Node):
    """ROS 2 Node executing the frozen FA-MAPPO policy and tactical guidance across the fleet."""

    ROLE_MAP = {"tracker": 0, "interceptor": 1, "relay": 2, "scout": 3}

    def __init__(self):
        super().__init__('usv_policy_executor_node')

        self.declare_parameter('num_agents', 12)
        self.declare_parameter('num_targets', 3)
        self.declare_parameter('model_path', '')

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.num_targets = max(1, self.get_parameter('num_targets').get_parameter_value().integer_value)
        model_path = self.get_parameter('model_path').get_parameter_value().string_value

        self.get_logger().info(
            f"Initializing USVPolicyExecutorNode for {self.num_agents} vessels "
            f"across {self.num_targets} hostile wings."
        )

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
            self.get_logger().warning("No FA-MAPPO checkpoint loaded; operating in analytic guidance mode.")

        # State caches
        self.usv_poses: Dict[int, Tuple[float, float, float, float, float, float]] = {}
        self.target_beliefs: Dict[Tuple[int, int], Tuple[float, float, float, float]] = {}
        self.health_beliefs: Dict[int, float] = {i: 1.0 for i in range(self.num_agents)}
        self.assigned_roles: Dict[int, str] = {i: "tracker" for i in range(self.num_agents)}
        self.assigned_wings: Dict[int, int] = {i: 0 for i in range(self.num_agents)}
        self.target_slots: Dict[int, Tuple[float, float]] = {}
        self.allocation_costs: Dict[int, float] = {i: 0.0 for i in range(self.num_agents)}
        self.degraded_flags: Dict[int, bool] = {i: False for i in range(self.num_agents)}
        self.defusal_progress: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}

        # Telemetry
        self.sim_step_count = 0
        self.phases: Dict[int, GuidancePhase] = {i: GuidancePhase.LAUNCH for i in range(self.num_agents)}
        self.marl_authority: Dict[int, float] = {i: 0.0 for i in range(self.num_agents)}

        # Publishers & Subscriptions
        self.cmd_nominal_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.odom_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.belief_subs: Dict[Tuple[int, int], rclpy.subscription.Subscription] = {}
        self.health_subs: Dict[int, rclpy.subscription.Subscription] = {}
        self.role_subs: Dict[int, rclpy.subscription.Subscription] = {}

        for i in range(self.num_agents):
            self.cmd_nominal_pubs[i] = self.create_publisher(Twist, f'/usv_{i}/cmd_vel_nominal', 10)
            self.odom_subs[i] = self.create_subscription(
                Odometry, f'/usv_{i}/odom', self._make_odom_cb(i), 10
            )
            for m in range(self.num_targets):
                self.belief_subs[(i, m)] = self.create_subscription(
                    TargetBelief, f'/usv_{i}/target_{m}/belief', self._make_belief_cb(i, m), 10
                )
            self.health_subs[i] = self.create_subscription(
                USVHealth, f'/usv_{i}/health', self._make_health_cb(i), 10
            )
            self.role_subs[i] = self.create_subscription(
                RoleAssignment, f'/usv_{i}/role', self._make_role_cb(i), 10
            )

        self.defuse_subs: Dict[int, rclpy.subscription.Subscription] = {}
        for m in range(self.num_targets):
            self.defuse_subs[m] = self.create_subscription(
                Float32, f'/target_{m}/defuse_status', self._make_defuse_cb(m), 10
            )

        # 10 Hz Policy Inference Loop
        self.timer = self.create_timer(0.1, self._policy_step)

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
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

    def _make_belief_cb(self, agent_id: int, target_id: int):
        def callback(msg: TargetBelief):
            self.target_beliefs[(agent_id, target_id)] = (
                msg.position.x, msg.position.y, msg.velocity.x, msg.velocity.y
            )
        return callback

    def _make_defuse_cb(self, target_id: int):
        def callback(msg: Float32):
            self.defusal_progress[target_id] = float(msg.data)
        return callback

    def _make_health_cb(self, agent_id: int):
        def callback(msg: USVHealth):
            self.health_beliefs[agent_id] = msg.health_index
        return callback

    def _make_role_cb(self, agent_id: int):
        def callback(msg: RoleAssignment):
            self.assigned_roles[agent_id] = msg.role
            self.assigned_wings[agent_id] = int(msg.assigned_target_id)
            self.target_slots[agent_id] = (msg.target_slot.x, msg.target_slot.y)
            self.allocation_costs[agent_id] = float(msg.allocation_cost)
            self.degraded_flags[agent_id] = bool(msg.is_degraded_reallocation)
        return callback

    # ------------------------------------------------------------------
    # Frozen FA-MAPPO actor
    # ------------------------------------------------------------------
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

    def _assemble_observation(self, agent_id: int, belief: Tuple[float, float, float, float]) -> np.ndarray:
        """
        Builds the 35-dimensional observation exactly as specified during FA-MAPPO training.
        The layout is frozen with the weights and must not be reordered.
        """
        x, y, psi, u, v, r = self.usv_poses[agent_id]
        h_i = self.health_beliefs.get(agent_id, 1.0)
        role = self.assigned_roles.get(agent_id, "tracker")

        tx, ty, tvx, tvy = belief
        dx_t = tx - x
        dy_t = ty - y
        dist_t = math.hypot(dx_t, dy_t)
        bearing_t = wrap_angle(math.atan2(dy_t, dx_t) - psi)

        fault_one_hot = [1.0, 0.0, 0.0, 0.0] if h_i >= 0.60 else [0.0, 0.0, 1.0, 0.0]
        role_one_hot = [0.0, 0.0, 0.0, 0.0]
        role_one_hot[self.ROLE_MAP.get(role.lower(), 3)] = 1.0

        # 4 nearest neighbors
        neighbors = []
        for other_id in range(self.num_agents):
            if other_id != agent_id and other_id in self.usv_poses:
                ox, oy, _, _, _, _ = self.usv_poses[other_id]
                ndist = math.hypot(ox - x, oy - y)
                neighbors.append((ndist, (ox - x) / 50.0, (oy - y) / 50.0,
                                  self.health_beliefs.get(other_id, 1.0)))
        neighbors.sort(key=lambda item: item[0])
        neighbor_features: List[float] = []
        for k in range(4):
            if k < len(neighbors):
                ndist, ndx, ndy, nh = neighbors[k]
                neighbor_features.extend([ndx, ndy, ndist / 50.0, nh])
            else:
                neighbor_features.extend([0.0, 0.0, 2.0, 0.0])

        return np.array([
            dx_t / 50.0, dy_t / 50.0, dist_t / 50.0, bearing_t / math.pi,
            tvx / 2.0, tvy / 2.0,
            u / 2.5, v / 1.0, r / 0.8, psi / math.pi,
            h_i,
            *fault_one_hot,
            *role_one_hot,
            *neighbor_features
        ], dtype=np.float32)

    # ------------------------------------------------------------------
    # Policy loop
    # ------------------------------------------------------------------
    def _policy_step(self):
        self.sim_step_count += 1
        t_sec = self.sim_step_count * 0.1

        # Transit lane indices, assigned within each wing so a wing crosses open water abreast.
        wing_members: Dict[int, List[int]] = {}
        for i in range(self.num_agents):
            wing_members.setdefault(self.assigned_wings.get(i, 0), []).append(i)
        lane_index = {
            aid: idx
            for members in wing_members.values()
            for idx, aid in enumerate(sorted(members))
        }

        for i in range(self.num_agents):
            if i not in self.usv_poses:
                continue

            x, y, psi, u, v, r = self.usv_poses[i]
            role = self.assigned_roles.get(i, "tracker").lower()
            wing = self.assigned_wings.get(i, 0)
            h_i = self.health_beliefs.get(i, 1.0)
            slot = self.target_slots.get(i)

            belief = self.target_beliefs.get((i, wing))
            if belief is None:
                belief = self.target_beliefs.get((i, 0))
            if belief is None:
                # No track yet: hold the last command rather than guessing a goal.
                continue

            tx, ty, tvx, tvy = belief
            if slot is None:
                slot = (tx, ty)

            d_station = math.hypot(slot[0] - x, slot[1] - y)
            d_target = math.hypot(tx - x, ty - y)
            target_speed = math.hypot(tvx, tvy)

            # Ring geometry is recovered from the published station, so no extra plumbing is
            # needed to know what radius and phase the allocator intends.
            ring_radius = math.hypot(slot[0] - tx, slot[1] - ty)
            station_bearing = math.atan2(slot[1] - ty, slot[0] - tx)

            is_interceptor = "interceptor" in role
            is_tracker = "tracker" in role
            defusal = self.defusal_progress.get(wing, 0.0)
            # Once a hostile is arrested, the interceptors and trackers committed to it are
            # garrisoning it: the strike element included, which circles the hull rather than
            # holding a terminal capture corridor that no longer has anything to capture.
            # Relays and scouts are NOT garrison members -- they hold fleet-level stations
            # (the mesh backbone and the wide-area picket) that span the whole formation, and
            # treating them as orbiters would have them circle a hull instead of doing their job.
            is_garrison = (defusal >= 100.0) and (is_interceptor or is_tracker)
            slot_kind = ("guard" if is_garrison
                         else ("stern" if is_interceptor else ("ring" if is_tracker else "station")))
            pincer_active = is_tracker and not is_garrison and ring_radius < 18.0

            phase = select_phase(
                role=role,
                slot_kind=slot_kind,
                health=h_i,
                dist_to_station=d_station,
                dist_to_target=d_target,
                mission_elapsed=t_sec,
                pincer_active=pincer_active,
                defusal_progress=defusal,
                target_neutralized=(defusal >= 100.0),
                is_casualty_station=(h_i < 0.25),
            )
            self.phases[i] = phase

            # ---------------- Analytic tactical guidance ----------------
            if phase is GuidancePhase.COMBAT_LOSS:
                u_geo, r_geo, heading_err = 0.0, 0.0, 0.0

            elif is_interceptor and phase in (GuidancePhase.TERMINAL, GuidancePhase.EMP_LOCK):
                # Proportional navigation onto the lead point: a true collision course.
                pn = proportional_navigation(
                    own_pos=(x, y), own_psi=psi, own_speed=max(u, 0.5),
                    target_pos=(tx, ty), target_vel=(tvx, tvy),
                )
                heading_err = wrap_angle(pn.desired_heading - psi)
                r_geo = pn.yaw_rate_cmd
                u_geo = speed_profile(phase, d_station, target_speed, health=h_i)

            elif (is_tracker or is_garrison) and phase in (
                    GuidancePhase.SHADOW, GuidancePhase.PINCER,
                    GuidancePhase.DEPLOY, GuidancePhase.ARREST_GUARD):
                # Lyapunov standoff-orbit field with the hostile's own velocity as feed-forward,
                # phase-locked onto the assigned ring station. Without the feed-forward term the
                # ring translates out from under the tracker and containment never closes.
                # A garrison holds a precessing station around a stationary hull, so it needs
                # enough orbit rate to keep sweeping round rather than settling on a bearing.
                if phase is GuidancePhase.PINCER:
                    orbit_speed = 1.75
                elif is_garrison:
                    orbit_speed = 1.5
                else:
                    orbit_speed = 1.3
                sol = standoff_orbit_command(
                    own_pos=(x, y), center=(tx, ty), center_vel=(tvx, tvy),
                    radius=max(ring_radius, 1.0), station_bearing=station_bearing,
                    circulation=RING_CIRCULATION, orbit_speed=orbit_speed,
                )
                heading_err = wrap_angle(sol.desired_heading - psi)
                r_geo = heading_rate_cmd(heading_err)
                u_geo = sol.desired_speed

            else:
                # Transit, deployment and support stationkeeping: drive the published
                # station, offset into a wing lane while still far out.
                aim = slot
                if phase in (GuidancePhase.LAUNCH, GuidancePhase.TRANSIT):
                    aim = transit_waypoint(
                        (x, y), slot,
                        lane_index=lane_index.get(i, 0),
                        lane_count=max(1, len(wing_members.get(wing, [i]))),
                    )
                heading_err = wrap_angle(math.atan2(aim[1] - y, aim[0] - x) - psi)
                r_geo = heading_rate_cmd(heading_err)
                u_geo = speed_profile(phase, d_station, target_speed, health=h_i)

            u_geo *= facing_factor(heading_err)

            # ---------------- FA-MAPPO blend ----------------
            authority = MARL_AUTHORITY.get(phase, 0.0)
            if self.policy_weights is None:
                authority = 0.0
            self.marl_authority[i] = authority

            if authority > 0.0:
                marl_u, marl_r = self._forward_marl_policy(
                    self._assemble_observation(i, belief)
                )
                u_cmd = (1.0 - authority) * u_geo + authority * marl_u
                r_cmd = (1.0 - authority) * r_geo + authority * marl_r
            else:
                u_cmd, r_cmd = u_geo, r_geo

            u_cmd = float(np.clip(u_cmd, 0.0, MAX_SURGE))
            r_cmd = float(np.clip(r_cmd, -MAX_YAW_RATE, MAX_YAW_RATE))

            if phase is GuidancePhase.RECOVERY:
                u_cmd = min(u_cmd, 1.0)
            elif phase is GuidancePhase.COMBAT_LOSS:
                u_cmd, r_cmd = 0.0, 0.0

            cmd = Twist()
            cmd.linear.x = u_cmd
            cmd.angular.z = r_cmd
            self.cmd_nominal_pubs[i].publish(cmd)

        self._log_telemetry(t_sec)

    def _log_telemetry(self, t_sec: float) -> None:
        """Reports the MAPPO / analytic-guidance division of labour every 2.0 s."""
        if self.sim_step_count % 20 != 0:
            return

        dists = []
        for i in range(self.num_agents):
            wing = self.assigned_wings.get(i, 0)
            belief = self.target_beliefs.get((i, wing))
            if i in self.usv_poses and belief is not None:
                dists.append(math.hypot(self.usv_poses[i][0] - belief[0],
                                        self.usv_poses[i][1] - belief[1]))
        mean_dist = float(np.mean(dists)) if dists else 0.0
        mean_authority = float(np.mean(list(self.marl_authority.values())))

        rows = []
        for i in range(self.num_agents):
            phase = self.phases.get(i, GuidancePhase.LAUNCH)
            auth = self.marl_authority.get(i, 0.0)
            # The law actually flown depends on role as well as phase: only containment
            # trackers fly the standoff-orbit field; relays and scouts hold a waypoint station.
            role_i = self.assigned_roles.get(i, "").lower()
            if "interceptor" in role_i and phase in (GuidancePhase.TERMINAL, GuidancePhase.EMP_LOCK):
                law = "PN-LEAD"
            elif phase is GuidancePhase.ARREST_GUARD:
                law = "GARRISON-ORBIT"
            elif "tracker" in role_i and phase in (GuidancePhase.SHADOW, GuidancePhase.PINCER,
                                                   GuidancePhase.DEPLOY):
                law = "ORBIT-FIELD"
            else:
                law = "WAYPOINT"
            rows.append(
                f"USV-{i:<2} {self.assigned_roles.get(i, '?')[:4].upper():<4} T{self.assigned_wings.get(i, 0)} "
                f"{phase.value:<12} {law:<11} MAPPO {auth*100:3.0f}% "
                f"cost {self.allocation_costs.get(i, 0.0):7.1f}"
            )

        self.get_logger().info(
            f"\n[FA-MAPPO / GUIDANCE SPLIT] t={t_sec:6.1f}s | "
            f"Policy: {'fa_mappo_actor.npz (frozen)' if self.policy_weights else 'analytic only'} | "
            f"Fleet mean range to wing hostile: {mean_dist:5.1f}m | "
            f"Mean MAPPO authority: {mean_authority*100:4.1f}%\n  "
            + "\n  ".join(rows)
        )


def main(args=None):
    rclpy.init(args=args)
    node = USVPolicyExecutorNode()
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
