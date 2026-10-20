# Codebase Implementation Walkthrough

This document provides a detailed file-by-file walkthrough of the **Fault-Aware Multi-Agent USV Swarm** codebase, explaining the internal logic, algorithms, state variables, and interface contracts across both Stage 1 (`training/`) and Stage 2 (`src/`).

---

## 1. Directory Organization

The codebase is organized into two primary tiers:
```
usv_swarm_ws/
├── training/                # Stage 1: Vectorized Python Simulation & ML Pipeline
│   ├── env/                 # 3-DOF physics, target dynamics, fault injection, CBF shield, metrics
│   ├── models/              # XGBoost fault classifier & FA-MAPPO neural network architectures
│   ├── scripts/             # Training & evaluation scripts (FDI training, Stage 1 3x3 benchmark)
│   └── checkpoints/         # Serialized model weights (xgb_diagnosis.json, fa_mappo_actor.pt)
├── src/                     # Stage 2: ROS 2 Lyrical Distributed Robotics Workspace
│   ├── usv_msgs/            # Custom strongly-typed ROS 2 message definitions
│   ├── usv_sim_core/        # 20 Hz multi-agent 3-DOF hydrodynamic simulator node
│   ├── usv_perception_mesh/ # Ad-hoc gossip perception node with stochastic packet drop
│   ├── usv_health_diagnosis/# 10 Hz residual sliding-window diagnostic node (loads xgb_diagnosis.json)
│   ├── usv_recovery_planner/# Dynamic Hungarian role manager + Section 5 mission verifier
│   ├── usv_policy_marl/     # Kinematic & FA-MAPPO policy executor node
│   ├── usv_safety_shield/   # 20 Hz analytical Control Barrier Function safety shield
│   ├── usv_fault_injector/  # Runtime fault injection scheduler node
│   └── usv_bringup/         # Launch orchestration and RViz visualization configuration
├── tests/                   # Automated regression test suite
└── docs/                    # Complete documentation suite, test reports, and empirical logs
```

---

## 2. Stage 1: Training & Core Algorithmic Library (`training/`)

### 2.1 `training/env/dynamics.py` — 3-DOF Hydrodynamics & Evasive Target
- **Purpose:** Implements the nonlinear 3-DOF Fossen horizontal equations of motion for both USVs and the target vessel.
- **State Representation:** 
  - $\boldsymbol{\eta} = [x, y, \psi]^T$ (inertial position and heading).
  - $\boldsymbol{\nu} = [u, v, r]^T$ (body-fixed surge velocity, sway velocity, and yaw rate).
- **Key Classes & Methods:**
  - `USVDynamics`: Integrates rigid-body mass $m = 18.0\,\text{kg}$, inertia $I_z = 2.6\,\text{kg}\cdot\text{m}^2$, added mass terms ($X_{\dot{u}} = -2.0, Y_{\dot{v}} = -10.0, N_{\dot{r}} = -1.0$), and quadratic drag ($X_{uu} = 35.0, Y_{vv} = 80.0, N_{rr} = 15.0$).
  - `USVDynamics.step(tau_u, tau_r, dt)`: Uses **4th-Order Runge-Kutta (RK4)** integration to calculate state derivatives $\dot{\boldsymbol{\nu}} = \boldsymbol{M}^{-1}(\boldsymbol{\tau} - \boldsymbol{C}(\boldsymbol{\nu})\boldsymbol{\nu} - \boldsymbol{D}(\boldsymbol{\nu})\boldsymbol{\nu})$ and $\dot{\boldsymbol{\eta}} = \boldsymbol{R}(\psi)\boldsymbol{\nu}$.
  - `TargetDynamics`: Encapsulates target movement across 3 modes:
    - `nominal`: Constant transit speed $u = 1.2\,\text{m/s}$.
    - `sinusoidal`: Lateral weaving with cross-track amplitude $4.0\,\text{m}$ and period $15.0\,\text{s}$.
    - `reactive`: Implements centroid evasion and non-destructive containment arrest. When the distance from the target to the enclosing tracker centroid is $< 6.0\,\text{m}$ with $\ge 3$ USVs nearby, the target detects that escape is impossible and systematically decelerates to $< 0.20\,\text{m/s}$.

