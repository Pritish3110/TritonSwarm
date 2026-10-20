# USV Swarm Capstone: Fault-Tolerant Multi-Agent Coordination & Agentic Recovery

A simulation-first, two-stage robotics and artificial intelligence framework for autonomous marine swarms ($N = 4 \text{ to } 10$ Unmanned Surface Vehicles) cooperatively tracking and non-destructively encircling an evasive surface vessel under up to **30% node degradation and 40% communication link loss**.

---

## Documentation Hub

The complete documentation suite, empirical proof logs, and technical reports are bundled in the [`docs/`](docs/) directory:

- **[Master Capstone Project Report](docs/FINAL_CAPSTONE_REPORT.md):** Complete academic & enterprise capstone report bundling background, theory, code, benchmarks, and logs.
- **[System Architecture & Theory](docs/ARCHITECTURE.md):** 3-DOF vessel dynamics, FT-Attn MARL, CBF safety filters, EKF tracker, Hungarian allocator, Groq LLM arbiter.
- **[Technology Stack & Environment Specs](docs/TECH_STACK.md):** Ubuntu 26.04, ROS 2 Lyrical, Conda Python 3.11, PyTorch, XGBoost, PettingZoo, RTX 3060 VRAM budget.
- **[Codebase Implementation Walkthrough](docs/IMPLEMENTATION_WALKTHROUGH.md):** File-by-file deep dive across all modules in `training/` and all 9 ROS 2 packages in `src/`.
- **[Testing & Empirical Benchmarks](docs/TESTING_AND_BENCHMARKS.md):** Test pipeline execution guide, log file analyses, Section 5 verification rubric, and reproduction steps.
- **[Empirical Performance Report](docs/CAPSTONE_PERFORMANCE_REPORT.md):** Raw benchmark tables, confusion matrices, and verification status across Stage 1 & Stage 2.
- **[Documentation Index & Quickstart](docs/README.md):** Overview map of the `docs/` repository.

---

## 1. System Architecture

The architecture implements a closed **Diagnose $\rightarrow$ Reconfigure $\rightarrow$ Act $\rightarrow$ Verify** loop:

```
                            +-----------------------------------------------+
                            |            usv_fault_injector                 |
                            |   (GPS drift, thruster kill, link loss proxy) |
                            +-----------------------------------------------+
                                      |                           |
                                      v                           v
                            +-------------------+       +-------------------+
                            |   usv_sim_core    | ----> | usv_perception    |
                            | (3-DOF dynamics)  |       |   _mesh (EKF)     |
                            +-------------------+       +-------------------+
                                      ^                           |
                                      |                           v
                            +-------------------+       +-------------------+
                            | usv_safety_shield |       |   Communication   |
                            | (Hard CBF filter) |       |  Mesh (p2p loss)  |
                            +-------------------+       +-------------------+
                                      ^                           |
                                      |                           v
                            +-------------------+       +-------------------+
                            |  usv_policy_marl  | <---- |    usv_health     |
                            | (Frozen FA-MAPPO) |       |  _diagnosis (XGB) |
                            +-------------------+       +-------------------+
                                      ^                           |
                                      |                           v
                            +-----------------------------------------------+
                            |             usv_recovery_planner              |
                            | - Fast Loop (10 Hz): Real-time Hungarian roles|
                            | - Event Loop: Groq (openai/gpt-oss-120b) / LLM|
                            | - Closed-Loop Section 5 Mission Verification  |
                            +-----------------------------------------------+
```

---

## 2. Theoretical & Mathematical Foundations

### 3-DOF Vessel Dynamics (Fossen / Norrbin)
$$\dot{\mathbf{\eta}} = \mathbf{R}(\psi) \mathbf{\nu} + \mathbf{v}_{\text{drift}}$$
$$\mathbf{M} \dot{\mathbf{\nu}} + \mathbf{C}(\mathbf{\nu})\mathbf{\nu} + \mathbf{D}(\mathbf{\nu})\mathbf{\nu} = \mathbf{\tau}$$
* State: Pose $\mathbf{\eta} = [x, y, \psi]^T$ in inertial frame; Velocities $\mathbf{\nu} = [u, v, r]^T$ in body frame.
* Drag: Bilinear linear and quadratic drag in surge, sway, and yaw.
* Actuation limits: $u_{\text{max}} = 2.5\,\text{m/s}$, $r_{\text{max}} = 0.8\,\text{rad/s}$.

