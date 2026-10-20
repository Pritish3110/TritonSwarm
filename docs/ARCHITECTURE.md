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

---

## 12. Multi-Target Battle Doctrine (Stage 2 Scale-Up)

The single-target prototype was scaled to a **multi-hostile agentic drone battle**: $N = 12$ ally
drones against $M = 3$ coordinated hostile drones. Four new rclpy-free modules carry the doctrine,
so the algorithms are unit-testable independently of ROS 2:

| Module | Package | Responsibility |
|---|---|---|
| `establishment.py` | `usv_recovery_planner` | Tactical establishment resolver, wing partitioning, slot lattice, Hungarian cost matrix |
| `guidance.py` | `usv_policy_marl` | Proportional navigation, standoff-orbit command, engagement phase machine |
| `barriers.py` | `usv_safety_shield` | Velocity-aware multi-target control barrier functions |
| `hostile_ai.py` | `usv_sim_core` | Coordinated hostile evasion, escort formation, mutual support |

### 12.1 The Role-Continuity Invariant

**Claim.** While at least four combat-fit vessels survive, every canonical role
(`interceptor`, `tracker`, `relay`, `scout`) is manned.

In the prototype, slots were generated *from* the surviving role vector:

$$\mathcal{S}_t = f(\mathbf{r}_{t-1}), \qquad \mathbf{r}_t = g(\mathcal{S}_t)$$

This recursion is the defect. Destroying the only relay removed `relay` from
$\mathbf{r}_{t-1}$, so $\mathcal{S}_t$ contained no relay slot, and no assignment could ever
restore it. Role loss was therefore permanent.

Slots are now generated from the **mission state** instead:

$$\mathcal{S}_t = f\big(\mathcal{W}_t,\; |\mathcal{A}^{\text{fit}}_t|\big), \qquad
\mathbf{r}_t = g(\mathcal{S}_t)$$

where $\mathcal{W}_t$ is the live wing partition and $\mathcal{A}^{\text{fit}}_t$ the set of
combat-fit vessels. Because the Hungarian solve is a **complete** bipartite matching over a
square cost matrix $\mathbf{C} \in \mathbb{R}^{N \times N}$, every slot in $\mathcal{S}_t$ is
filled by exactly one vessel. A role slot cannot disappear when its incumbent is destroyed, so
the next solve necessarily backfills it.

**Doctrinal fill order** (earlier demands are manned first under scarcity):

1. One interceptor on the highest-threat hostile
2. One tracker on the highest-threat hostile
3. **One relay** — the mesh backbone
4. **One scout** — wide-area early warning
5. One interceptor per remaining live wing
6. One tracker per remaining live wing
7. Second and third tracker per live wing
8. One arrest guard per neutralized hull
9. Second relay, then second scout, then surplus trackers

Role-class coverage deliberately outranks spreading force across hostiles: a swarm with no
comms backbone cannot fight at all, whereas an under-engaged second hostile is still tracked
through the mesh. $N=6, M=1$ resolves to `1 interceptor + 3 trackers + 1 relay + 1 scout`,
identical to the prototype's hardcoded split, preserving Stage-1 reproducibility.

**Fitness floors.** $h_{\min} = 0.70$ (interceptor), $0.55$ (tracker), $0.35$ (relay),
$0.25$ (scout). The infeasibility barrier is hard *only while fit alternatives outnumber the
quota*; under scarcity it degrades to a graded penalty, so the least-unfit survivor takes the
role rather than leaving it vacant. Degraded manning is flagged, never silent. Vessels below
$h = 0.25$ are combat losses: excluded from the establishment and routed to rear casualty
stations, with asymmetric penalties ($10^6$ for a wreck holding a role, $10^4$ for a fit hull
standing off) so the correct pairing is strictly optimal.

### 12.2 Tactical Wings and Merge Hysteresis

Hostiles are partitioned into wings by union-find. Two live hostiles share a wing when
$\|\mathbf{p}_i - \mathbf{p}_j\| \le 25\,\text{m}$, releasing only beyond $32\,\text{m}$.
The hysteresis prevents wing chatter, and the merge resolves a geometric deadlock: two
overlapping $14\,\text{m}$ exclusion envelopes would otherwise trap trackers between hostiles.
A merged wing widens its ring to $R + \tfrac{1}{2}\max_{i,j}\|\mathbf{p}_i - \mathbf{p}_j\|$.

