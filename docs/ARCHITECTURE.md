# System Architecture & Mathematical Foundations

This document provides a comprehensive technical breakdown of the architecture, algorithms, and mathematical formulations governing the **Fault-Aware Multi-Agent USV Swarm**.

---

## 1. High-Level Two-Stage Architecture

The system is structured as a two-stage pipeline separating offline learning and model serialization from online physical-fidelity distributed deployment:

```mermaid
flowchart TD
    subgraph Stage1["Stage 1: Vectorized Python Simulation & Model Training"]
        A["3-DOF Vessel Dynamics & Target Evasion"] --> B["PettingZoo ParallelEnv"]
        C["Domain-Randomized Fault Injector"] --> B
        B --> D["Sliding-Window Feature Extractor"]
        D --> E["XGBoost Supervised Fault Classifier<br/>(Macro-F1: 0.9880)"]
        E -->|Serializes xgb_diagnosis.json| F["Model Checkpoints"]
        B --> G["FA-MAPPO Actor + FT-Attn Critic"]
        G -->|Serializes fa_mappo_actor.pt| F
        F --> H["Stage 1 3x3 Fault Sweep Benchmark"]
    end

    subgraph Stage2["Stage 2: Distributed ROS 2 Physical Deployment"]
        I["usv_sim_core (3-DOF Native Sim Node)"] -->|/usv_{i}/odom, /target/odom| J["usv_perception_mesh"]
        J -->|/usv_{i}/target_belief| K["usv_health_diagnosis<br/>(Loads xgb_diagnosis.json)"]
        K -->|/usv_{i}/health (h_i)| L["usv_recovery_planner<br/>(Hungarian + Groq LLM Arbiter)"]
        L -->|/usv_{i}/role| M["usv_policy_marl<br/>(Loads fa_mappo_actor.pt)"]
        M -->|/usv_{i}/cmd_vel_nominal| N["usv_safety_shield<br/>(CBF Filter: d >= 4.0m)"]
        N -->|/usv_{i}/cmd_vel| I
        L -->|/mission/status| O["Section 5 Mission Verifier"]
        L -->|/recovery/event| P["Groq LLM Tactical Arbiter"]
    end

    F -.->|Loads Frozen Weights| K
    F -.->|Loads Frozen Weights| M
```

---

## 2. Mathematical Modeling of Vehicle Dynamics

### 2.1 3-DOF USV Hydrodynamics (Fossen/Norrbin Formulation)
Each USV is governed by non-linear 3-DOF horizontal equations of motion:

$$\dot{\boldsymbol{\eta}} = \boldsymbol{R}(\psi) \boldsymbol{\nu}$$

$$\boldsymbol{M} \dot{\boldsymbol{\nu}} + \boldsymbol{C}(\boldsymbol{\nu}) \boldsymbol{\nu} + \boldsymbol{D}(\boldsymbol{\nu}) \boldsymbol{\nu} = \boldsymbol{\tau} + \boldsymbol{\tau}_{drift}$$

Where:
- $\boldsymbol{\eta} = [x, y, \psi]^T$ represents global North-East position and heading angle.
- $\boldsymbol{\nu} = [u, v, r]^T$ represents body-fixed surge velocity, sway velocity, and yaw rate.
- $\boldsymbol{R}(\psi)$ is the standard SO(2) rotation matrix:
  $$\boldsymbol{R}(\psi) = \begin{bmatrix} \cos\psi & -\sin\psi & 0 \\ \sin\psi & \cos\psi & 0 \\ 0 & 0 & 1 \end{bmatrix}$$
- $\boldsymbol{M} = \text{diag}(m_{11}, m_{22}, m_{33})$ incorporates vessel rigid-body mass and hydrodynamic added mass:
  $$m_{11} = m - X_{\dot{u}}, \quad m_{22} = m - Y_{\dot{v}}, \quad m_{33} = I_z - N_{\dot{r}}$$
- $\boldsymbol{C}(\boldsymbol{\nu})$ is the hydrodynamic Coriolis-centripetal matrix:
  $$\boldsymbol{C}(\boldsymbol{\nu}) = \begin{bmatrix} 0 & 0 & -m_{22} v \\ 0 & 0 & m_{11} u \\ m_{22} v & -m_{11} u & 0 \end{bmatrix}$$