### 2.2 `training/env/fault_injector.py` — Domain-Randomized Fault Simulator
- **Purpose:** Injects realistic physical, sensor, and communication faults into agents during simulation.
- **Fault Types Handled:**
  - `THRUSTER_FAULT`: Scales available surge thrust down to 35% of nominal capacity ($\eta_{thrust} = 0.35$).
  - `GPS_FAULT`: Adds zero-mean Gaussian noise ($\sigma = 4.0\,\text{m}$) or linear drift ($0.5\,\text{m/s}$) to position observations.
  - `LINK_LOSS`: Discards communication packets over simulated ad-hoc links with packet drop probability up to 40%.
- **Key Methods:**
  - `inject_step(agent_id, odom, comms_matrix)`: Modifies sensor readings and inter-agent visibility graphs according to active fault schedules.

### 2.3 `training/env/ekf_tracker.py` — Target State Estimator
- **Purpose:** Fuses noisy, range-limited target observations into a consistent target kinematic state $[x_t, y_t, u_t, v_t, \psi_t]^T$.
- **Algorithm:** Standard Extended Kalman Filter with constant-velocity prediction model and range-bearing measurement updates. Handles measurement dropouts gracefully during communication disruptions.

### 2.4 `training/env/role_manager.py` — Dynamic Hungarian Allocator with Hysteresis
- **Purpose:** Computes optimal one-to-one assignments between USVs and spatial encircling slots.
- **Key Mechanics:**
  - Generates $N$ encircling slots around the target: Primary Trackers ($k < 2$) and Interceptors ($k \ge 2$) on the $R = 20.0\,\text{m}$ ring; Relays at $R = 30.0\,\text{m}$; Scouts at $R = 45.0\,\text{m}$.
  - **Cost Matrix:** Distance-to-slot $\|\boldsymbol{p}_i - \boldsymbol{p}_s\|$.
  - **Degradation Penalty:** Adds $+150.0 \times (1.0 - h_i)$ for assigning a degraded agent ($h_i < 0.50$) to a ring tracker slot, forcing degraded vessels to hand off ring duties.
  - **Relay Incentive:** Subtracts $40.0 \times (1.0 - h_i)$ for assigning a degraded agent to an escort/relay role.
  - **Hysteresis Anti-Chatter:** Subtracts $-15.0$ from the cost if agent $i$ was assigned to slot $s$ in the preceding tick, preventing high-frequency role swapping.

### 2.5 `training/env/safety_shield.py` — Analytical Control Barrier Function (CBF)
- **Purpose:** Provides a mathematically guaranteed safety barrier between all vessel pairs.
- **Safety Invariant:** Enforces minimum inter-agent separation $d_{ij} \ge 4.0\,\text{m}$.
- **Filtering Logic:**
  - Warning zone: $d_{warning} = 10.0\,\text{m}$.
  - When $d_{ij} < 10.0\,\text{m}$, speed is scaled by $\lambda = (d_{ij} - 4.0)/6.0$ and evasive yaw is blended into the steering command.
  - Hard stop: When $d_{ij} \le 5.0\,\text{m}$, forward speed is strictly clamped to $0.0\,\text{m/s}$.

### 2.6 `training/env/metrics.py` — Section 5 Encirclement Rubric Evaluator
- **Purpose:** Evaluates mission success according to the Section 5 Capstone Encirclement Rubric.
- **Evaluation Criteria:**
  - **Radius Compliance:** Ring agents must be within $R = 20.0 \pm 3.0\,\text{m}$.
  - **Criterion 1 (Angular Spread):** $\text{spread} \ge 2\pi(1 - 1/N) - 0.15\,\text{rad}$ ($0.15\,\text{rad}$ physical tolerance margin).
  - **Criterion 2 (Max Angular Gap):** $\text{max\_gap} \le (2\pi/N) \times 1.5$.
  - **Target Arrest:** Target trailing speed $< 0.20\,\text{m/s}$ or dwell time $\ge 10.0\,\text{s}$.
  - **Debounce Timer:** Criteria must be continuously satisfied for $\ge 5.0\,\text{s}$ before declaring encirclement achieved.
  - **Zero-Collision Invariant:** Across all timesteps, $\min_{i \ne j} d_{ij} \ge 4.0\,\text{m}$.

### 2.7 `training/env/agentic_arbiter.py` — Tactical Groq LLM Arbiter
- **Purpose:** Interfaces with Groq LPUs running `openai/gpt-oss-120b` for high-level tactical re-planning.
- **Features:**
  - Triggered exclusively by anomaly events (severe degradation $h_i < 0.30$, encirclement stall $> 15\,\text{s}$).
  - Emits validated JSON with role reassignments, escort vessel pairing, and revised standoff geometry.
  - Includes a zero-crash deterministic fallback for offline or unauthenticated operation.