### 12.3 Guidance Laws

**Interceptors — proportional navigation.** Lateral acceleration is commanded proportional to
the line-of-sight rate,

$$a_{\text{cmd}} = N_{pn} V_c \dot{\lambda}, \qquad
\dot{\lambda} = \frac{\mathbf{r} \times \mathbf{v}}{\|\mathbf{r}\|^2}, \qquad
V_c = -\frac{\mathbf{r} \cdot \mathbf{v}}{\|\mathbf{r}\|}$$

with $N_{pn} = 3.0$, driving $\dot{\lambda} \to 0$: a true collision course onto the lead point
$\mathbf{p}_T + \mathbf{v}_T t_{go}$. Unlike pure pursuit this *leads* a crossing hostile, so
interceptor tracks visibly curve ahead of the target instead of tail-chasing.

**Trackers — standoff orbit of a moving hostile.** The command is composed in the hostile's own
frame and transformed back to the ground frame:

$$\mathbf{v}_{\text{cmd}} =
\underbrace{-k_r (d - R)\,\hat{\mathbf{r}}}_{\text{radial}} +
\underbrace{k_a (\Delta\phi \cdot R)\,\hat{\mathbf{t}}}_{\text{phase}} +
\underbrace{\mathbf{v}_T}_{\text{feed-forward}}$$

with $k_r = 0.45$, $k_a = 0.30$. Two properties matter:

- The **feed-forward** term $\mathbf{v}_T$ is essential. A pure orbit field describes motion
  relative to the centre only, so without it the ring translates out from under the tracker and
  containment never closes against a hostile making $1.9\,\text{m/s}$.
- The **phase term is signed**. A vessel that has run ahead of its station is commanded
  retrograde and drops back. A forward-only trim lets every vessel catch up but none fall back,
  which piles the whole ring onto a single arc.

Circulation is uniform across the ring; counter-rotating traffic on a shared ring is a standing
collision hazard, so station error is trimmed with speed rather than direction.

**Engagement phases.** `LAUNCH → TRANSIT → DEPLOY → SHADOW → PINCER → TERMINAL → EMP_LOCK →
ARREST_GUARD`, with `RECOVERY` and `COMBAT_LOSS` branches. Damage outranks geometry, so a
crippled hull peels off instead of pressing an attack it cannot complete.

### 12.4 How MAPPO and the Hungarian Allocator Divide the Work

The allocator decides **which** hostile each vessel is committed to and **which** station it
holds; FA-MAPPO and the guidance laws decide **how** it flies there. Authority granted to the
frozen actor is phase-dependent — highest where it was trained (formation holding), and **zero**
on the safety-critical terminal strike leg, which deterministic PN owns outright:

| Phase | MAPPO authority | Law flown |
|---|---|---|
| `LAUNCH` / `TRANSIT` | 10% / 15% | waypoint + lane deconfliction |
| `DEPLOY` | 30% | waypoint / orbit field |
| `SHADOW` | **45%** | standoff orbit field |
| `PINCER` | 35% | standoff orbit field |
| `TERMINAL` / `EMP_LOCK` | **0%** | proportional navigation |
| `ARREST_GUARD` | 25% | standoff orbit field |
| `RECOVERY` / `COMBAT_LOSS` | 0% | waypoint / all stop |

The split is published live in the `[FA-MAPPO / GUIDANCE SPLIT]` telemetry block, alongside each
vessel's Hungarian allocation cost, so the division of labour is observable rather than implied.

### 12.5 Velocity-Aware Control Barrier Functions

Each constraint $h(\mathbf{p}) = \|\mathbf{p}_i - \mathbf{p}_{\text{obs}}\| - d_{\text{barrier}}$
is enforced through the class-$\mathcal{K}$ condition $\dot{h} + \lambda h \ge 0$, bounding the
permitted closing rate and hence the commanded surge:

$$u \cos\beta \;\le\; \lambda\,(d - d_{\text{barrier}})\,\sigma \;-\; \dot{d}_{\text{obs}}$$

