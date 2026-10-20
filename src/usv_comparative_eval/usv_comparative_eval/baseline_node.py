"""
USV Swarm Simulation - ROS 2 Ablated Baseline Swarm Node
Author: Principal Enterprise Engineer
Stage: 2 (Architecture Ablation Study)

This node is the CONTROL CONDITION for the architecture comparison. It flies the same fleet
against the same hostiles in the same physics, toward the SAME win condition (hold an
interceptor inside the 7.5 m strike corridor for 4.0 s to defuse a hostile), but with every
TritonSwarm subsystem removed. It is a conventional swarm controller, not a straw man: each
ablation is the obvious naive implementation of the thing it replaces.

Ablations, and the failure mode each one produces
-------------------------------------------------
NO_ALLOCATOR         Roles are a static table fixed at startup and never reconsidered.
                     -> ROLE_VACANCY. When a drone is lost, its role is simply gone for good.

NO_WING_PARTITION    Every drone independently chases whichever hostile looks nearest.
                     -> FORCE_DOGPILE: the fleet masses on one hostile and leaves the others
                        completely unengaged.
                     -> ASSIGNMENT_THRASH: with noisy ranges, near-equidistant hostiles make
                        the choice oscillate tick to tick.

NO_LEAD_GUIDANCE     Pure pursuit straight at the hostile's last measured position; no
                     proportional navigation, no standoff-orbit field, no phase locking.
                     -> TAIL_CHASE: drones converge on the pursuit bearing and arrive as a
                        clump behind the hostile.
                     -> CONTAINMENT_FAIL: no angular spread, so the encirclement criteria
                        never pass and the hostile always has an open escape sector.

NO_CBF_GUARANTEE     Conventional boids-style fixed-radius separation IS present -- this is
                     deliberately a credible baseline, not an unprotected one -- but it is a
                     soft steering bias with no closing-rate awareness and no bound on the
                     worst case. Commands go straight to /usv_{i}/cmd_vel with no control
                     barrier function in the loop.
                     -> COLLISION: separation reduces hull strikes without preventing them,
                        and a hull strike is a mission kill.
                     -> STALL: the repulsion term fights the pursuit objective, so drones
                        stand off each other instead of closing on the hostile.
                     -> HOSTILE_IMPACT: nothing brakes an interceptor at the arrest boundary,
                        so pure pursuit at full throttle rams the hostile. That disables the
                        target but wrecks the drone, and a ramming capture fails the mission's
                        non-destructive arrest objective.

NO_STATE_ESTIMATION  Raw noisy range measurements with dropouts; no EKF, no gossip fusion,
                     no cooperative consensus. A dropped detection leaves a stale position.
                     -> TRACK_ERROR: large belief error and occasional total track loss.

NO_FAULT_DIAGNOSIS   Health telemetry is published for the world model and the scorer, but is
                     never consulted for any decision.
                     -> UNDETECTED_FAULT: a crippled drone keeps trying to fly an assignment
                        its remaining thrust cannot achieve, and drags the formation with it.

NO_DAMAGE_CONTROL    Nothing recovers a lost drone (the fault injector is launched with
                     self_healing:=false for this stack).
                     -> ATTRITION: losses are cumulative and the fleet only ever shrinks.

NO_DEADLOCK_ESCAPE   No inter-agent repulsion or tangential escape.
                     -> STALL: drones that converge on the same point sit there.

Standardized ROS 2 Interfaces:
- Subscribes: /usv_{i}/odom, /usv_{i}/injected_fault, /target_{m}/odom
- Publishes:  /usv_{i}/cmd_vel            (DIRECT - no CBF shield in the loop)
- Publishes:  /usv_{i}/health             (raw echo + collision mission-kills; never consulted)
- Publishes:  /usv_{i}/role               (static roles, greedy target)
- Publishes:  /usv_{i}/target_{m}/belief  (raw unfiltered measurement)
- Publishes:  /target_{m}/defuse_status   (same win condition as the full stack)
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32
from usv_msgs.msg import RoleAssignment, TargetBelief, USVHealth

# Same actuator envelope as the full stack.
MAX_SURGE = 3.5
MAX_YAW_RATE = 0.8

# Same win condition as the full stack, so the comparison is on method, not on objective.
D_DEFUSE_LOCK = 7.5
D_DEFUSE_BREAK = 9.5
T_DEFUSE = 4.0

# Centre-to-centre range at which a drone and a hostile hull are in contact. The production
# CBF brakes an interceptor at 4.0 m specifically so this is never reached; this stack has no
# such bound, so pure pursuit at full throttle drives straight through it.
D_HULL_STRIKE = 3.0

# Naive standoff radii: a radial stand-off with no angular coordination.
R_TRACKER_STANDOFF = 20.0
R_RELAY_STANDOFF = 30.0
R_SCOUT_STANDOFF = 45.0

CASUALTY_HEALTH = 0.25


class USVBaselineSwarmNode(Node):
    """Conventional swarm controller with every TritonSwarm subsystem ablated."""

    ABLATIONS = (
        "NO_ALLOCATOR", "NO_WING_PARTITION", "NO_LEAD_GUIDANCE", "NO_CBF_GUARANTEE",
        "NO_STATE_ESTIMATION", "NO_FAULT_DIAGNOSIS", "NO_DAMAGE_CONTROL",
        "NO_DEADLOCK_ESCAPE",
    )

    def __init__(self):
        super().__init__('usv_baseline_swarm_node')

        self.declare_parameter('num_agents', 12)
        self.declare_parameter('num_targets', 3)
        self.declare_parameter('sensor_noise_std', 2.5)
        self.declare_parameter('dropout_rate', 0.25)
        self.declare_parameter('sensor_range', 70.0)
        self.declare_parameter('collision_radius', 3.0)
        # Conventional boids-style separation: present, but with no formal guarantee.
        self.declare_parameter('separation_radius', 7.0)
        self.declare_parameter('separation_gain', 1.8)
        self.declare_parameter('random_seed', 20251020)

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.num_targets = max(1, self.get_parameter('num_targets').get_parameter_value().integer_value)
        self.noise_std = self.get_parameter('sensor_noise_std').get_parameter_value().double_value
        self.dropout_rate = self.get_parameter('dropout_rate').get_parameter_value().double_value
        self.sensor_range = self.get_parameter('sensor_range').get_parameter_value().double_value
        self.collision_radius = self.get_parameter('collision_radius').get_parameter_value().double_value
        self.separation_radius = self.get_parameter('separation_radius').get_parameter_value().double_value
        self.separation_gain = self.get_parameter('separation_gain').get_parameter_value().double_value
        seed = self.get_parameter('random_seed').get_parameter_value().integer_value

        # Fixed seed: the ablation study must be reproducible run to run.
        self.rng = np.random.default_rng(seed)

        self.get_logger().warning(
            "\n" + "=" * 118 + "\n"
            f"[BASELINE SWARM - ABLATED CONTROL CONDITION] {self.num_agents} drones vs "
            f"{self.num_targets} hostiles\n"
            "Running a conventional swarm controller with the TritonSwarm architecture REMOVED.\n"
            "Ablations active: " + ", ".join(self.ABLATIONS) + "\n"
            f"Conventional boids separation IS enabled (radius {self.separation_radius:.1f}m, "
            f"gain {self.separation_gain:.1f}) so this is a credible baseline, not an "
            f"unprotected one. What it lacks is a formal safety guarantee.\n"
            "Win condition is unchanged: hold an interceptor inside the 7.5 m strike corridor "
            "for 4.0 s.\n" + "=" * 118
        )

        # --- NO_ALLOCATOR: a static role table, fixed now and never reconsidered ----------
        self.static_roles: Dict[int, str] = {}
        for i in range(self.num_agents):
            if i < self.num_targets:
                self.static_roles[i] = "interceptor"
            elif i == self.num_agents - 2:
                self.static_roles[i] = "relay"
            elif i == self.num_agents - 1:
                self.static_roles[i] = "scout"
            else:
                self.static_roles[i] = "tracker"
        self.get_logger().info(
            "[NO_ALLOCATOR] Static role table fixed at startup: "
            + ", ".join(f"USV-{i}:{r}" for i, r in sorted(self.static_roles.items()))
        )

        # --- State -----------------------------------------------------------------------
        self.agent_pose: Dict[int, Tuple[float, float, float]] = {}
        self.agent_surge: Dict[int, float] = {}
        self.hw_health: Dict[int, float] = {i: 1.0 for i in range(self.num_agents)}
        self.hw_thrust: Dict[int, float] = {i: 1.0 for i in range(self.num_agents)}
        self.mission_killed: Dict[int, bool] = {i: False for i in range(self.num_agents)}
        self.chosen_target: Dict[int, int] = {i: 0 for i in range(self.num_agents)}

        self.target_pos: Dict[int, Tuple[float, float]] = {}
        # NO_STATE_ESTIMATION: a single raw measurement per (agent, hostile), no filter behind it.
        self.raw_measurement: Dict[Tuple[int, int], Tuple[float, float]] = {}
        self.measurement_age: Dict[Tuple[int, int], float] = {}

        self.defusal_timer: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.defusal_progress: Dict[int, float] = {m: 0.0 for m in range(self.num_targets)}
        self.neutralized: Dict[int, bool] = {m: False for m in range(self.num_targets)}
        self.lock_announced: Dict[int, bool] = {m: False for m in range(self.num_targets)}

        # --- Failure-mode counters (reported live, and independently re-derived by the
        #     impartial scorecard node from ground truth) --------------------------------
        self.failures: Dict[str, int] = {
            "COLLISION": 0, "HOSTILE_IMPACT": 0, "TRACK_LOSS": 0, "ASSIGNMENT_THRASH": 0,
            "ROLE_VACANCY": 0, "STALL": 0, "UNDETECTED_FAULT": 0,
        }
        self.collision_pairs: set = set()
        self.impact_pairs: set = set()
        # Hostiles stopped by being rammed rather than cleanly arrested.
        self.destructively_disabled: Dict[int, bool] = {m: False for m in range(self.num_targets)}
        self.sim_time = 0.0
        self.step_count = 0

        # --- Publishers & Subscriptions ---------------------------------------------------
        self.cmd_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.health_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.role_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        self.belief_pubs: Dict[Tuple[int, int], rclpy.publisher.Publisher] = {}

        for i in range(self.num_agents):
            # NO_SAFETY_SHIELD: straight onto the actuator topic, with no barrier in between.
            self.cmd_pubs[i] = self.create_publisher(Twist, f'/usv_{i}/cmd_vel', 10)
            self.health_pubs[i] = self.create_publisher(USVHealth, f'/usv_{i}/health', 10)
            self.role_pubs[i] = self.create_publisher(RoleAssignment, f'/usv_{i}/role', 10)
            for m in range(self.num_targets):
                self.belief_pubs[(i, m)] = self.create_publisher(
                    TargetBelief, f'/usv_{i}/target_{m}/belief', 10
                )

            self.create_subscription(Odometry, f'/usv_{i}/odom', self._make_odom_cb(i), 10)
            self.create_subscription(
                USVHealth, f'/usv_{i}/injected_fault', self._make_fault_cb(i), 10
            )

        self.defuse_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        for m in range(self.num_targets):
            self.create_subscription(Odometry, f'/target_{m}/odom', self._make_target_cb(m), 10)
            self.defuse_pubs[m] = self.create_publisher(Float32, f'/target_{m}/defuse_status', 10)
        self.legacy_defuse_pub = self.create_publisher(Float32, '/target/defuse_status', 10)

        # 10 Hz naive control loop
        self.dt = 0.1
        self.timer = self.create_timer(self.dt, self._control_step)

    # ------------------------------------------------------------------
    def _make_odom_cb(self, agent_id: int):
        def callback(msg: Odometry):
            qz = msg.pose.pose.orientation.z
            qw = msg.pose.pose.orientation.w
            psi = 2.0 * math.atan2(qz, qw)
            self.agent_pose[agent_id] = (
                msg.pose.pose.position.x, msg.pose.pose.position.y, psi
            )
            self.agent_surge[agent_id] = msg.twist.twist.linear.x
        return callback

    def _make_fault_cb(self, agent_id: int):
        def callback(msg: USVHealth):
            # NO_FAULT_DIAGNOSIS: the hardware state is recorded for the world model and the
            # scorer, and is then deliberately never consulted by any control decision.
            self.hw_health[agent_id] = float(msg.health_index)
            self.hw_thrust[agent_id] = float(msg.thrust_efficiency)
        return callback

    def _make_target_cb(self, target_id: int):
        def callback(msg: Odometry):
            self.target_pos[target_id] = (
                msg.pose.pose.position.x, msg.pose.pose.position.y
            )
        return callback

    @staticmethod
    def _wrap(a: float) -> float:
        return (a + math.pi) % (2.0 * math.pi) - math.pi

    # ------------------------------------------------------------------
    def _control_step(self):
        if len(self.agent_pose) < self.num_agents or len(self.target_pos) < self.num_targets:
            return

        self.sim_time += self.dt
        self.step_count += 1
        now = self.get_clock().now().to_msg()

        self._sense_raw()
        self._detect_collisions()
        self._detect_hostile_impacts()

        for i in range(self.num_agents):
            x, y, psi = self.agent_pose[i]
            role = self.static_roles[i]

            if self.mission_killed[i]:
                # A hull strike is a mission kill: no recovery path exists in this stack.
                self._publish_cmd(i, 0.0, 0.0)
                self._publish_role(now, i, role, self.chosen_target[i], (x, y))
                continue

            # --- NO_WING_PARTITION: greedily chase whichever hostile looks nearest --------
            prev_choice = self.chosen_target[i]
            choice = self._greedy_nearest(i)
            if choice != prev_choice:
                self.failures["ASSIGNMENT_THRASH"] += 1
            self.chosen_target[i] = choice

            tx, ty = self.raw_measurement.get((i, choice), self.target_pos[choice])

            # --- NO_LEAD_GUIDANCE: pure pursuit onto the last measured position ----------
            # A radial standoff with no tangential or phase component, which is why the fleet
            # arrives as a clump on the pursuit bearing instead of encircling.
            standoff = {
                "interceptor": 0.0,
                "tracker": R_TRACKER_STANDOFF,
                "relay": R_RELAY_STANDOFF,
                "scout": R_SCOUT_STANDOFF,
            }[role]

            range_to_target = math.hypot(tx - x, ty - y)
            if standoff > 0.0 and range_to_target > 1e-6:
                # Hold off radially along the agent's own current bearing from the hostile.
                own_bearing = math.atan2(y - ty, x - tx)
                gx = tx + standoff * math.cos(own_bearing)
                gy = ty + standoff * math.sin(own_bearing)
            else:
                gx, gy = tx, ty

            dx = gx - x
            dy = gy - y
            dist = math.hypot(dx, dy)

            # --- NO_CBF_GUARANTEE: conventional boids separation, blended as a soft steering
            # bias. It has no closing-rate term and no worst-case bound, so it trades hull
            # strikes for stalls and oscillation without ever guaranteeing separation.
            sep_x, sep_y, d_min = self._separation_vector(i, x, y)
            if dist > 1e-6:
                gdx, gdy = dx / dist, dy / dist
            else:
                gdx, gdy = math.cos(psi), math.sin(psi)
            cx = gdx + self.separation_gain * sep_x
            cy = gdy + self.separation_gain * sep_y
            heading_err = self._wrap(math.atan2(cy, cx) - psi)

            # Naive proportional controller, full throttle until close, with a crude
            # proximity throttle that is not tied to how fast the gap is actually closing.
            u_cmd = float(np.clip(0.9 * dist, 0.0, MAX_SURGE))
            if d_min < self.separation_radius:
                u_cmd *= float(np.clip(d_min / self.separation_radius, 0.0, 1.0))
            r_cmd = float(np.clip(heading_err * 2.0, -MAX_YAW_RATE, MAX_YAW_RATE))

            # --- NO_DEADLOCK_ESCAPE: no repulsion, no tangential slide. Record the stall. --
            if (self.hw_health[i] >= CASUALTY_HEALTH
                    and abs(self.agent_surge.get(i, 0.0)) < 0.15
                    and range_to_target > 12.0):
                self.failures["STALL"] += 1

            # --- NO_FAULT_DIAGNOSIS: a crippled drone keeps flying the same assignment ----
            if self.hw_thrust[i] < 0.80:
                self.failures["UNDETECTED_FAULT"] += 1

            self._publish_cmd(i, u_cmd, r_cmd)
            self._publish_role(now, i, role, choice, (gx, gy))

        self._publish_health(now)
        self._update_defusal()
        self._report_failures()

    # ------------------------------------------------------------------
    def _sense_raw(self) -> None:
        """
        NO_STATE_ESTIMATION: one raw noisy measurement per (agent, hostile), with dropouts and
        no filter, no cooperative fusion and no consensus. A dropped detection simply leaves
        the previous position in place, so the belief goes stale and drifts.
        """
        now = self.get_clock().now().to_msg()
        for i in range(self.num_agents):
            if i not in self.agent_pose:
                continue
            ax, ay, _ = self.agent_pose[i]
            for m in range(self.num_targets):
                tx, ty = self.target_pos[m]
                key = (i, m)
                in_range = math.hypot(tx - ax, ty - ay) <= self.sensor_range
                detected = in_range and (self.rng.random() > self.dropout_rate)

                if detected:
                    self.raw_measurement[key] = (
                        tx + float(self.rng.normal(0.0, self.noise_std)),
                        ty + float(self.rng.normal(0.0, self.noise_std)),
                    )
                    self.measurement_age[key] = 0.0
                else:
                    self.measurement_age[key] = self.measurement_age.get(key, 0.0) + self.dt
                    if key not in self.raw_measurement:
                        # Never acquired: the drone has no idea where this hostile is.
                        self.raw_measurement[key] = (tx, ty)
                    elif self.measurement_age[key] > 2.0:
                        self.failures["TRACK_LOSS"] += 1

                bx, by = self.raw_measurement[key]
                tb = TargetBelief()
                tb.stamp = now
                tb.source_agent_id = i
                tb.position.x = float(bx)
                tb.position.y = float(by)
                tb.velocity.x = 0.0   # no velocity state is estimated at all
                tb.velocity.y = 0.0
                var = self.noise_std ** 2
                tb.covariance = [var, 0.0, 0.0, var]
                tb.estimation_confidence = float(
                    np.clip(1.0 / (1.0 + self.measurement_age.get(key, 0.0)), 0.0, 1.0)
                )
                tb.target_visible = bool(detected)
                self.belief_pubs[key].publish(tb)

    def _separation_vector(self, agent_id: int, x: float, y: float) -> Tuple[float, float, float]:
        """
        Conventional boids separation: a unit repulsion from every neighbour inside a fixed
        radius, weighted linearly by how far inside that radius it is.

        This is what a conventional swarm controller provides. Note what it does NOT provide:
        no closing-rate (h_dot) term, so it reacts to where neighbours ARE rather than to how
        fast the gap is shrinking; no per-pair bound, so a crowded formation can overwhelm it;
        and no distinction between a neighbour that is closing and one that is opening, so it
        brakes vessels that were never in danger.
        """
        sx = 0.0
        sy = 0.0
        d_min = float('inf')
        for j, pose in self.agent_pose.items():
            if j == agent_id:
                continue
            ox, oy, _ = pose
            dx = x - ox
            dy = y - oy
            d = math.hypot(dx, dy)
            d_min = min(d_min, d)
            if d >= self.separation_radius or d < 1e-6:
                continue
            w = (self.separation_radius - d) / self.separation_radius
            sx += (dx / d) * w
            sy += (dy / d) * w
        return sx, sy, d_min

    def _greedy_nearest(self, agent_id: int) -> int:
        """
        NO_WING_PARTITION: independent greedy choice with no deconfliction between drones, so
        nothing stops the whole fleet selecting the same hostile and abandoning the others.
        """
        ax, ay, _ = self.agent_pose[agent_id]
        best = self.chosen_target[agent_id]
        best_d = float('inf')
        for m in range(self.num_targets):
            if self.neutralized[m]:
                continue
            mx, my = self.raw_measurement.get((agent_id, m), self.target_pos[m])
            d = math.hypot(mx - ax, my - ay)
            if d < best_d:
                best_d = d
                best = m
        return best

    def _detect_collisions(self) -> None:
        """
        NO_SAFETY_SHIELD: nothing prevents a hull strike, and a hull strike is a mission kill.
        """
        ids = sorted(self.agent_pose.keys())
        for a_idx in range(len(ids)):
            for b_idx in range(a_idx + 1, len(ids)):
                i, j = ids[a_idx], ids[b_idx]
                if self.mission_killed[i] and self.mission_killed[j]:
                    continue
                ax, ay, _ = self.agent_pose[i]
                bx, by, _ = self.agent_pose[j]
                d = math.hypot(ax - bx, ay - by)
                if d >= self.collision_radius:
                    continue

                pair = frozenset((i, j))
                if pair in self.collision_pairs:
                    continue
                self.collision_pairs.add(pair)
                self.failures["COLLISION"] += 1
                self.mission_killed[i] = True
                self.mission_killed[j] = True
                self.get_logger().error(
                    f"\n[COLLISION] >>> t={self.sim_time:5.1f}s: USV-{i} "
                    f"[{self.static_roles[i].upper()}] struck USV-{j} "
                    f"[{self.static_roles[j].upper()}] at {d:.2f}m separation. "
                    f"Both hulls are mission kills. No CBF shield was in the loop, and this "
                    f"stack has no damage control to recover them."
                )

    def _detect_hostile_impacts(self) -> None:
        """
        NO_CBF_GUARANTEE: nothing brakes an interceptor at the arrest boundary, so pure
        pursuit at full throttle rams the hostile. The drone is wrecked by the impact and the
        hostile is destructively disabled -- it stops, but the mission called for a
        non-destructive arrest, so this is a failure dressed up as a success.
        """
        for i in sorted(self.agent_pose.keys()):
            if self.mission_killed[i]:
                continue
            ax, ay, _ = self.agent_pose[i]
            for m in range(self.num_targets):
                tx, ty = self.target_pos[m]
                d = math.hypot(ax - tx, ay - ty)
                if d >= D_HULL_STRIKE:
                    continue

                pair = (i, m)
                if pair in self.impact_pairs:
                    continue
                self.impact_pairs.add(pair)
                self.failures["HOSTILE_IMPACT"] += 1
                self.mission_killed[i] = True
                was_clean = not self.destructively_disabled[m]
                self.destructively_disabled[m] = True
                # A rammed hostile is physically stopped.
                self.defusal_timer[m] = T_DEFUSE
                self.neutralized[m] = True
                self.get_logger().error(
                    f"\n[HOSTILE IMPACT] >>> t={self.sim_time:5.1f}s: USV-{i} "
                    f"[{self.static_roles[i].upper()}] RAMMED HOSTILE-{m} at {d:.2f}m. "
                    f"The drone is a mission kill and the hostile is DESTRUCTIVELY disabled. "
                    f"Nothing braked the approach at the 4.0 m arrest boundary, so this is "
                    f"not a non-destructive arrest."
                    + ("" if was_clean else " (hostile already rammed earlier)")
                )

    def _publish_cmd(self, agent_id: int, u: float, r: float) -> None:
        cmd = Twist()
        cmd.linear.x = float(u)
        cmd.angular.z = float(r)
        self.cmd_pubs[agent_id].publish(cmd)

    def _publish_role(self, now, agent_id: int, role: str, target_id: int,
                      slot: Tuple[float, float]) -> None:
        ra = RoleAssignment()
        ra.stamp = now
        ra.agent_id = agent_id
        ra.role = role
        ra.target_slot.x = float(slot[0])
        ra.target_slot.y = float(slot[1])
        ra.target_slot.z = 0.0
        ra.allocation_cost = 0.0   # no allocator runs, so there is no allocation cost
        ra.is_degraded_reallocation = False
        ra.assigned_target_id = int(target_id)
        self.role_pubs[agent_id].publish(ra)

    def _publish_health(self, now) -> None:
        """
        Publishes the hull state for the world model and the impartial scorer. A collision
        mission-kill is reported as zero health; otherwise the raw hardware state is echoed.
        Nothing in this node ever reads it back for a control decision.
        """
        for i in range(self.num_agents):
            msg = USVHealth()
            msg.stamp = now
            msg.agent_id = i
            if self.mission_killed[i]:
                msg.health_index = 0.0
                msg.fault_class = 3
                msg.fault_confidence = 1.0
                msg.thrust_efficiency = 0.0
                msg.fault_description = "COLLISION_MISSION_KILL"
            else:
                msg.health_index = float(self.hw_health[i])
                msg.fault_class = 0 if self.hw_health[i] >= 0.80 else 2
                msg.fault_confidence = 1.0
                msg.thrust_efficiency = float(self.hw_thrust[i])
                msg.fault_description = (
                    "HEALTHY" if self.hw_health[i] >= 0.80 else "UNDIAGNOSED_DEGRADATION"
                )
            msg.battery_level = 0.95
            msg.packet_loss_rate = 0.05
            self.health_pubs[i].publish(msg)

    def _update_defusal(self) -> None:
        """Identical win condition to the full stack: 4.0 s of sustained lock inside 7.5 m."""
        for m in range(self.num_targets):
            tx, ty = self.target_pos[m]
            locked = False
            for i in range(self.num_agents):
                if self.static_roles[i] != "interceptor" or self.mission_killed[i]:
                    continue
                ax, ay, _ = self.agent_pose[i]
                if math.hypot(ax - tx, ay - ty) <= D_DEFUSE_LOCK:
                    locked = True
                    if not self.lock_announced[m]:
                        self.lock_announced[m] = True
                        self.get_logger().info(
                            f"\n[BASELINE ENGAGEMENT] >>> USV-{i} reached the strike corridor "
                            f"of HOSTILE-{m}. EMP defusal lock accumulating."
                        )
                    break

            if self.neutralized[m]:
                self.defusal_timer[m] = T_DEFUSE
            elif locked:
                self.defusal_timer[m] += self.dt
            else:
                self.defusal_timer[m] = max(0.0, self.defusal_timer[m] - 0.4 * self.dt)

            self.defusal_progress[m] = min(100.0, (self.defusal_timer[m] / T_DEFUSE) * 100.0)
            if self.defusal_progress[m] >= 100.0 and not self.neutralized[m]:
                self.neutralized[m] = True
                self.get_logger().info(
                    f"\n[BASELINE] >>> HOSTILE-{m} defused and CLEANLY arrested at "
                    f"t={self.sim_time:5.1f}s (no hull strike)."
                )

            msg = Float32()
            msg.data = float(self.defusal_progress[m])
            self.defuse_pubs[m].publish(msg)
            if m == 0:
                self.legacy_defuse_pub.publish(msg)

    # ------------------------------------------------------------------
    def _report_failures(self) -> None:
        """Live failure-mode dashboard for the ablated stack, every 2.0 s."""
        if self.step_count % 20 != 0:
            return

        fit = [i for i in range(self.num_agents)
               if not self.mission_killed[i] and self.hw_health[i] >= CASUALTY_HEALTH]
        manned = {"interceptor": 0, "tracker": 0, "relay": 0, "scout": 0}
        for i in fit:
            manned[self.static_roles[i]] += 1
        vacant = [r for r, c in manned.items() if c < 1]
        if vacant:
            self.failures["ROLE_VACANCY"] += 1

        dogpile: Dict[int, int] = {}
        for i in fit:
            dogpile[self.chosen_target[i]] = dogpile.get(self.chosen_target[i], 0) + 1
        unengaged = [m for m in range(self.num_targets)
                     if not self.neutralized[m] and dogpile.get(m, 0) == 0]

        width = 118
        lines = ["", "=" * width]
        lines.append(
            f"[BASELINE SWARM - NO ARCHITECTURE] t={self.sim_time:6.1f}s | "
            f"fleet {len(fit)}/{self.num_agents} operational | "
            f"hostiles disabled {sum(self.neutralized.values())}/{self.num_targets} "
            f"(CLEAN arrests "
            f"{sum(1 for m in range(self.num_targets) if self.neutralized[m] and not self.destructively_disabled[m])}"
            f"/{self.num_targets}, RAMMED {sum(self.destructively_disabled.values())})"
        )
        lines.append("-" * width)
        lines.append(
            " EFFECTIVE MANNING (fit drones holding each STATIC role) | "
            + " | ".join(f"{r[:3].upper()} {c}{'  VACANT!' if c < 1 else ''}"
                         for r, c in manned.items())
        )
        if vacant:
            lines.append(
                f" ROLE_VACANCY     | {', '.join(v.upper() for v in vacant)} has no fit drone. "
                "The static role table cannot re-task anyone, so this is permanent."
            )
        lines.append(
            " FORCE ALLOCATION | "
            + ", ".join(f"H{m}: {dogpile.get(m, 0)} drones" for m in range(self.num_targets))
            + (f"   >>> UNENGAGED HOSTILES: {unengaged}" if unengaged else "")
        )
        lines.append("-" * width)
        lines.append(
            " FAILURE COUNTS   | "
            f"COLLISION {self.failures['COLLISION']} ({len(self.collision_pairs)} pairs lost) | "
            f"HOSTILE_IMPACT {self.failures['HOSTILE_IMPACT']} | "
            f"TRACK_LOSS {self.failures['TRACK_LOSS']} | "
            f"THRASH {self.failures['ASSIGNMENT_THRASH']} | "
            f"STALL {self.failures['STALL']} | "
            f"UNDETECTED_FAULT {self.failures['UNDETECTED_FAULT']}"
        )
        lines.append("-" * width)
        lines.append(" DRONE | Role (STATIC) | State                        | Chasing | Defusal")
        lines.append("-" * width)
        for i in range(self.num_agents):
            if self.mission_killed[i]:
                rammed = any(pair[0] == i for pair in self.impact_pairs)
                state = ("X MISSION KILL (RAMMED A HOSTILE)" if rammed
                         else "X MISSION KILL (COLLISION)")
            elif self.hw_health[i] < CASUALTY_HEALTH:
                state = "X LOST (no damage control)"
            elif self.hw_thrust[i] < 0.80:
                state = "! CRIPPLED (fault undiagnosed)"
            else:
                state = "pursuing"
            m = self.chosen_target[i]
            lines.append(
                f" USV-{i:<2} | {self.static_roles[i].upper():<13} | {state:<28} | "
                f"H{m:<6} | {self.defusal_progress[m]:5.1f}%"
            )
        lines.append("=" * width)
        self.get_logger().info("\n".join(lines))


def main(args=None):
    rclpy.init(args=args)
    node = USVBaselineSwarmNode()
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