- $\boldsymbol{D}(\boldsymbol{\nu})$ encapsulates linear and non-linear hydrodynamic damping:
  $$\boldsymbol{D}(\boldsymbol{\nu}) = \begin{bmatrix} X_u + X_{uu}|u| & 0 & 0 \\ 0 & Y_v + Y_{vv}|v| & 0 \\ 0 & 0 & N_r + N_{rr}|r| \end{bmatrix}$$
- Numerical integration is computed using high-accuracy **4th-Order Runge-Kutta (RK4)** integration at $\Delta t = 0.05\,\text{s}$ in ROS 2 (`usv_sim_core`) and $\Delta t = 0.10\,\text{s}$ in vectorized training (`training/env/dynamics.py`).

---

## 3. Evasive Target Vessel & Containment Dynamics

The target vessel operates in three configurable behavioral modes:
1. **Nominal Mode:** Fixed cruise speed ($u = 1.2\,\text{m/s}$) along a straight transit vector.
2. **Sinusoidal Mode:** Evasive lateral weaving with cross-track amplitude $A = 4.0\,\text{m}$ and period $T = 15.0\,\text{s}$.
3. **Reactive Evasion & Non-Destructive Containment Arrest:**
   - Detects approaching trackers within perception radius $R_{contain} = 25.0\,\text{m}$.
   - Computes the geometric centroid of surrounding trackers:
     $$\bar{\boldsymbol{c}} = \frac{1}{|\mathcal{N}_{close}|} \sum_{j \in \mathcal{N}_{close}} \boldsymbol{p}_j$$
   - **Open Sector Escape:** If $\|\boldsymbol{p}_{target} - \bar{\boldsymbol{c}}\| \ge 6.0\,\text{m}$, steers along the vector pointing away from $\bar{\boldsymbol{c}}$ towards open water at evasive speeds up to $u_{evade} = 1.5\,\text{m/s}$.
   - **Non-Destructive Containment Arrest:** When hemmed in by $\ge 3$ vessels across all quadrants ($\|\boldsymbol{p}_{target} - \bar{\boldsymbol{c}}\| < 6.0\,\text{m}$), the target recognizes escape is blocked and decelerates towards arrest:
     $$u_{target}(t + \Delta t) = \max(0.08, u_{target}(t) - 0.3 \Delta t)$$
   - When trailing 2-second speed drops below $0.20\,\text{m/s}$ or dwell time $\ge 10.0\,\text{s}$ is sustained, Section 5 arrest criteria are met.

---

## 4. Fault Diagnosis & Health Belief Architecture ($h_i(t)$)

### 4.1 Sliding-Window Residual Feature Extraction
Each USV maintains a sliding window of recent telemetry ($W = 10$ ticks, corresponding to $1.0\,\text{s}$ of physical history):
1. **Position Residuals:** $\|\boldsymbol{p}_{GPS} - \boldsymbol{p}_{DeadReckoning}\|$ (Mean, Variance, Max, Trend).
2. **Surge Velocity Error:** $|u_{cmd} - u_{meas}|$ (Mean, Variance, Max).
3. **Yaw Rate Error:** $|r_{cmd} - r_{meas}|$ (Mean, Variance).
4. **Perception Dropout Rate:** Fraction of missing lidar/radar detections over the window.
5. **Comms Loss Rate:** Proportion of lost mesh packets over expected neighbor links.
6. **Battery Drain:** Battery level and consumption rate.

### 4.2 Supervised XGBoost Classifier
An XGBoost gradient-boosted decision forest processes the 14-dimensional feature vector:
- **Classes:** 
  - Class 0: $\text{HEALTHY}$
  - Class 1: $\text{GPS\_FAULT}$ (additive zero-mean Gaussian noise $\sigma = 4.0\,\text{m}$ or linear drift $0.5\,\text{m/s}$)
  - Class 2: $\text{THRUSTER\_FAULT}$ (loss of control authority down to 35% of nominal thrust)
  - Class 3: $\text{LINK\_LOSS}$ (intermittent or sustained mesh packet drop up to 40%)
- **Health Index Formulation:**
  $$h_i(t) = P(\text{HEALTHY}) + 0.50 P(\text{GPS}) + P(\text{THRUST}) \cdot \eta_{thrust} + 0.40 P(\text{LINK})$$
  Where $\eta_{thrust} = \max(0.2, 1.0 - |u_{cmd} - u_{meas}| / 2.0)$.