with $\lambda = 0.9$, obstacle share $\sigma = 0.5$ for symmetrically-braking allies and
$\sigma = 1$ otherwise, and the barrier placed $0.5\,\text{m}$ outside $d_{\text{safe}}$ to
absorb first-order actuator lag ($\tau \approx 0.5\,\text{s}$). This is strictly better than a
position-only throttle in both directions:

- it intervenes early enough on a fast closure that the invariant is never crossed;
- it does **not** brake a vessel that is merely near an obstacle while opening or passing
  tangentially, so the swarm threads past drifting wrecks instead of jamming behind them.

**Barrier tiers.** Inter-ally $d \ge 4.0\,\text{m}$ (never relaxed, any role or phase);
$14.0\,\text{m}$ exclusion envelope around *every* hostile for non-interceptors; a
$4.2\,\text{m}$ capture corridor granted to an interceptor on its **assigned** hostile only,
with a zero-thrust brake at $4.0\,\text{m}$. The pincer floor is $15.5\,\text{m}$ — deliberately
outside the exclusion envelope, so guidance never commands trackers onto a station the shield
forbids. A vessel already inside an envelope receives a composite tangential escape course at
reduced-but-nonzero speed; zeroing thrust inside a moving hostile's envelope would leave it to
be run down.

### 12.6 Coordinated Hostile Behaviour

Hostiles fight the encirclement as a group, not as individuals:

- **Open-sector escape** — reads the bearings of every pursuer and drives the widest unguarded
  sector of its own perimeter.
- **Split evasion** — biases escape vectors away from live partners, forcing the swarm to divide.
- **Escort formation** — escorts hold station $38\,\text{m}$ off the flagship's quarters
  (deliberately outside the wing-merge radius, so the hostile formation does not merge its own
  wings while unthreatened).
- **Mutual support** — an unengaged hostile makes a relief run toward a partner under EMP lock.
- **Breakout burst** — $1.95\,\text{m/s}$ sprint when a wide sector opens with pursuers still
  beyond $22\,\text{m}$; choked to $1.15\,\text{m/s}$ when flanked inside $16\,\text{m}$.
- **Propulsion damage** — EMP lock compounds with geometry: a hull both flanked and under lock
  is slower than one suffering either alone, decaying to $0.00\,\text{m/s}$ at full arrest.

### 12.7 Verified Results

`ros2 launch usv_bringup swarm_simulation.launch.py` (defaults: $N=12$, $M=3$,
`scenario:=role_attrition`), 170 s mission:

| Criterion | Result |
|---|---|
| All hostiles defused & arrested | **3 / 3 at 0.00 m/s** |
| Inter-ally collision invariant $d \ge 4.0\,\text{m}$ | **Held — worst 5.06 m** |
| Role vacancies (any canonical role unmanned) | **0** |
| Combat losses sustained | 3 of 12 (relay, scout, tracker — each backfilled) |
| Role manning at mission end | INT 3/3, TRK 4/4, RLY 1/1, SCT 1/1 (all fit) |
| Reallocation events | 103, event-driven only (0 during steady state) |

The `role_attrition` scenario destroys whichever vessel *currently holds* each role, so the
establishment must re-man it from the survivors — e.g. relay destroyed at $t=22\,\text{s}$,
backfilled by a tracker within one planner tick, with the formation re-forming around the
replacement.

---

## 13. Autonomous Damage Control (Self-Healing)

Degradation in TritonSwarm is **transient, not cumulative**. Every fault the injector can
produce -- including a total combat loss -- is survivable, and the recovered vessel is
re-tasked back into the formation.

### 13.1 Recovery Pipeline

```
 fault / combat loss  ->  adrift  ->  damage control  ->  re-enters establishment  ->  re-tasked
   h -> 0.00                16 s        12 s Hermite ramp      h >= 0.25               on station
   thrust -> 0.00                       h, thrust -> 1.00
```

1. **Loss.** The hull goes dead in the water ($h = 0$, thrust $= 0$). `usv_sim_core` zeroes its
   propulsion, so it drifts; the allocator drops it from the establishment and routes it to a
   rear casualty station.
2. **Backfill (immediate).** Because slots come from the establishment rather than the
   surviving role vector (§12.1), the vacated role is re-manned by the next Hungarian solve --
   measured at **≤ 1.0 s** of sustained vacancy.
