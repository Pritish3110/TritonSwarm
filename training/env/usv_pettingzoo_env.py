"""
USV Swarm Simulation - PettingZoo Parallel Environment
Author: Principal Enterprise Engineer
Stage: 1 (Multi-Agent Training Environment)

Conforms to PettingZoo ParallelEnv standard:
- N agents (parameterized 4-10)
- 3-DOF vessel dynamics with hydrodynamics, drag, drift
- Target vessel with nominal, sinusoidal, and reactive evasion modes
- Integrated FaultInjector & EKF cooperative state estimation
- Section 5 Encirclement & Debounced Mission Success evaluation
"""

from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple
import gymnasium as gym
from gymnasium import spaces
import numpy as np
from pettingzoo import ParallelEnv

from training.env.dynamics import USVDynamics3DOF, USVState, TargetVessel, TargetMode, VesselParams
from training.env.fault_injector import FaultInjector, FaultType
from training.env.ekf_tracker import EKFTracker
from training.env.metrics import EncirclementEvaluator, MetricSnapshot


class USVSwarmParallelEnv(ParallelEnv):
    """PettingZoo ParallelEnv for cooperative USV swarm tracking and encirclement."""

    metadata = {"render_modes": ["human", "rgb_array"], "name": "usv_swarm_v1"}

    def __init__(
        self,
        num_agents: int = 6,
        dt: float = 0.1,
        max_episode_steps: int = 500,
        target_mode: TargetMode = TargetMode.REACTIVE,
        node_fault_ratio: float = 0.30,
        max_link_loss_rate: float = 0.40,
        seed: Optional[int] = None
    ):
        super().__init__()
        assert 4 <= num_agents <= 10, f"Number of agents must be in [4, 10], got {num_agents}"
        self.n_agents = num_agents
        self.dt = dt
        self.max_steps = max_episode_steps
        self.target_mode = target_mode
        self.node_fault_ratio = node_fault_ratio
        self.max_link_loss_rate = max_link_loss_rate
        self.seed_val = seed

        self.possible_agents = [f"usv_{i}" for i in range(num_agents)]
        self.agent_name_to_id = {f"usv_{i}": i for i in range(num_agents)}

        # Subsystems
        self.vessel_dynamics = USVDynamics3DOF(VesselParams())
        self.fault_injector = FaultInjector(
            num_agents=num_agents,
            node_fault_ratio=node_fault_ratio,
            max_link_loss_rate=max_link_loss_rate,
            seed=seed
        )
        self.evaluator = EncirclementEvaluator()

        # State tracking
        self.agent_states: Dict[int, USVState] = {}
        self.ekf_trackers: Dict[int, EKFTracker] = {}
        self.agent_roles: Dict[int, str] = {}
        self.agent_health_beliefs: Dict[int, float] = {}
        self.target: Optional[TargetVessel] = None

        self.current_step = 0
        self.current_time = 0.0

        # Action Space: kinematic setpoints [u_cmd in [0, 2.5], r_cmd in [-0.8, 0.8]]
        self._action_spaces = {
            agent: spaces.Box(
                low=np.array([0.0, -0.8], dtype=np.float32),
                high=np.array([2.5, 0.8], dtype=np.float32),
                dtype=np.float32
            )
            for agent in self.possible_agents
        }

        # Observation Space (dim = 36):
        # [0:6]  Relative target state [dx_t, dy_t, dist_t, bearing_t, vx_t, vy_t]
        # [6:10] Own velocities & heading [u, v, r, psi]
        # [10:15] Health belief & fault type one-hot [h_i, c0, c1, c2, c3]
        # [15:19] Role one-hot [is_tracker, is_interceptor, is_relay, is_scout]
        # [19:35] Up to 4 nearest neighbors (4 features each: [dx, dy, dist, h_j])
        self.obs_dim = 35
        self._observation_spaces = {
            agent: spaces.Box(
                low=-float("inf"),
                high=float("inf"),
                shape=(self.obs_dim,),
                dtype=np.float32
            )
            for agent in self.possible_agents
        }

    def observation_space(self, agent: str) -> spaces.Space:
        return self._observation_spaces[agent]

    def action_space(self, agent: str) -> spaces.Space:
        return self._action_spaces[agent]

    def reset(
        self,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, np.ndarray], Dict[str, Dict[str, Any]]]:
        if seed is not None:
            self.seed_val = seed
        rng = np.random.default_rng(self.seed_val)

        self.agents = self.possible_agents.copy()
        self.current_step = 0
        self.current_time = 0.0
        self.evaluator.reset()
        self.fault_injector.reset(randomize=True)

        # Initialize target near origin with random heading
        t_x = float(rng.uniform(-10.0, 10.0))
        t_y = float(rng.uniform(-10.0, 10.0))
        t_psi = float(rng.uniform(-math.pi, math.pi))
        self.target = TargetVessel(initial_x=t_x, initial_y=t_y, initial_psi=t_psi, mode=self.target_mode)

        # Initialize USVs dispersed in perimeter around target (35m to 55m radius)
        self.agent_states.clear()
        self.ekf_trackers.clear()
        self.agent_roles.clear()
        self.agent_health_beliefs.clear()

        for i in range(self.n_agents):
            angle = (2.0 * math.pi / self.n_agents) * i + float(rng.uniform(-0.2, 0.2))
            dist = float(rng.uniform(35.0, 50.0))
            usv_x = t_x + dist * math.cos(angle)
            usv_y = t_y + dist * math.sin(angle)
            usv_psi = (angle + math.pi + float(rng.uniform(-0.3, 0.3))) % (2 * math.pi) - math.pi

            state = USVState(agent_id=i, x=usv_x, y=usv_y, psi=usv_psi)
            self.agent_states[i] = state

            # EKF tracker initialized with perturbed target belief
            self.ekf_trackers[i] = EKFTracker(
                initial_x=t_x + float(rng.normal(0, 3.0)),
                initial_y=t_y + float(rng.normal(0, 3.0)),
                dt=self.dt
            )
            # Default initial roles: 4 Trackers, rest Scouts/Relays
            self.agent_roles[i] = "tracker" if i < min(4, self.n_agents) else "scout"
            self.agent_health_beliefs[i] = 1.0

        observations = {agent: self._get_obs(agent) for agent in self.agents}
        infos = {agent: {"role": self.agent_roles[self.agent_name_to_id[agent]]} for agent in self.agents}

        return observations, infos

    def _get_obs(self, agent_name: str) -> np.ndarray:
        agent_id = self.agent_name_to_id[agent_name]
        state = self.agent_states[agent_id]
        ekf = self.ekf_trackers[agent_id]

        # 1. Target belief relative features
        t_est_x, t_est_y, t_est_vx, t_est_vy = ekf.state
        dx_t = t_est_x - state.x
        dy_t = t_est_y - state.y
        dist_t = math.hypot(dx_t, dy_t)
        bearing_t = (math.atan2(dy_t, dx_t) - state.psi + math.pi) % (2 * math.pi) - math.pi

        # 2. Own dynamic states
        u = state.u
        v = state.v
        r = state.r
        psi = state.psi

        # 3. Health & Fault category
        h_i = self.agent_health_beliefs.get(agent_id, 1.0)
        gt_fault, _ = self.fault_injector.get_ground_truth_health(agent_id)
        fault_one_hot = [0.0, 0.0, 0.0, 0.0]
        fault_one_hot[int(gt_fault)] = 1.0

        # 4. Role one-hot [tracker, interceptor, relay, scout]
        role = self.agent_roles.get(agent_id, "scout").lower()
        role_map = {"tracker": 0, "interceptor": 1, "relay": 2, "scout": 3}
        role_one_hot = [0.0, 0.0, 0.0, 0.0]
        role_one_hot[role_map.get(role, 3)] = 1.0

        # 5. Up to 4 nearest communicating neighbors
        neighbors = []
        for other_id, other_state in self.agent_states.items():
            if other_id == agent_id:
                continue
            dx = other_state.x - state.x
            dy = other_state.y - state.y
            dist = math.hypot(dx, dy)
            if self.fault_injector.can_communicate(agent_id, other_id, dist):
                other_h = self.agent_health_beliefs.get(other_id, 1.0)
                neighbors.append((dist, dx, dy, other_h))

        neighbors.sort(key=lambda x: x[0])
        neighbor_features = []
        for k in range(4):
            if k < len(neighbors):
                d, dx, dy, h_j = neighbors[k]
                neighbor_features.extend([dx / 50.0, dy / 50.0, d / 50.0, h_j])
            else:
                neighbor_features.extend([0.0, 0.0, 2.0, 0.0])

        obs_vec = np.array([
            dx_t / 50.0, dy_t / 50.0, dist_t / 50.0, bearing_t / math.pi,
            t_est_vx / 2.0, t_est_vy / 2.0,
            u / 2.5, v / 1.0, r / 0.8, psi / math.pi,
            h_i,
            *fault_one_hot,
            *role_one_hot,
            *neighbor_features
        ], dtype=np.float32)

        return obs_vec

    def step(
        self,
        actions: Dict[str, np.ndarray]
    ) -> Tuple[
        Dict[str, np.ndarray],
        Dict[str, float],
        Dict[str, bool],
        Dict[str, bool],
        Dict[str, Dict[str, Any]]
    ]:
        self.current_step += 1
        self.current_time += self.dt

        # 1. Update active faults
        self.fault_injector.update(self.current_time, self.dt)

        # 2. Advance target vessel
        tracker_pos = [
            (s.x, s.y)
            for i, s in self.agent_states.items()
            if self.agent_roles.get(i) in ["tracker", "interceptor"]
        ]
        target_state = self.target.step(self.dt, tracker_pos)

        # 3. Advance USV dynamics
        for agent_name in self.agents:
            agent_id = self.agent_name_to_id[agent_name]
            act = actions.get(agent_name, np.array([0.0, 0.0], dtype=np.float32))
            u_cmd, r_cmd = float(act[0]), float(act[1])

            # Apply thruster degradation
            state = self.agent_states[agent_id]
            prof = self.fault_injector.profiles[agent_id]
            state.thrust_efficiency = prof.thrust_efficiency

            self.vessel_dynamics.step(state, u_cmd=u_cmd, r_cmd=r_cmd, dt=self.dt)

        # 4. Sensor simulation & EKF update
        for agent_id, state in self.agent_states.items():
            ekf = self.ekf_trackers[agent_id]
            ekf.predict(self.dt)

            if not self.fault_injector.is_detection_dropped(agent_id):
                # Measure true relative target range and bearing
                true_dx = target_state.x - state.x
                true_dy = target_state.y - state.y
                true_range = math.hypot(true_dx, true_dy)
                true_bearing = math.atan2(true_dy, true_dx)

                # Corrupt measurement based on sensor faults
                meas_x, meas_y = self.fault_injector.corrupt_gps(agent_id, state.x, state.y)
                ekf.update_range_bearing(meas_x, meas_y, true_range, true_bearing)

        # 5. Gossip communication fusion
        for i in self.agent_states:
            for j in self.agent_states:
                if i != j:
                    d = math.hypot(self.agent_states[i].x - self.agent_states[j].x,
                                   self.agent_states[i].y - self.agent_states[j].y)
                    if self.fault_injector.can_communicate(i, j, d):
                        # Blend estimates
                        self.ekf_trackers[i].fuse_neighbor_estimate(
                            self.ekf_trackers[j].state,
                            self.ekf_trackers[j].P,
                            weight=0.7
                        )

        # 6. Update health beliefs
        for i in self.agent_states:
            _, true_h = self.fault_injector.get_ground_truth_health(i)
            self.agent_health_beliefs[i] = true_h

        # 7. Evaluate Section 5 Encirclement & Success Metrics
        pos_dict = {i: (s.x, s.y) for i, s in self.agent_states.items()}
        trailing_speed = (
            sum(target_state.speed_history) / len(target_state.speed_history)
            if target_state.speed_history else target_state.speed
        )

        metric_snap: MetricSnapshot = self.evaluator.evaluate_step(
            current_time=self.current_time,
            dt=self.dt,
            agent_positions=pos_dict,
            agent_health=self.agent_health_beliefs,
            agent_roles=self.agent_roles,
            target_pos=(target_state.x, target_state.y),
            target_speed_2s=trailing_speed
        )

        # 8. Reward computation
        rewards = self._compute_rewards(metric_snap)

        # 9. Terminations & Truncations
        terminated = metric_snap.debounced_success or metric_snap.collision_detected
        truncated = self.current_step >= self.max_steps

        terminations = {agent: terminated for agent in self.agents}
        truncations = {agent: truncated for agent in self.agents}

        if terminated or truncated:
            self.agents = []

        observations = {agent: self._get_obs(agent) for agent in self.possible_agents}
        infos = {
            agent: {
                "success": metric_snap.debounced_success,
                "collision": metric_snap.collision_detected,
                "angular_spread": metric_snap.angular_spread,
                "max_gap": metric_snap.max_gap,
                "participating": metric_snap.participating_count
            }
            for agent in self.possible_agents
        }

        return observations, rewards, terminations, truncations, infos

    def _compute_rewards(self, snap: MetricSnapshot) -> Dict[str, float]:
        """Calculates cooperative and local rewards aligned with encirclement objectives."""
        rewards = {}
        tx, ty = self.target.state.x, self.target.state.y

        # Global cooperative reward terms
        r_spread = 2.0 if snap.criterion_1_pass else -0.5
        r_gap = 2.0 if snap.criterion_2_pass else -0.5
        r_success = 50.0 if snap.debounced_success else 0.0
        r_collision = -100.0 if snap.collision_detected else 0.0

        for agent_name in self.possible_agents:
            agent_id = self.agent_name_to_id[agent_name]
            state = self.agent_states[agent_id]
            dist_to_t = math.hypot(state.x - tx, state.y - ty)

            # Ring proximity reward: optimal distance is R = 20m
            ring_error = abs(dist_to_t - 20.0)
            r_dist = math.exp(-0.1 * ring_error)

            # Collision proximity penalty between USVs
            r_sep_penalty = 0.0
            for other_id, other_state in self.agent_states.items():
                if other_id != agent_id:
                    d = math.hypot(state.x - other_state.x, state.y - other_state.y)
                    if d < 6.0:
                        r_sep_penalty -= (6.0 - d) * 1.5

            total_reward = (
                r_dist * 1.5 +
                r_spread +
                r_gap +
                r_sep_penalty +
                r_success +
                r_collision
            )
            rewards[agent_name] = float(total_reward)

        return rewards