### 2.8 `training/models/fault_classifier.py` — XGBoost Fault Classifier
- **Purpose:** Feature extraction and supervised gradient-boosted classification of USV operational health.
- **Input Vector:** 14 sliding-window ($W = 10$) residual features covering position discrepancy, velocity tracking error, yaw rate variance, comms packet drop rate, and sensor dropouts.
- **Output:** Probability distribution over 4 classes (`HEALTHY`, `GPS_FAULT`, `THRUSTER_FAULT`, `LINK_LOSS`) and composite health index $h_i(t)$.

### 2.9 `training/models/fa_mappo.py` — Fault-Tolerant MARL Policy
- **Purpose:** Multi-agent PPO network incorporating Fault-Tolerant Multi-Head Attention (FT-Attn).
- **Architecture:** Individual actor MLPs with central critic attending over teammate health beliefs ($h_j$) to generate coordinated encircling controls under incomplete connectivity.

---

## 3. Stage 2: Distributed ROS 2 Stack (`src/`)

### 3.1 `usv_msgs` (Interface Package)
Defines the five custom message types (`USVHealth.msg`, `RoleAssignment.msg`, `TargetBelief.msg`, `MissionStatus.msg`, `RecoveryEvent.msg`) used across all nodes.

### 3.2 `usv_sim_core` (`sim_node.py`)
- Runs at 20 Hz.
- Maintains the true 3-DOF continuous physical simulation for all $N$ USVs and the target vessel.
- Publishes `/usv_{i}/odom` and `/target/odom`.
- Listens to `/usv_{i}/cmd_vel` from the safety shield to update body velocities.

### 3.3 `usv_perception_mesh` (`perception_node.py`)
- Runs at 10 Hz.
- Simulates range-limited onboard sensors ($R_{sensor} = 35.0\,\text{m}$) and the ad-hoc wireless mesh.
- Implements stochastic packet dropping based on fault injector directives.
- Publishes `/usv_{i}/target_belief` with EKF state estimates.

### 3.4 `usv_health_diagnosis` (`diagnosis_node.py`)
- Runs at 10 Hz.
- Maintains a 10-tick sliding window of telemetry residuals for each USV.
- Loads `training/checkpoints/xgb_diagnosis.json` to compute health probabilities and composite health index $h_i(t)$.
- Publishes `/usv_{i}/health` (`usv_msgs/msg/USVHealth`).

### 3.5 `usv_recovery_planner` (`planner_node.py`)
- Runs at 10 Hz.
- Subscribes to `/usv_{i}/health` and target beliefs.
- Runs the Hungarian matching algorithm with hysteresis and health penalties to assign roles.
- Evaluates Section 5 encirclement criteria at each tick and publishes `/mission/status`.
- Emits `/recovery/event` to trigger tactical reconfiguration when faults occur.

### 3.6 `usv_policy_marl` (`policy_executor_node.py`)
- Runs at 10 Hz.
- Subscribes to `/usv_{i}/role` and `/usv_{i}/target_belief`.
- Computes nominal surge and yaw commands ($u_{cmd}, r_{cmd}$) toward assigned encircling slots using kinematic guidance and frozen FA-MAPPO policy weights.
- Publishes `/usv_{i}/cmd_vel_nominal`.

### 3.7 `usv_safety_shield` (`safety_node.py`)
- Runs at 20 Hz.
- Acts as a safety gatekeeper between `/usv_{i}/cmd_vel_nominal` and actuators.
- Implements the Control Barrier Function filter: checks all neighbor distances from odometry and overrides velocities if separation drops below $10.0\,\text{m}$, guaranteeing $d \ge 4.0\,\text{m}$.
- Publishes safe actuation commands to `/usv_{i}/cmd_vel`.

### 3.8 `usv_fault_injector` (`fault_injector_node.py`)
- Runs an event timer.
- Injects scheduled hardware and network faults (e.g., thruster degradation on USV-1 at $t = 15.0\,\text{s}$).
- Simulates communication link blackouts and GPS noise spikes.

### 3.9 `usv_bringup` (`swarm_simulation.launch.py`)
- ROS 2 launch file orchestrating the startup of all 7 nodes.
- Configurable parameters: `num_agents` (default: 6), `target_mode` (default: `reactive`), `use_rviz` (default: false).
- Sets up namespacing, topic remappings, and clean shutdown handling.