3. **Damage control.** After `repair_delay` (16 s) the repair ramp begins, interpolating health
   and thrust back to nominal over `repair_duration` (12 s) along a smooth Hermite profile
   $\alpha_s = 3\alpha^2 - 2\alpha^3$, so thrust returns gradually rather than stepping.
4. **Re-entry.** Crossing $h \ge 0.25$ puts the vessel back in the establishment. Since the
   establishment signature includes the fit-vessel count, the watchdog re-resolves immediately
   rather than waiting for its 1 Hz tick.
5. **Re-promotion.** Crossing $h \ge 0.80$ raises a `HEALTHY_RESTORED` recovery event, and the
   vessel becomes eligible for the full role taxonomy again.

Verified live (`scenario:=role_attrition`):

```
t=22.0s  [COMBAT LOSS]              USV-0 [RELAY] destroyed, dead in the water
t=22.1s  [REALLOC]                  USV-2 SCOUT -> RELAY (cost 29.6)      <- role re-manned
t=38.0s  [DAMAGE CONTROL]           recovery of USV-0 begun after 16.0s adrift
         ...                        h: 0.00 -> 0.04 -> 0.16 -> ... (UNDER REPAIR)
t=50.0s  [DAMAGE CONTROL COMPLETE]  USV-0 restored to h=1.00, back in the fight
```

The fleet therefore finishes the mission at **12/12 operational** despite sustaining three
combat losses, against a worst-case trough of 10/12. Set `self_healing:=false` to disable it
and reproduce cumulative attrition (this is how the ablated baseline in §14 is run).

### 13.2 Doctrine Corrections Found by Live Measurement

Three design errors were only exposed by instrumented live runs, and each is now covered by a
regression test:

| Defect | Symptom | Correction |
|---|---|---|
| **Scissor bias** displaced the two flank ring stations inward to "seal the escape sector" | It opened a ~117° hole elsewhere on the ring; Section 5 criterion 2 failed for entire engagements | Removed. Even phasing minimises the maximum angular gap *by construction*, which is exactly what the criteria measure; the squeeze comes from collapsing the radius alone |
| **Stations were chased, not led** | A ring station orbiting a moving hostile translates while the vessel transits toward it, so the fleet closed at only (own − hostile) speed and strung out on one bearing instead of encircling | `lead_station()` projects the station forward by the exact solution of the interception quadratic $(\lvert v\rvert^2 - s^2)t^2 + 2(\mathbf{R}\cdot\mathbf{v})t + \lvert\mathbf{R}\rvert^2 = 0$. Time to arrest fell from 96 s to 53 s |
| **Strike released before containment** | The interceptor simply ran the hostile down alone; the encirclement the mission is specified around never formed, and the arrest masked it | The strike element now **stages at 22 m** until the wing's containment ring is established, judged over the containment element only so the strike cannot authorize itself. A wing below ring strength is released after 85 s under *explicitly degraded* containment rather than deadlocking |

---

## 14. Architecture Ablation Study (Comparative Simulation)

`usv_comparative_eval` provides a **control condition**: the same fleet, the same hostiles, the
same physics, the same fault schedule and the same win condition, with the TritonSwarm
intelligence stack removed and replaced by one conventional swarm controller.

```bash
./run_baseline_sim.sh          # run the ablated baseline alone
./run_rviz.sh baseline         # visualize it (crimson ocean grid, so the two are distinguishable)
./run_comparison.sh 190 12 3   # run BOTH conditions headless and print the comparison
```

### 14.1 Ablations and the Failure Mode Each Produces

| Ablation | Replaced by | Failure mode |
|---|---|---|
| `NO_ALLOCATOR` | static role table fixed at startup | `ROLE_VACANCY` — a role dies permanently with its holder |
| `NO_WING_PARTITION` | independent greedy nearest-target | `FORCE_DOGPILE`, `ASSIGNMENT_THRASH` |
| `NO_LEAD_GUIDANCE` | pure pursuit onto the last measured position | `TAIL_CHASE`, `CONTAINMENT_FAIL` |
| `NO_CBF_GUARANTEE` | conventional boids separation (7 m, gain 1.8) | `COLLISION`, `HOSTILE_IMPACT`, `STALL` |
| `NO_STATE_ESTIMATION` | raw noisy measurement, 25% dropout, no fusion | `TRACK_ERROR`, `TRACK_LOSS` |
| `NO_FAULT_DIAGNOSIS` | health published but never consulted | `UNDETECTED_FAULT` |
| `NO_DAMAGE_CONTROL` | `self_healing:=false` | `ATTRITION` |
| `NO_DEADLOCK_ESCAPE` | no tangential escape, no repulsion budget | `STALL` |