- **Empirical Performance:** **98.44% Accuracy | 0.9880 Macro-F1** across 3,905 holdout windows.

---

## 5. Control Barrier Function (CBF) Hard Safety Shield

To guarantee collision freedom ($d_{ij} \ge 4.0\,\text{m}$) regardless of neural network outputs, packet drops, or actuator faults, an analytical Control Barrier Function filter processes nominal commands $\boldsymbol{u}_{nominal}$:

### 5.1 Barrier Definition
For any pair of vessels $i$ and $j$, define the safety barrier:
$$h_{ij}(\boldsymbol{p}_i, \boldsymbol{p}_j) = \|\boldsymbol{p}_i - \boldsymbol{p}_j\|^2 - d_{safe}^2 \ge 0$$
Where:
- $d_{safe} = 4.0\,\text{m}$ (hard collision boundary).
- $d_{warning} = 10.0\,\text{m}$ (active deceleration and evasive yaw boundary).

### 5.2 Omnidirectional Barrier Filter
For any neighboring vessel $j$ within the warning boundary ($d_{ij} < d_{warning}$):
1. **Speed Scaling:**
   $$\lambda_{ij} = \text{clamp}\left(\frac{d_{ij} - d_{safe}}{d_{warning} - d_{safe}}, 0.0, 1.0\right)$$
   $$u_{safe} = \min(u_{safe}, u_{max} \cdot \lambda_{ij})$$
2. **Evasive Steering:**
   $$r_{evade} = \text{sgn}(-\theta_{ij}^{rel}) \cdot r_{max} \cdot (1.0 - \lambda_{ij})$$
   $$r_{safe} = \text{clamp}(0.3 r_{cmd} + 0.7 r_{evade}, -r_{max}, r_{max})$$
3. **Hard Emergency Halt:** If $d_{ij} \le d_{safe} + 1.0\,\text{m}$, $u_{safe} \leftarrow 0.0\,\text{m/s}$.

---

## 6. Dynamic Hungarian Role Allocator with Hysteresis

The fleet dynamically assigns roles to maintain optimal encircling geometry using the Kuhn-Munkres (Hungarian) bipartite matching algorithm:
- **Ring Roles:** Primary Trackers ($k < 2$) and Interceptors ($k \ge 2$) spaced uniformly at radius $R = 20.0\,\text{m}$ around the target.
- **Support Roles:** Relays (intermediate standoff $R_{relay} = 30.0\,\text{m}$) and Scouts (outer perimeter $R_{scout} = 45.0\,\text{m}$).

### Cost Function with Degradation Penalty and Hysteresis:
$$C_{i, s} = \|\boldsymbol{p}_i - \boldsymbol{p}_s^{slot}\| + \text{Penalty}(h_i, \text{role}_s) + H_{i, s}$$
Where:
- $\text{Penalty}(h_i, \text{Tracker}) = (1.0 - h_i) \times 150.0$ if $h_i < 0.50$ (forces degraded nodes out of the encirclement ring).
- $\text{Penalty}(h_i, \text{Relay}) = -(1.0 - h_i) \times 40.0$ (favors degraded nodes for low-thrust relay tasks).
- $H_{i, s} = -15.0$ if agent $i$ held slot $s$ in the previous tick (eliminates role chattering and switching jitter).

---

## 7. Event-Triggered LLM Arbiter (Groq LPUs)

When an anomalous condition occurs (node degradation $h_i < 0.30$, severe link disruption $> 40\%$, or encirclement stall $> 15\,\text{s}$), the system emits a high-level `RecoveryEvent`:
1. **Trigger:** Event-driven (not polled every tick, conserving compute and tokens).
2. **Model:** Groq cloud LPUs running `openai/gpt-oss-120b` (500 tokens/sec, 131k context window).
3. **Structured Schema:** Emits Pydantic JSON action:
   ```json
   {
     "strategy": "DYNAMIC_REALLOCATION",
     "reallocated_roles": {"1": "relay", "4": "tracker"},
     "escort_id": 1,
     "target_standoff_radius": 20.0,
     "rationale": "USV-1 has suffered thruster degradation (h=0.35). Promoting healthy scout USV-4 to 20m ring."
   }
   ```
4. **Deterministic Fallback:** If internet connectivity is interrupted or API keys are absent, the system automatically falls back to an internal rule-based arbiter without crashing.