### Target Vessel Evasion Profiles
1. **Nominal Straight-Line:** $1.2\,\text{m/s}$ (48% of USV top speed).
2. **Sinusoidal Evasive:** $1.5\,\text{m/s}$, lateral amplitude $4\,\text{m}$, period $15\,\text{s}$.
3. **Reactive Evasion:** Triggers when any Tracker/Interceptor is within $12\,\text{m}$. Accelerates toward $1.5\,\text{m/s}$ (60% of USV top speed) and steers away from the swarm centroid bearing with yaw rate capped at $0.4\,\text{rad/s}$.

### Hard Control Barrier Function (CBF) Safety Invariant
Enforces minimum vessel separation $d_{\text{safe}} = 4.0\,\text{m}$ between any pair of USVs:
$$h_{ij} = \|\mathbf{p}_i - \mathbf{p}_j\|^2 - d_{\text{safe}}^2 \ge 0$$
$$\dot{h}_{ij} \ge -\gamma h_{ij}$$
The CBF filter modulates commanded surge velocity and injects evasive repulsive steering whenever vessels close within the warning threshold ($d < 8.0\,\text{m}$), guaranteeing zero physical collisions regardless of high-level policy or LLM actions.

### Section 5 Encirclement Criteria & Mission Success
* **Participating Agents:** $N$ healthy ($h_i(t) \ge 0.30$) Tracker/Interceptor agents within $R = 20 \pm 3\,\text{m}$ of the target.
* **Criterion 1 (Angular Spread):**
  $$\text{Angular Spread} \ge 2\pi \left(1 - \frac{1}{N}\right)$$
* **Criterion 2 (Maximum Angular Gap):**
  $$\text{Max Gap} \le \left(\frac{2\pi}{N}\right) \times 1.5$$
* **Target Arrest Condition:** Target trailing 2-second speed $< 0.2\,\text{m/s}$ OR sustained dwell time inside $[17, 23]\,\text{m}$ ring for $\ge 10.0\,\text{s}$.
* **Mission Success:** Criteria 1, 2, and Arrest condition satisfied continuously for $\ge 5.0\,\text{s}$ (debounced), with **zero** inter-vessel collisions throughout.

---

## 3. Reference Literature Touchpoints

* **[1] Wang et al. (2025):** Sensor fusion pattern for cooperative multi-USV perception.
* **[2] Shrudhi et al. (2023):** Baseline feasibility of MARL target tracking in ROS.
* **[3] Hamid et al. (2026):** Multi-USV encirclement benchmarking with MAPPO.
* **[5] Hegde et al. (2026):** Self-healing communication topology and relay assignment.
* **[6] Luo et al. (2026):** AI-enabled diagnose-heal-verify closed loop.
* **[9] Gu et al. (2021) (FT-Attn):** Attention down-weighting of faulty agents in centralized critic.
* **[10] Ramezani et al. (2024):** Fallback coordination under sudden node attrition.
* **[12] Shi et al. (2024):** Fault-aware reward shaping and domain-randomized fault schedules.
* **[14] Raptis et al. (2025):** Safety architecture: high-level LLM decides roles and topologies, while deterministic CBF guarantees collision safety.

---

## 4. How to Run

### Environment Setup
```bash
conda activate usv_swarm_env
cd "/home/pritish_3110/Personal Files/Syllabus/Projects/Capstone_project/usv_swarm_ws"
```

### Stage 1: Machine Learning & Verification
1. **Train Supervised XGBoost Fault Classifier:**
   ```bash
   python -m training.scripts.train_diagnosis
   ```
2. **Train Fault-Aware MAPPO Policy:**
   ```bash
   python -m training.scripts.train_policy
   ```
3. **Run 3x3 Fault Sweep Benchmark (Section 5 Rubric):**
   ```bash
   python -m training.scripts.evaluate_stage1
   ```
4. **Run Automated Test Suite:**
   ```bash
   pytest tests/test_swarm_core.py
   ```

### Stage 2: ROS 2 Swarm Simulation
```bash
source /opt/ros/lyrical/setup.bash
source install/setup.bash

# Launch complete 6-USV swarm with reactive target
ros2 launch usv_bringup swarm_simulation.launch.py num_agents:=6 target_mode:=reactive
```

---

## 5. Telemetry & Monitoring

* **Mission Verification Status:**
  ```bash
  ros2 topic echo /mission/status
  ```
* **Event-Triggered Recovery Decisions (Groq `openai/gpt-oss-120b`):**
  ```bash
  ros2 topic echo /recovery/event
  ```
* **Individual USV Health Vectors ($h_i(t)$):**
  ```bash
  ros2 topic echo /usv_0/health
  ```
* **RViz2 3D Visualization:**
  ```bash
  rviz2 -d src/usv_bringup/config/rviz2_swarm.rviz
  ```