The baseline **keeps conventional boids separation on purpose**. The study compares a formal
safety guarantee against a soft heuristic, not against nothing at all; an unprotected swarm
would be a straw man. What the heuristic lacks is a closing-rate ($\dot h$) term and any
worst-case bound, so it reduces hull strikes without ever preventing them.

### 14.2 Impartial Scoring

`scorecard_node` is launched by **both** stacks with identical thresholds, and reads only
ground-truth kinematics, ground-truth health, published roles and published beliefs. Neither
architecture can influence how it is judged. Containment is assessed purely geometrically --
any tracker or interceptor inside the ring band counts regardless of what the stack *claims*
it is assigned to -- so the baseline is penalised for poor results, never for poor bookkeeping.

Two measurement traps were identified and corrected during validation:

- **Arrest credit.** An arrested hull counts as contained, which rewards whoever disabled a
  hostile earliest rather than whoever encircled better — and initially made the *baseline*
  appear to out-encircle the full stack. Encirclement is therefore reported over **live
  hostile-seconds only**, with the arrest-inclusive figure kept as informational.
- **Path efficiency.** Orbiting a hostile is the correct behaviour and inherently lowers a
  net-displacement/path-length ratio. That row is reported **without a verdict** rather than
  scored as a loss.

The decisive metric is **CLEAN non-destructive arrests**. The brief is a non-destructive
arrest, so a hostile stopped by being rammed is disabled but *not* captured — and ramming is
exactly what a swarm with no barrier function does, because nothing brakes it at the 4.0 m
arrest boundary.

### 14.3 Results — 12 drones vs 3 hostiles, 190 s, `role_attrition`

| Metric | Full architecture | Ablated baseline | Verdict |
|---|---|---|---|
| **MISSION CLEAN** (all criteria) | **PASS** | FAIL | Full |
| **CLEAN non-destructive arrests** | **3 / 3** | 0 / 3 | Full |
| Hostiles disabled (incl. ramming) | 3 / 3 | 3 / 3 | tie |
| Drone-on-hostile hull strikes | **0** | 3 | Full |
| Closest approach to a hostile | 3.63 m | 2.83 m | Full |
| Colliding drone pairs | **0** | 5 | Full |
| Worst inter-drone separation | **4.32 m** | 2.50 m | Full |
| Time below the 4.0 m invariant | **0.0 s** | 156.8 s | Full |
| Longest **sustained** role vacancy | **1.0 s** | 160.1 s | 160× |
| — relay unmanned | 1.0 s | 160.2 s | 160× |
| — scout unmanned | 1.1 s | 142.2 s | 129× |
| Hostile tracking RMSE | **2.70 m** | 18.31 m | 6.8× |
| Assignment changes | **64.7 /min** | 184.2 /min | 2.8× |
| Mean fleet availability | **97.3 %** | 29.4 % | 3.3× |
| Drones operational at the end | **12 / 12** | 1 / 12 | 12× |

At a second operating point (**12 drones vs 1 hostile**, where the fleet is large enough to
ring the target) encirclement itself separates the two: **22.5 % of live hostile-time encircled
versus 0.0 %**, with the strike released only on `containment ring established with 6 trackers,
max gap 1.58 ≤ 1.68 rad`.

### 14.4 Where the Baseline Wins, and Why That Is Not a Better Outcome

The study is reported honestly, including the rows the baseline takes:

- **Time to disable (26.5 s vs 108.1 s).** The baseline is faster because nothing brakes its
  approach and nothing makes it wait for containment. It reaches the objective by ramming it,
  loses drones to hull strikes, and leaves roles permanently unmanned. Against a
  non-destructive arrest brief, arriving sooner this way is a mission failure, not a win.
