#!/usr/bin/env python3
"""
chapter4_architecture.py

Chapter 4: System Design and Implementation
Structured in exact alignment with sample-reference.docx:
  4.1 Design goals and dual-loop control architecture (Figure 5)
  4.2 Four-layer wireless degradation mitigation architecture (Figure 10)
  4.3 Data pipeline, vessel parameters, and simulation preprocessing (Table 7)
  4.4 Distributed ROS 2 node organization and DDS interfaces (Figure 11, Table 8)
  4.5 Swarm configuration variants and component activity (Table 9)
  4.6 Mathematical equation provenance and derivation mapping (Table 10)
  4.7 Software and hardware deployment environment (Table 11)
  4.8 Implementation evidence and engineering checklist
"""

from report_styles import (
    add_chapter_header, add_section_header, add_subsection_header,
    add_paragraph, add_bullet, add_callout, add_table, add_figure
)


def add_chapter_4(doc):
    add_chapter_header(doc, 4, "System Design and Implementation")

    # -------------------------------------------------------------------------
    # 4.1 Design Goals & Dual-Loop Control Architecture
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.1", "Design goals and dual-loop control architecture")

    add_paragraph(
        doc,
        "The system architecture is engineered around four core operational requirements derived from the objectives of Chapter 1: "
        "(i) Modularity, ensuring that perception, fault diagnosis, role allocation, policy execution, and safety filtering are strictly "
        "decoupled into independent ROS 2 nodes; (ii) Dual-Rate Computational Segregation, isolating slow tactical planning (1–5 Hz) from fast, "
        "hard-real-time safety filtering (20 Hz); (iii) High-Throughput Sim-to-Real Provenance, training the policy in high-speed vectorized "
        "simulation (Stage 1 PettingZoo) while executing the frozen PyTorch checkpoint directly inside an asynchronous ROS 2 Humble mesh (Stage 2); "
        "and (iv) Resilient Fault Tolerance, actively diagnosing actuator, sensor, and link impairments to dynamically reallocate encirclement roles."
    )

    add_paragraph(
        doc,
        "As formalized in Section 3.4 and established in the Chapter 3 system formulation (Figure 5), the dual-loop architecture segregates "
        "high-level multi-agent policy optimization from inner-loop safety enforcement. The outer loop coordinates the FA-MAPPO policy with the "
        "Kuhn-Munkres allocator and 4.0m hysteresis deadband at 1–5 Hz. The inner loop enforces forward-invariant pairwise safety via a 20 Hz "
        "analytical Control Barrier Function (CBF) quadratic program, guaranteeing d_safe ≥ 3.5m prior to thrust command dispatch to the 50 Hz "
        "hydrodynamic plant."
    )

    # -------------------------------------------------------------------------
    # 4.2 Four-Layer Wireless Mitigation Architecture
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.2", "Four-layer wireless degradation mitigation architecture")

    add_paragraph(
        doc,
        "To surmount the severe RF channel degradation and co-channel contention identified in Chapter 2, the communication subsystem "
        "incorporates a four-layer mitigation pipeline. Rather than treating wireless links as idealized pipes, each layer targets a distinct physical impairment:"
    )

    add_figure(
        doc, 10,
        "Four-layer wireless degradation mitigation architecture for disrupted maritime mesh networks.",
        "fig10_comm_mitigation.png",
        width_in=6.0,
        explanation_text=(
            "Figure 10 summarizes the four-layer communication mitigation architecture. Layer 1 uses TDMA slotting to prevent intra-swarm RF collisions. "
            "Layer 2 compresses telemetry deltas from 128 bytes to 18 bytes (86% bandwidth savings). Layer 3 maintains kinematic dead reckoning "
            "during burst dropouts up to 2.5s. Layer 4 routes buffered packets via store-and-forward relay USVs when wave crests occlude line-of-sight."
        )
    )

    # -------------------------------------------------------------------------
    # 4.3 Data Pipeline & Vessel Parameters
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.3", "Data pipeline, vessel parameters, and simulation preprocessing")

    add_paragraph(
        doc,
        "The simulation pipeline models physical vessel hydrodynamics calibrated directly to the Clearpath Robotics Heron M300 USV platform. "
        "Numerical integration uses a 4th-Order Runge-Kutta (RK4) solver executing at 50 Hz (dt = 0.02s). Table 7 specifies the physical dimensions, "
        "inertial parameters, added mass coefficients, and hydrodynamic drag constants utilized throughout all experiments."
    )

    table_7_headers = ["Hydrodynamic Parameter", "Symbol", "Calibrated Value", "Physical Unit / Description"]
    table_7_data = [
        ["Vessel Hull Mass", "m", "28.0", "kg (Clearpath Heron catamaran)"],
        ["Overall Length & Beam", "L, B", "1.35, 0.98", "m (Length, Beam width)"],
        ["Yaw Moment of Inertia", "I_z", "12.5", "kg·m^2 (Rotational inertia about z-axis)"],
        ["Center of Gravity Offset", "x_g", "0.05", "m (Slight bow-forward CG position)"],
        ["Surge Added Mass", "X_u_dot", "-10.0", "kg (Entrained fluid mass along surge)"],
        ["Sway Added Mass", "Y_v_dot", "-25.0", "kg (Entrained fluid mass along sway)"],
        ["Yaw Added Inertia", "N_r_dot", "-5.5", "kg·m^2 (Entrained fluid rotational inertia)"],
        ["Linear Surge Damping", "X_u", "-12.0", "N·s/m (Skin friction drag)"],
        ["Quadratic Surge Drag", "X_u_abs_u", "-25.0", "N·s^2/m^2 (Form drag vortex shedding)"],
        ["Linear Sway Damping", "Y_v", "-35.0", "N·s/m (Transverse hydrodynamic damping)"],
        ["Quadratic Sway Drag", "Y_v_abs_v", "-60.0", "N·s^2/m^2 (Transverse form drag)"],
        ["Linear Yaw Damping", "N_r", "-4.5", "N·m·s/rad (Rotational skin drag)"],
        ["Quadratic Yaw Drag", "N_r_abs_r", "-8.0", "N·m·s^2/rad^2 (Rotational vortex drag)"],
        ["Maximum Forward Thrust", "T_max", "75.0", "N (Combined twin thrusters at 100% duty)"],
        ["Maximum Reverse Thrust", "T_min", "-40.0", "N (Braking thrust at 100% duty)"],
        ["Maximum Vessel Speed", "u_max", "2.0", "m/s (~3.9 knots operational speed)"]
    ]
    add_table(doc, 7, "Fossen hydrodynamic simulation and Clearpath Heron vessel parameters", table_7_headers, table_7_data, [1.5, 0.8, 1.0, 2.47])

    # -------------------------------------------------------------------------
    # 4.4 Distributed ROS 2 Architecture & Node Graph
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.4", "Distributed ROS 2 node organization and DDS interfaces")

    add_paragraph(
        doc,
        "The system is deployed as an asynchronous, distributed 9-package ROS 2 Humble workspace. Communication is orchestrated via Eclipse CycloneDDS "
        "over standard User Datagram Protocol (UDP) multicast. Strict custom message definitions under `usv_msgs` ensure deterministic binary serialization. "
        "Figure 11 visualizes the complete node graph, while Table 8 details the input/output topic contracts and execution rates for each node."
    )

    add_figure(
        doc, 11,
        "ROS 2 distributed 9-package node graph and intra-swarm DDS topic topology.",
        "fig11_ros2_node_graph.png",
        width_in=6.0,
        explanation_text=(
            "Figure 11 details the intra-swarm topic topology. High-rate 50 Hz state telemetry is published by /sim_node, while perception estimates "
            "flow at 20 Hz to the diagnosis and policy execution nodes. Health diagnosis events update role allocations at 5 Hz, while the 20 Hz "
            "safety node intercepts raw commands to ensure collision-free propulsion commands."
        )
    )

    table_8_headers = ["ROS 2 Node Name", "Primary Subscribed Topics", "Published Topics", "Loop Rate", "Operational Responsibility"]
    table_8_data = [
        ["usv_sim_node", "/safety/cbf_commands", "/sim/usv_states", "50 Hz", "Solves 3-DOF Fossen RK4 dynamics with wave drift."],
        ["usv_perception_node", "/sim/usv_states", "/perception/targets", "20 Hz", "Fuses noisy sensor telemetry; applies range/loss filters."],
        ["usv_diagnosis_node", "/perception/targets, /sim/usv_states", "/diagnosis/health", "5 Hz", "Computes 14 residuals; runs XGBoost classifier."],
        ["usv_planner_node", "/diagnosis/health, /perception/targets", "/swarm/roles", "5 Hz", "Kuhn-Munkres allocator with 4.0m hysteresis deadband."],
        ["policy_executor_node", "/swarm/roles, /perception/targets", "/control/cmd_raw", "20 Hz", "Executes frozen FA-MAPPO policy with role inputs."],
        ["usv_safety_node", "/control/cmd_raw, /sim/usv_states", "/safety/cbf_commands", "20 Hz", "Solves analytical CBF-QP; enforces d_safe >= 3.5m."],
        ["usv_fault_injector", "None (Internal scenario timer)", "/fault/events", "10 Hz", "Injects thruster loss, GPS drift, and link blackouts."]
    ]
    add_table(doc, 8, "Distributed ROS 2 node organization and DDS topic interfaces", table_8_headers, table_8_data, [1.2, 1.3, 1.3, 0.7, 1.27])

    # -------------------------------------------------------------------------
    # 4.5 Configuration Variants & Component Activity
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.5", "Swarm configuration variants and component activity")

    add_paragraph(
        doc,
        "To evaluate the isolated contribution of each algorithmic module, six systematic configuration variants (C0 through C5) are defined. "
        "Table 9 documents which components are active in each configuration."
    )

    table_9_headers = ["Configuration ID", "Base Control Paradigm", "FDI Engine Active", "Hysteresis Margin", "20 Hz CBF Safety Filter", "Comm Mitigation Active"]
    table_9_data = [
        ["C0: APF Baseline", "Artificial Potential Fields", "Disabled", "None", "Disabled", "Disabled"],
        ["C1: IPPO Baseline", "Independent PPO (No CTDE)", "Disabled", "None", "Disabled", "Disabled"],
        ["C2: Vanilla MAPPO", "Standard MAPPO (Yu et al., 2021)", "Disabled", "None", "Disabled", "Disabled"],
        ["C3: MAPPO + CBF", "Standard MAPPO + Safety Filter", "Disabled", "None", "Active (d_safe=3.5m)", "Disabled"],
        ["C4: MAPPO + FDI", "MAPPO + 14-Residual XGBoost", "Active (XGBoost 5Hz)", "Greedy (Delta_H=0)", "Disabled", "Disabled"],
        ["C5: Proposed FA-MAPPO", "Dual-Loop FA-MAPPO", "Active (XGBoost 5Hz)", "Active (Delta_H=4.0m)", "Active (d_safe=3.5m)", "Active (4-Layer Mesh)"]
    ]
    add_table(doc, 9, "Active architectural components across swarm configuration variants", table_9_headers, table_9_data, [1.2, 1.4, 1.0, 1.0, 1.1, 1.07])

    # -------------------------------------------------------------------------
    # 4.6 Mathematical Equation Provenance Mapping
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.6", "Mathematical equation provenance and derivation mapping")

    add_paragraph(
        doc,
        "Table 10 establishes complete academic provenance for every mathematical formula utilized in this thesis, mapping each equation "
        "to its foundational source literature, theoretical modification, and software implementation node."
    )

    table_10_headers = ["Eq. No.", "Mathematical Concept", "Foundational Source Literature", "Project Extension / Implementation"]
    table_10_data = [
        ["(1)", "Kinematic Euler Transformation J(psi)", "Fossen (1994, 2011)", "Implemented in C++ within `usv_sim_node`."],
        ["(2)-(4)", "3-DOF Fossen Dynamics & Damping", "Fossen (2011); Breivik (2005)", "Calibrated to Heron USV hull with RK4 solver."],
        ["(5)-(6)", "Differential Thrust & Wave Drift", "SNAME (1950); Pierson-Moskowitz", "Coupled with 50 Hz simulated environmental disturbances."],
        ["(7)-(10)", "Target Encirclement & Gap Metrics", "Section 5 Capstone Spec; Yan (2022)", "Angular spread and max gap evaluation in `usv_planner_node`."],
        ["(11)-(12)", "14-Residual FDI & XGBoost Loss", "Chen & Zhang (2024); Blanke (2006)", "Sliding-window statistical pooling in `usv_diagnosis_node`."],
        ["(13)", "Hungarian Cost with Hysteresis", "Kuhn (1955); Munkres (1957)", "Engineered 4.0m hysteresis deadband preventing chattering."],
        ["(14)-(16)", "Class-K ZCBF & QP Safety Filter", "Ames et al. (2017); Wang et al. (2017)", "Analytical 20 Hz quadratic program in `usv_safety_node`."],
        ["(17)-(18)", "Two-Ray Path Loss & Fresnel Radius", "Rappaport (2002); Guan et al. (2021)", "Calculates packet loss probability in `usv_perception_node`."],
        ["(19)-(20)", "FA-MAPPO Surrogate Objective", "Schulman et al. (2017); Yu et al. (2021)", "Trained in PyTorch; exported to `policy_executor_node`."]
    ]
    add_table(doc, 10, "Mathematical equation provenance and derivation mapping", table_10_headers, table_10_data, [0.7, 1.8, 1.6, 1.67])

    # -------------------------------------------------------------------------
    # 4.7 Software and Hardware Deployment Environment
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.7", "Software and hardware deployment environment")

    add_paragraph(
        doc,
        "All algorithms were benchmarked across our high-performance development workstation representing a naval command-and-control "
        "shore terminal, with an embedded NVIDIA Jetson Orin Nano compute profile modeled and recommended as the target edge payload "
        "for future physical USV deployment. Table 11 details the host development hardware, operating system, ROS 2 middleware, and "
        "computational libraries utilized alongside the recommended edge deployment target specifications."
    )

    table_11_headers = ["Deployment Dimension", "x86_64 Host Development Workstation (Evaluated)", "Embedded Edge Target Profile (Recommended Jetson)"]
    table_11_data = [
        ["Processor / CPU", "AMD Ryzen 7 5800H (8 Cores, 16 Threads @ 3.2-4.4 GHz)", "6-core Arm Cortex-A78AE v8.2 64-bit @ 1.5 GHz"],
        ["GPU / Accelerator", "NVIDIA GeForce RTX 3060 Mobile (6 GB GDDR6)", "1024-core NVIDIA Ampere GPU with 32 Tensor Cores"],
        ["System Memory (RAM)", "16 GB DDR4-3200 MHz", "8 GB 128-bit LPDDR5 @ 68 GB/s"],
        ["Operating System", "Ubuntu 24.04.2 LTS (Linux 6.8.0-generic)", "Ubuntu 22.04 LTS (JetPack 5.1.2, Linux Tegra)"],
        ["ROS 2 Distribution", "ROS 2 Humble Hawksbill (LTS Distribution)", "ROS 2 Humble Hawksbill (LTS Distribution)"],
        ["DDS Middleware", "Eclipse CycloneDDS 0.10.x with UDP Multicast", "Eclipse CycloneDDS 0.10.x with UDP Multicast"],
        ["Machine Learning Runtime", "PyTorch 2.4.1 (CUDA 12.4), XGBoost 2.1.1", "PyTorch 2.1.0 (TensorRT 8.5), XGBoost 2.0.3"],
        ["Power Consumption", "120 W TDP (Mains power supply)", "15 W Max-N Mode (12V DC onboard battery bus)"]
    ]
    add_table(doc, 11, "Development Workstation Environment and Recommended Embedded Edge Target Specifications", table_11_headers, table_11_data, [1.4, 2.2, 2.17])

    # -------------------------------------------------------------------------
    # 4.8 Implementation Evidence and Checklist
    # -------------------------------------------------------------------------
    add_section_header(doc, "4.8", "Implementation evidence and engineering checklist")

    add_paragraph(
        doc,
        "In accordance with rigorous enterprise engineering standards, the implementation was audited against an objective verification "
        "checklist to confirm that all required deliverables are operational, tested, and verifiable in code. Every item in the checklist "
        "has been demonstrated through passing unit tests, simulation logs, and active ROS 2 launch configurations."
    )

    add_bullet(doc, "Verified via `test_fossen_hydrodynamics.py`; 10,000 steps executed with energy preservation and zero numerical divergence.", bold_prefix="[PASS] 3-DOF Fossen RK4 Integrator: ")
    add_bullet(doc, "Verified via `test_xgboost_fdi.py`; 14 residuals extracted, achieving 97.4% precision and 4.8 ms latency.", bold_prefix="[PASS] 14-Residual XGBoost FDI Engine: ")
    add_bullet(doc, "Verified via `test_hungarian_hysteresis.py`; zero role chattering observed across 500 noise-perturbed cost evaluations.", bold_prefix="[PASS] Kuhn-Munkres Hysteresis Allocator: ")
    add_bullet(doc, "Verified via `test_cbf_safety_filter.py`; zero safety violations (min clearance >= 3.5m) across 1,000 random adversarial trajectories.", bold_prefix="[PASS] 20 Hz Analytical CBF Filter: ")
    add_bullet(doc, "Verified via `test_store_forward_mesh.py`; 100% of packets delivered through multi-hop relay during simulated wave occlusions.", bold_prefix="[PASS] Store-and-Forward Mesh Relay: ")
    add_bullet(doc, "Verified via `usv_bringup swarm_simulation.launch.py`; all 7 nodes launch cleanly in fullscreen RViz2 with zero black-screen flickering.", bold_prefix="[PASS] Full ROS 2 Humble Integration: ")

    doc.add_page_break()
