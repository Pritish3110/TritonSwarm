#!/usr/bin/env python3
"""
chapter2_lit_review.py

Chapter 2: Literature Review and Background
Structured in exact alignment with sample-reference.docx:
  2.1 Thematic sub-reviews (2.1.1 - 2.1.5)
  2.2 Literature timeline and method convergence (Figure 1 + explanatory text)
  2.3 Complementary swarm roles and characteristic failure modes (Table 4)
  2.4 Taxonomy of maritime swarm fault modes and recovery levels (Figure 2 + explanatory text)
  2.5 Comparative summary and identified gap (Table 5 in landscape section)
"""

from report_styles import (
    add_chapter_header, add_section_header, add_subsection_header,
    add_paragraph, add_bullet, add_callout, add_table, add_figure,
    add_landscape_section, add_portrait_section
)


def add_chapter_2(doc):
    add_chapter_header(doc, 2, "Literature Review and Background")

    # -------------------------------------------------------------------------
    # 2.1 Thematic Sub-Reviews
    # -------------------------------------------------------------------------
    add_section_header(doc, "2.1", "Thematic sub-reviews")

    # 2.1.1
    add_subsection_header(doc, "2.1.1", "Maritime USV hydrodynamics and control")
    add_paragraph(
        doc,
        "The mathematical foundation of marine surface vehicle simulation and control was formalized by Fossen (1994, 2011), "
        "who formulated the canonical 3-DOF horizontal equations of motion for surface vessels. Unlike terrestrial mobile robots or "
        "aerial quadrotors, marine craft operate at the dynamic air-water interface, where hydrodynamic added mass (the inertia of fluid "
        "entrained by the accelerating hull) contributes up to 40% of the effective rigid-body mass. Furthermore, hydrodynamic damping "
        "is strictly nonlinear, comprising linear skin friction at low velocities and quadratic form drag at operational speeds. Breivik and "
        "Fossen (2002, 2005) extended these models to line-of-sight (LOS) guidance and nonlinear path-following for underactuated marine craft. "
        "However, classical guidance laws rely on accurate, uncorrupted hydrodynamic state feedback; when thrusters degrade or GPS positions drift, "
        "deterministic guidance fails to maintain encirclement geometries."
    )

    # 2.1.2
    add_subsection_header(doc, "2.1.2", "Multi-agent reinforcement learning for target encirclement")
    add_paragraph(
        doc,
        "Cooperative target encirclement and caging has evolved rapidly from classical potential field methods (Khatib, 1986; Warren, 1990) "
        "to Multi-Agent Reinforcement Learning (MARL). Lowe et al. (2017) established the centralized training with decentralized execution "
        "(CTDE) paradigm via Multi-Agent Deep Deterministic Policy Gradient (MADDPG). In 2021, Yu et al. demonstrated that Multi-Agent PPO "
        "(MAPPO) matches or exceeds off-policy algorithms in cooperative multi-agent benchmarks when equipped with centralized value functions "
        "and normalized generalized advantage estimation (GAE). While MAPPO has been applied to maritime escort missions (Chen et al., 2023), "
        "standard policy networks assume time-invariant agent capabilities. When an agent experiences propulsion loss, its policy outputs commands "
        "it can no longer physically execute, destabilizing consensus across the entire formation."
    )

    # 2.1.3
    add_subsection_header(doc, "2.1.3", "Fault detection, isolation, and resilient reconfiguration")
    add_paragraph(
        doc,
        "Fault Detection and Isolation (FDI) for marine vessels has traditionally relied on model-based residual observers (Blanke et al., 2006). "
        "However, marine environmental disturbances (wave action, variable currents) generate substantial residual noise, causing high false-alarm "
        "rates in classical threshold detectors. Recent advances leverage machine learning for data-driven fault diagnosis; Chen & Zhang (2024) "
        "demonstrated that gradient-boosted decision trees (XGBoost) achieve superior classification speed and robustness compared to deep neural "
        "networks on embedded microcontrollers. Nevertheless, existing works treat FDI as an isolated supervisory diagnostic layer rather than "
        "dynamically coupling health states into real-time role reallocation and policy execution."
    )

    # 2.1.4
    add_subsection_header(doc, "2.1.4", "Safety filters and control barrier functions")
    add_paragraph(
        doc,
        "Ensuring collision-free navigation in multi-agent swarms requires formal mathematical safety guarantees. Ames et al. (2014, 2017, 2019) "
        "formalized Control Barrier Functions (CBFs), proving that the forward invariance of a safe set can be enforced by solving an online convex "
        "Quadratic Program (QP) that projects unconstrained nominal control actions onto an affine halfspace. In multi-robot systems, CBF-based safety "
        "filters (Wang et al., 2017) guarantee minimum pairwise inter-agent clearance. In this research, we embed a 20 Hz analytical CBF filter "
        "beneath the high-level FA-MAPPO policy, guaranteeing physical clearance even when an agent loses control authority."
    )

    # 2.1.5
    add_subsection_header(doc, "2.1.5", "Maritime RF propagation and co-channel contention")
    add_paragraph(
        doc,
        "Marine surface wireless communications are subject to severe physical impairments governed by two-ray ground reflection and dynamic sea state "
        "geometry (Rappaport, 2002; Guan et al., 2021). Because USV antennas are mounted low (typically 1.0 m to 2.0 m above water level), wave crests "
        "in Sea State 3 (H_s = 1.25 m) directly occlude the first Fresnel zone ellipsoid, causing deep signal fading and burst packet loss. Furthermore, "
        "when multiple USVs broadcast uncoordinated 50 Hz telemetry over shared 2.4 GHz ISM channels, co-channel interference and the hidden terminal "
        "problem induce catastrophic packet drop rates exceeding 60%, breaking multi-agent consensus."
    )

    # -------------------------------------------------------------------------
    # 2.2 Literature Timeline and Method Convergence
    # -------------------------------------------------------------------------
    add_section_header(doc, "2.2", "Literature timeline and method convergence")

    add_figure(
        doc, 1,
        "Timeline of the datasets, models and methods discussed in this chapter, grouped by role.",
        "fig01_timeline.png",
        width_in=6.0,
        explanation_text=(
            "Figure 1 places the cited works in chronological order, spanning from foundational marine hydrodynamics (Fossen, 1994) "
            "to modern safe MARL and machine learning fault diagnosis. The methodological evolution reveals three convergent historical threads: "
            "(i) rigorous hydrodynamic modeling and nonlinear planar control; (ii) cooperative multi-agent policy learning (MADDPG, MAPPO); and "
            "(iii) formal safety filtering via Control Barrier Functions. The proposed FA-MAPPO architecture synthesizes these three disparate lines "
            "into a unified, closed-loop fault-aware framework operating in distributed ROS 2."
        )
    )

    # -------------------------------------------------------------------------
    # 2.3 Swarm Roles and Characteristic Failure Modes
    # -------------------------------------------------------------------------
    add_section_header(doc, "2.3", "Complementary swarm roles and characteristic failure modes")

    add_paragraph(
        doc,
        "To achieve resilient target containment under physical degradation, the swarm architecture defines four specialized, "
        "mutually complementary operational roles: Tracker, Relay, Scout, and Interceptor. Table 4 summarizes the operational responsibilities, "
        "complementary strengths, and characteristic failure modes of each role. When an agent experiences degradation, its operational capabilities "
        "are systematically matched to the role whose functional constraints it can still fulfill."
    )

    role_headers = ["Swarm Role", "Primary Mission Mandate", "Complementary Capabilities", "Characteristic Failure Modes"]
    role_data = [
        [
            "Tracker / Ring Interceptor",
            "Maintain tight 20m standoff orbit slot around target vessel.",
            "High control authority, dual-thruster agility, precise target tracking.",
            "Thruster power loss prevents keeping pace; GPS drift causes orbit distortion."
        ],
        [
            "Store-and-Forward Relay",
            "Bridge wireless mesh partitions around wave crest obstructions.",
            "Requires minimal kinetic maneuvering; high RF buffer capacity.",
            "Packet buffer overflow; isolated link drops during extreme sea states."
        ],
        [
            "Wide-Area Scout",
            "Patrol outer perimeter and detect secondary surface obstacles.",
            "High-speed transit; operates outside dense encirclement ring.",
            "Compass bias causes angular drift; sensor occlusion during wave pitch."
        ],
        [
            "Degraded Escort",
            "Maintain loose standoff clearance without active orbit slot duty.",
            "Conserves degraded thruster output; serves as ready standby asset.",
            "Kinematic lag; must rely on neighboring relays for telemetry routing."
        ]
    ]
    add_table(doc, 4, "Complementary strengths and characteristic failure modes of the four swarm roles", role_headers, role_data, [1.3, 1.6, 1.4, 1.47])

    # -------------------------------------------------------------------------
    # 2.4 Taxonomy of Swarm Fault Modes and Recovery Levels
    # -------------------------------------------------------------------------
    add_section_header(doc, "2.4", "Taxonomy of maritime swarm fault modes and recovery levels")

    add_figure(
        doc, 2,
        "Taxonomy of maritime swarm fault modes and the four-tiered autonomous recovery hierarchy.",
        "fig02_taxonomy.png",
        width_in=6.0,
        explanation_text=(
            "Figure 2 illustrates the structured four-tier recovery hierarchy designed to handle progressive physical and communicative "
            "impairments. Level 0 represents nominal operation where all vessels execute cooperative encirclement under full trust. "
            "Level 1 activates dead-reckoning state estimation when GPS covariance expands. Level 2 triggers Kuhn-Munkres role reallocation "
            "when thruster power drops, demoting degraded agents to escort or relay roles. Level 3 engages store-and-forward mesh routing "
            "when wave crests occlude line-of-sight RF propagation."
        )
    )

    # -------------------------------------------------------------------------
    # 2.5 Comparative Summary and Identified Gap (Landscape Section)
    # -------------------------------------------------------------------------
    add_section_header(doc, "2.5", "Comparative summary and identified gap")

    add_paragraph(
        doc,
        "Table 5 provides a detailed comparative synthesis of fifteen foundational works across the maritime robotics, MARL, and fault-tolerant "
        "control domains. To accommodate the extensive multidimensional comparison matrix, Table 5 is presented in full-width landscape orientation."
    )

    # Transition to Landscape Section for Table 5
    sec_land = add_landscape_section(doc)

    lit_headers = ["Citation & Author", "Primary Methodology", "Hydrodynamic Fidelity", "FDI Engine", "Safety Filter", "Multi-Fault Resilience", "Identified Critical Limitation"]
    lit_data = [
        ["Fossen (1994, 2011)", "3-DOF Nonlinear Ship Kinematics", "Rigid-body + added mass + drag", "None (Open-loop)", "None", "None", "Pure mathematical plant; no multi-agent consensus."],
        ["Breivik & Fossen (2005)", "Line-of-Sight (LOS) Guidance", "Planar kinematics + damping", "None", "None", "None", "Single-vessel path following; cannot encircle reactive targets."],
        ["Khatib (1986); Warren (1990)", "Artificial Potential Fields (APF)", "Point-mass planar kinematics", "None", "Repulsive fields", "Poor (Local minima)", "Suffers from local minima and severe oscillation under noise."],
        ["Lowe et al. (2017) MADDPG", "Multi-Agent Off-Policy DDPG", "Point-mass kinematics (Gym)", "None", "Reward penalty", "None (Policy collapse)", "Reward penalty fails to prevent collisions under actuator loss."],
        ["Yu et al. (2021) MAPPO", "Centralized Critic PPO (CTDE)", "Simplified particle kinematics", "None", "Reward penalty", "None", "Static health assumption; frozen policy clumps under fault."],
        ["Chen et al. (2023)", "Safe Multi-USV Escort MARL", "3-DOF Fossen Hydrodynamics", "Threshold check", "APF Barrier", "Single thruster drop", "Lacks real-time ML FDI; role reallocations suffer chattering."],
        ["Ames et al. (2017)", "Control Barrier Functions (CBF)", "Control-affine nonlinear plants", "None", "QP Min-Deviation", "Single-agent safety", "Formulated for single robots; unaddressed MARL policy coupling."],
        ["Wang et al. (2017)", "Decentralized Multi-Robot CBFs", "Single-integrator mobile robots", "None", "Decentralized QP", "None", "Idealized point robots; ignores maritime hydrodynamic inertia."],
        ["Blanke et al. (2006)", "Diagnosis & Fault-Tolerant Control", "Model-based residual observers", "Kalman Observer", "Supervisory logic", "Actuator freeze only", "Residual observers exhibit false alarms in wave-drift states."],
        ["Chen & Zhang (2024)", "Marine FDI using Gradient Trees", "Static engine telemetry datasets", "XGBoost Classifier", "None", "Diagnostic only", "Offline diagnostic study; disconnected from real-time swarm control."],
        ["Rappaport (2002); Guan (2021)", "Two-Ray RF Ground Reflection", "Electromagnetic wave propagation", "None", "None", "Link loss modeling", "Channel model only; does not propose swarm control mitigations."],
        ["Kuhn (1955); Munkres (1957)", "Hungarian Bipartite Assignment", "Combinatorial optimization", "None", "None", "Static cost matrix", "Lacks hysteresis; causes severe role chattering near boundaries."],
        ["Yan et al. (2022)", "Cooperative Marine Caging", "Unicycle kinematic model", "None", "Potential fields", "None", "Neglects hydrodynamic form drag and wireless packet contention."],
        ["He et al. (2024)", "Distributed Fault-Tolerant USVs", "Linearized vessel dynamics", "Observer residuals", "Sliding mode", "Actuator bias", "Requires continuous high-bandwidth inter-agent communications."],
        ["Proposed Work (FA-MAPPO)", "Dual-Loop FA-MAPPO + 20Hz CBF", "Full Fossen 3-DOF RK4 + added mass", "14-Residual XGBoost (5Hz)", "Analytical CBF-QP", "Full Tripartite Resilience", "Closed-loop, chatter-free, zero-collision verified on ROS 2."]
    ]
    add_table(doc, 5, "Comprehensive comparison of reviewed works with control strategy, fidelity, and limitations", lit_headers, lit_data, [1.4, 1.4, 1.3, 1.2, 1.1, 1.3, 1.99], is_landscape=True)

    add_paragraph(
        doc,
        "Table 5 highlights the central academic motivation of this capstone research: while individual components (Fossen hydrodynamics, "
        "MAPPO coordination, CBF safety filters, XGBoost diagnosis, and RF channel modeling) exist in isolation, no prior work has synthesized "
        "them into an integrated, dual-loop fault-tolerant architecture capable of surviving multi-fault cascades in asynchronous marine deployments."
    )

    # Return to Portrait Section
    sec_port = add_portrait_section(doc)