- **Transit directness (0.864 vs 0.521).** Flying straight at a target is direct; orbiting it
  is not. Containment requires orbiting, so this row is informational.

### 14.5 A Structural Limit, Stated Plainly

At 12 drones against 3 hostiles, **neither** architecture sustains three simultaneous Section 5
rings, and the metric correctly reports 0.0 % for both. Three rings need 3 trackers each plus
3 interceptors — 12 vessels — leaving nothing for the relay backbone or the scout picket. The
full architecture still wins the engagement by sequencing: wings re-task onto the live hostiles
as each arrest completes. Simultaneous three-hostile containment needs a fleet of roughly
15–18, and the establishment resolver scales to it without modification.

---

## 15. Formation Stability Corrections

A second round of instrumented runs showed the fleet *visibly milling about* for the first
~20 s of every mission and drones repeatedly turning around for a different hostile. Metrics
alone had not caught it, because the mission still completed. Five causes, all now regression
tested:

| Cause | Mechanism | Correction |
|---|---|---|
| **Unbounded lead horizon** | `lead_station()` extrapolated hostile velocity up to 60 s. Hostiles weave on a ~3 s period and switch tactic every few seconds, so the aim point swung tens of metres per tick and no vessel ever settled on a transit course | Lead clamped to `T_MANEUVER_HORIZON = 8.0 s`. The exact intercept solution is still used, then bounded to the window over which the hostile's velocity is actually predictive |
| **Hostiles spawned inside the wing merge band** | Escorts spawned 27.8 m from the flagship, between the 25 m merge and 32 m split radii, and station-kept at 38 m. The ally wings therefore merged and split repeatedly (3 → 2 → 1 → 2 → 3 wings) and *every* transition invalidated the station lattice and reshuffled the whole fleet | Formation spacing widened (`along 20 m`, `across 46 m`) and `R_ESCORT_STATION` raised to 52 m, so every hostile pair starts and stays outside the band |
| **Hysteresis band too narrow** | A 25/32 m band turned ordinary weaving into merge/split chatter | Widened to **22 m merge / 40 m split** |
| **Fixed wing-switch penalty** | A flat 45 (then 120) cost cannot work at every range: during the launch transit the fleet is bunched 85 m out with hostiles 90 m apart, so station distances to two wings differ by far more than any fixed cost and the assignment is nearly degenerate | Penalty is now `120 + 0.5 × distance` — scale-invariant, so a switch is only chosen when the other wing is dramatically closer. Still waived entirely once a hostile is neutralized, so force freed by an arrest re-tasks for free |
| **Threat-ranked surplus force** | Wings were ordered by raw threat, and hostile speed legitimately swings between regimes (contested 1.15 → evade 1.65 → burst 1.95 m/s). The ranking flipped every few seconds, moving the surplus tracker slot between wings and making that drone shuttle for the whole engagement | Ordering is by `THREAT_BAND = 2.5 m/s` then strictly by wing id. The band is wider than the entire evasion envelope, so evasive swings cannot re-rank; a genuinely faster class of hostile still draws surplus force |

Measured effect over a 90 s mission:

| | Before | After |
|---|---|---|
| Wing partitions observed | 1, 2 and 3 (chattering) | **3 (stable)** |
| Total wing changes | 35 | **14** |
| Wing changes in the first 18 s | 11 | **2** |
| Reallocation events | 70 | **50** |
| Collision invariant breaches | 0 | **0** |

The residual changes are event-driven: each cluster sits within ~2 s of a combat loss, a
damage-control restoration, or an arrest freeing a wing — which is the allocator doing its job.

### 15.1 Two Measurement Artifacts Corrected

The scorer was also penalising correct behaviour:

- **Stall threshold.** A stall was any immobile drone more than 12 m from its hostile, which
  flagged the strike element correctly staging at 22 m, the relay on its 30 m backbone and the
  scout on its 45 m picket. `STALL_RANGE` is now **55 m**, outside every doctrinal station, so
  a flagged stall means genuinely stuck. A test now asserts that *every* station radius
  (4.8 / 15.5 / 20 / 22 / 30 / 45 m) scores zero stall time.
- **Stack identity.** Both stacks publish `/usv_swarm/markers`, so in RViz they were visually
  indistinguishable and it was impossible to tell which one was on screen. `usv_sim_core` now
  takes a `stack_label` parameter, set by each launch file, and prints it as the first line of
  the battle HUD.

---

## 16. Reverted: Strike Staging Gate and Lead-Station Transit

Two changes introduced alongside the ablation study were **reverted**, because they degraded
the architecture simulation they were supposed to be measuring. The entries describing them in
§13.2 and §15 are superseded by this section.

### 16.1 What was wrong

**Strike staging gate.** §12 held the strike element at a 22 m staging station until its wing's
containment ring was established, releasing it under "degraded containment" after a timeout.
At the default operating point this was unsatisfiable by construction: §14.5 records that
12 drones cannot ring 3 hostiles simultaneously, because three rings need 3 trackers each plus
3 interceptors — the entire fleet — leaving nothing for the relay backbone or the scout picket.

The gate therefore *never* passed at 12 v 3. Its only effect was to idle all three interceptors
for the full 85 s timeout before releasing them to do exactly what they had done before:

```
[STRIKE AUTHORIZED - DEGRADED] >>> HOSTILE-0: no containment ring after 85s (1 tracker on station)
[STRIKE AUTHORIZED - DEGRADED] >>> HOSTILE-1: no containment ring after 85s (2 trackers on station)
[STRIKE AUTHORIZED - DEGRADED] >>> HOSTILE-2: no containment ring after 85s (1 tracker on station)
```

**Lead-station transit.** `lead_station()` projected ring stations forward along the hostile's
velocity during transit. It improved the encirclement metric, but added a prediction layer on
top of guidance that already converges, and it had itself needed clamping (§15) after an
unbounded horizon made the aim point thrash.

### 16.2 Why they were introduced, and why that was the wrong call

Both existed to raise one number: `active_containment_frac` in the comparison study. Neither
improved the mission. The gate traded 85 s of idling for a metric that is structurally
unreachable at the default fleet size — tuning the architecture to flatter its own benchmark
rather than to accomplish the task. The benchmark is supposed to measure the architecture, not
steer it.

### 16.3 Measured effect of the revert

Identical configuration throughout (12 drones, 3 hostiles, `role_attrition`):

| | First engagement | All 3 arrested by | Staging gate fired |
|---|---|---|---|
| Before the ablation study | 45.0 s | 85.5 s | — |
| With the staging gate | **99.0 s** | **118.5 s** | 3 times |
| **After the revert** | **28.5 s** | **79.5 s** | never |

The reverted simulation is *faster than the original*, because the formation-stability work of
§15 was kept: the fleet commits to its wings immediately instead of thrashing, so it reaches
the engagement sooner. The collision invariant still holds at zero breaches.

### 16.4 What was kept

- RViz camera control and windowed default (§15.1)
- Autonomous damage control / self-healing (§13)
- All formation-stability corrections: hostile spawn spacing, the 22/40 m wing hysteresis band,
  the scale-invariant wing-switch penalty, and `THREAT_BAND` (§15)
- The comparison study itself (§14), which now runs entirely from its own launch file

`active_containment_frac` consequently reads near zero for both stacks at 12 v 3. That is the
honest result, already stated in §14.5: the fleet is below the strength needed for simultaneous
three-hostile containment. The architecture wins that engagement by sequencing wings onto each
arrest, and the comparison remains decisive on clean arrests, hull strikes, collisions, role
continuity, tracking accuracy and fleet survival.

---

## 17. Standing Garrisons: Dividing the Fleet Across Arrested Hulls

Previously an arrested hull kept a single sentry and the rest of its wing re-tasked to the live
fight. An arrested hull is now **garrisoned**: it keeps a complete sub-formation, and once every
hostile is down the fleet has divided itself roughly equally between them, every role still
doing its own job.

### 17.1 Garrison Composition

Each arrested hull holds:

| Element | Station | Behaviour |
|---|---|---|
| Strike element (interceptor) | `R_GARRISON_ESCORT` = 9.5 m | circles the hull close in |
| Containment element (trackers) | `R_ARREST_GUARD` = 15.5 m | evenly phased ring, circles the hull |

The two rings are **counter-phased** (the escort sits opposite the first ring station) so they
never stack up, and 9.5 m sits inside 15.5 m so the elements stay radially separated. Both rings
**precess** at `GARRISON_SPIN_RATE = 0.10 rad/s`: the station each vessel holds sweeps around the
hull, so the existing phase-lock guidance produces continuous circulation. Without that the
garrison would converge on a fixed bearing and park, because a stationary hull supplies no
velocity feed-forward to keep the orbit moving.

Fleet-level roles are **not** garrison members, and are explicitly excluded:

- **Relay** — bridges the dispersed garrisons. With several garrisons the backbone's job is no
  longer to sit astern of one engagement box, so relays are stationed on the **chords between
  hull positions**; a lone relay holds the most central station it can. Either way the station
  is pushed clear of every hull by `clear_of_hulls()`, because with three collinear hostiles the
  formation centroid *is* the middle hull's position and a relay placed there would be commanded
  onto the hull and shoved off it by the safety shield for the rest of the mission.
- **Scout** — screens the whole formation. The picket radius grows with how far apart the
  garrisons are (`max(45, extent/2 + 25)`), so a dispersed fleet is not screened by a ring
  sitting inside it.

### 17.2 Equal Division, Without Abandoning the Live Fight

Live wings and garrison wings are ordered into one demand list — **live first**, so a hostile
still under way is never stripped of force to garrison an arrested one — and then filled by a
single rule. Rings are built out in step, so force divides evenly across every hull the fleet is
responsible for instead of one engagement absorbing all of it.

The lead wing is completed before the fleet spreads out, which matters for the no-downtime
invariant: giving every hull its strike element before any hull gets a tracker would leave the
**tracker role unmanned** on a small fleet. A test now asserts every role stays manned in the
fully-arrested state for every fleet size from 4 to 16.

### 17.3 Verified Result — 12 drones, 3 hostiles, 175 s

Garrisons forming as each arrest completes:

```
garrisons H1:3
garrisons H1:3, H2:3
garrisons H0:3, H1:3, H2:3
garrisons H0:4, H1:3, H2:3      <- fleet divided 4/3/3
```

Final formation, all three hostiles arrested:

| Hull | Strike element | Containment ring |
|---|---|---|
| H0 | USV-7 @ 9.6 m | USV-1, USV-9, USV-11 @ 16.2–16.6 m |
| H1 | USV-8 @ 9.6 m | USV-5, USV-6 @ 16.3–16.5 m |
| H2 | USV-4 @ 9.6 m | USV-0, USV-10 @ 16.2–16.8 m |

plus **USV-3 RELAY** on the mesh backbone bridging all three garrisons, and **USV-2 SCOUT** on
the wide-area picket screening the formation. Both fly `WAYPOINT` guidance, not the garrison
orbit — they are keeping the formation connected and screened, not circling a hull.

| | Result |
|---|---|
| Role manning | INT 3/3, TRK 7/7, RLY 1/1, SCT 1/1 — all fit |
| Garrison division | 4 / 3 / 3 (equal within one vessel) |
| Containment spread per hull | 3.96, 3.15, 3.16 rad — the garrisons genuinely encircle |
| Ring precession | 260° at t=171 s — continuously circling |
| Collision invariant | held, worst separation 4.53 m |
| Fleet | 12/12 operational |

### 17.4 A Measurement Fault Found Along the Way

The first garrison run appeared to show all three hostiles **frozen at non-spawn positions with
zero displacement**. The cause was not the simulation: a complete set of eight nodes from an
earlier run was still alive. On SIGINT, rclpy tears the context down from its own signal handler,
so `rclpy.spin()` raises `ExternalShutdownException` rather than `KeyboardInterrupt`; that was
uncaught, and `rclpy.shutdown()` was then called on an already-dead context. Nodes survived the
launch exiting, and a surviving `sim_core` published onto the same topics as the next run — two
simulators fighting over `/target_{m}/odom`.

Fixed in all nine nodes:

```python
except (KeyboardInterrupt, ExternalShutdownException):
    pass
finally:
    node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()
```

`run_stage2_sim.sh`, `run_baseline_sim.sh` and `run_comparison.sh` now also refuse to start if
simulation nodes are already running, naming the processes and the command to clear them, so a
run can never be silently corrupted this way again.
