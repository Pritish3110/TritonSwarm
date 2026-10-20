#!/usr/bin/env python3
"""
chapter1_intro.py

Chapter 1: Introduction
Structured in exact alignment with sample-reference.docx:
  1.1 Background and motivation
  1.2 Problem statement
  1.3 Research gap
  1.4 Aim and objectives (includes Table 3: Project Objectives Mapped to Deliverables and Chapters)
  1.5 Scope and assumptions
  1.6 Contributions
  1.7 Organization of the report
"""

from report_styles import (
    add_chapter_header, add_section_header, add_subsection_header,
    add_paragraph, add_bullet, add_callout, add_table
)


def add_chapter_1(doc):
    add_chapter_header(doc, 1, "Introduction")

    # -------------------------------------------------------------------------
    # 1.1 Background and Motivation
    # -------------------------------------------------------------------------
    add_section_header(doc, "1.1", "Background and motivation")
    
    add_paragraph(
        doc,
        "Maritime domain awareness, coastal littoral surveillance, high-seas interdiction, and maritime search-and-rescue (SAR) "
        "operations increasingly rely on autonomous robotic platforms. Traditional naval doctrine has historically deployed large, "
        "high-tonnage crewed vessels to patrol extensive exclusive economic zones (EEZs) and secure strategic maritime choke points. However, "
        "such centralized, monolithic assets face severe limitations: exorbitant capital and fuel expenditures, vulnerability to single-point "
        "catastrophic failure, substantial crew safety risks during hostile asymmetric engagements, and insurmountable geometric constraints "
        "when attempting to track and cordon off agile, non-cooperative surface vessels across thousands of square kilometers of open water."
    )
    
    add_paragraph(
        doc,
        "To overcome these operational limitations, cooperative swarms of Unmanned Surface Vehicles (USVs) have emerged as a disruptive "
        "autonomous paradigm. A multi-USV swarm distributes sensing, computational intelligence, and actuation across a fleet of agile, "
        "expendable vessels operating under distributed coordination. When tasked with tracking and cordoning off a dynamic target vessel, "
        "the swarm coordinates to form a bounded encirclement orbit—establishing a non-destructive standoff perimeter that restricts the target's "
        "escape trajectories while preserving physical standoff clearance to prevent collisions. Multi-Agent Reinforcement Learning (MARL) "
        "offers an ideal computational foundation for synthesizing such cooperative behaviors directly from high-dimensional state spaces."
    )

    add_paragraph(
        doc,
        "However, real-world deployment of USV swarms in dynamic marine environments is fraught with severe physical, sensory, and "
        "communication disturbances. First, marine hydrodynamics are governed by highly nonlinear hydrodynamic added mass, wave-frequency "
        "wave-drift forces, and velocity-squared drag forces. Second, marine surface vessels are acutely prone to physical equipment degradation, "
        "including propeller fouling, rudder motor burnouts, partial thruster power collapse, and GPS multipath drift or spoofing. Third, the "
        "marine RF environment suffers from severe multipath wave reflections and dynamic Fresnel zone wave-crest obstructions, causing burst packet "
        "loss and link drops. Existing MARL controllers are trained in idealized simulators under the assumption of uncorrupted states and nominal "
        "actuators; when deployed in realistic, fault-perturbed environments, they suffer catastrophic behavioral collapse, manifesting as inter-agent "
        "clumping, formation dissolution, and near-collisions."
    )

    # -------------------------------------------------------------------------
    # 1.2 Problem Statement
    # -------------------------------------------------------------------------
    add_section_header(doc, "1.2", "Problem statement")

    add_paragraph(
        doc,
        "The fundamental operational objective of this capstone research is to synthesize, formally verify, and deploy a decentralized "
        "cooperative control architecture for a swarm of N = 6 autonomous USVs tasked with capturing and non-destructively encircling an agile, "
        "non-cooperative surface vessel within a 20.0-meter standoff orbit in a disturbed marine environment (Sea States 2 to 3), while guaranteeing "
        "strict fault resilience under concurrent physical failures and communication blackouts."
    )

    add_paragraph(
        doc,
        "Mathematically, the system must achieve three coupled criteria: (i) Standoff Convergence: drive the target-relative distance of all "
        "active tracking agents to within the target orbital tolerance |r_k - R_orbit| ≤ 1.0 m where R_orbit = 20.0 m; (ii) Angular Encirclement "
        "and Perimeter Containment: maintain an aggregate angular perimeter coverage ΔΦ ≥ 3π/2 (4.712 rad) with a maximum contiguous angular gap "
        "Γ ≤ π/2 (1.571 rad); and (iii) Forward Invariant Safety: guarantee zero inter-vessel collisions by enforcing a pairwise Euclidean "
        "safety clearance d_ij(t) ≥ d_safe = 3.5 m for all pairs of vessels across the entire mission timeline, including during abrupt thruster failure, "
        "GPS spoofing drift, and RF communication blackout."
    )

    # -------------------------------------------------------------------------
    # 1.3 Research Gap
    # -------------------------------------------------------------------------
    add_section_header(doc, "1.3", "Research gap")

    add_paragraph(
        doc,
        "While cooperative multi-agent encirclement and autonomous path-following have received substantial research attention, existing "
        "methodologies exhibit three critical research gaps when deployed in physical marine environments:"
    )

    add_bullet(
        doc,
        "Existing MARL architectures (e.g., MADDPG, MAPPO, QMIX) treat agent health as an unobservable, static constant. When an agent suffers "
        "a 50% thruster power drop or compass drift, the frozen neural policy attempts to execute nominal control actions, causing kinematic tracking "
        "errors that destabilize neighboring agents and trigger swarm-wide clumping.",
        bold_prefix="Gap 1: Absence of Integrated Fault Awareness in Policy Execution. "
    )
    add_bullet(
        doc,
        "Traditional role reallocation schemes rely on greedy distance metrics or static finite-state machines. In dynamic marine environments, "
        "sensor noise causes rapid oscillation of cost metrics around the decision boundary, leading to high-frequency role chattering and "
        "unjustified cascading demotions into out-of-distribution roles never seen during MARL training.",
        bold_prefix="Gap 2: Role Chattering and Cascading Demotion Pathology. "
    )
    add_bullet(
        doc,
        "Reinforcement learning reward shaping cannot provide hard mathematical safety guarantees. Penalty-based collision avoidance in RL "
        "frequently permits transient safety boundary violations during unexpected perturbations, creating severe risks of physical vessel hull collision.",
        bold_prefix="Gap 3: Lack of Hard Safety Verification During Dynamic Reconfiguration. "
    )

    # -------------------------------------------------------------------------
    # 1.4 Aim and Objectives
    # -------------------------------------------------------------------------
    add_section_header(doc, "1.4", "Aim and objectives")

    add_paragraph(
        doc,
        "The overarching aim of this research is to design, implement, and validate a resilient, dual-loop Fault-Aware Multi-Agent "
        "Reinforcement Learning (FA-MAPPO) architecture that guarantees mission continuity and forward-invariant collision avoidance "
        "under multi-fault conditions. Table 3 explicitly maps each primary project objective to its engineering deliverable, produced evidence, "
        "and corresponding chapter in this report."
    )

    obj_headers = ["ID", "Objective Specification", "Deliverable / Evidence Produced", "Report Chapter"]
    obj_data = [
        [
            "O1",
            "Synthesize a high-fidelity 3-DOF Fossen marine hydrodynamics simulation with added mass, quadratic drag, and RK4 numerical integration.",
            "Hydrodynamic equations, coordinate frames, RK4 solver benchmarks, Heron USV physical parameter register.",
            "Chapter 3, 4"
        ],
        [
            "O2",
            "Design an onboard 14-residual kinematic feature extraction engine and train an XGBoost multi-class FDI classifier operating at 5 Hz.",
            "Residual formulation equations, confusion matrix (97.4% precision), ROC curves, feature importance ranking.",
            "Chapter 3, 4, 6"
        ],
        [
            "O3",
            "Formulate a Kuhn-Munkres Hungarian bipartite role and orbit slot allocator incorporating a 4.0m hysteresis margin barrier.",
            "Bipartite matching algorithm, hysteresis deadband proofs, chattering-free role transition logs.",
            "Chapter 3, 4, 6"
        ],
        [
            "O4",
            "Implement a 20 Hz analytical Control Barrier Function (CBF) quadratic programming safety filter guaranteeing d_safe ≥ 3.5m.",
            "Class-K ZCBF formulations, forward invariance proofs, zero-collision telemetry verification.",
            "Chapter 3, 6, App. D"
        ],
        [
            "O5",
            "Develop a four-layer wireless degradation mitigation architecture for disrupted marine mesh networks (TDMA, delta-state, DR, relay).",
            "RF two-ray propagation model, Fresnel obstruction analysis, store-and-forward message buffer specs.",
            "Chapter 3, 4"
        ],
        [
            "O6",
            "Deploy the entire system as a distributed 9-package ROS 2 Humble node graph communicating via OMG-compliant DDS topics.",
            "Package architecture, custom usv_msgs interfaces, launch files, RViz2 anti-flicker fullscreen configurations.",
            "Chapter 4"
        ],
        [
            "O7",
            "Execute a comprehensive 3x3 benchmark sweep across nominal, single-fault, and multi-fault scenarios with 95% Wilson confidence intervals.",
            "Quantitative master results matrix, Wilson confidence bounds, unpaired z-tests, paired ablation differences.",
            "Chapter 5, 6"
        ],
        [
            "O8",
            "Perform computational latency profiling on the development host and establish feasibility bounds against the recommended NVIDIA Jetson Orin Nano edge deployment specifications.",
            "Host execution latency profiles, per-node timing distributions (log scale), edge timing budget evaluation.",
            "Chapter 6"
        ]
    ]
    add_table(doc, 3, "Project Objectives Mapped to Deliverables, Evidence, and Report Chapters", obj_headers, obj_data, [0.6, 2.5, 2.1, 0.57], col_alignments=["CENTER", "LEFT", "LEFT", "LEFT"])

    # -------------------------------------------------------------------------
    # 1.5 Scope and Assumptions
    # -------------------------------------------------------------------------
    add_section_header(doc, "1.5", "Scope and assumptions")

    add_paragraph(
        doc,
        "To establish rigorous engineering validity, the scope and fundamental assumptions of this research are delineated as follows:"
    )

    add_bullet(
        doc,
        "Physical vessel construction, on-water sea trials, and open-ocean maritime deployments are strictly out of scope for this capstone research. "
        "The complete multi-agent system is synthesized, verified, and benchmarked within high-fidelity 3-DOF Fossen marine hydrodynamics simulation "
        "and distributed ROS 2 Humble node graphs on our development workstation, with the NVIDIA Jetson Orin Nano compute architecture recommended "
        "as the prospective edge deployment target for future sea trials.",
        bold_prefix="Scope Boundary and Physical Deployment: "
    )
    add_bullet(
        doc,
        "The USV fleet consists of N = 6 homogeneous twin-thruster surface craft whose physical dimensions and mass properties mirror the "
        "Clearpath Robotics Heron USV platform (length L = 1.35 m, beam B = 0.98 m, mass m = 28.0 kg, maximum speed u_max = 2.0 m/s).",
        bold_prefix="Swarm Fleet Composition: "
    )
    add_bullet(
        doc,
        "Vessel motion is strictly constrained to the 3-DOF horizontal sea surface plane: surge (longitudinal), sway (lateral), and yaw (rotation). "
        "Heave, roll, and pitch dynamics are neglected as secondary for low-speed littoral catamaran craft.",
        bold_prefix="Planar Hydrodynamic Domain: "
    )
    add_bullet(
        doc,
        "Environmental disturbances emulate Sea States 2 to 3, characterized by significant wave heights H_s in [0.5 m, 1.25 m] and stochastic "
        "irrotational surface current velocities up to 0.5 m/s.",
        bold_prefix="Marine Environmental Disturbances: "
    )
    add_bullet(
        doc,
        "Fault injections are modeled via a dedicated injector node covering thruster power reductions (20% to 100% loss), GPS position drift up to "
        "10.0 m, and RF packet drop bursts up to 80% loss.",
        bold_prefix="Failure Modes: "
    )

    # -------------------------------------------------------------------------
    # 1.6 Contributions
    # -------------------------------------------------------------------------
    add_section_header(doc, "1.6", "Contributions")

    add_paragraph(
        doc,
        "The primary technical contributions of this research are summarized as follows:"
    )

    add_bullet(
        doc,
        "A novel dual-loop architecture that bridges high-level MARL cooperative planning with high-rate (20 Hz) formal Control Barrier Function "
        "safety filtering, guaranteeing zero inter-vessel hull collisions even during catastrophic individual agent thruster loss.",
        bold_prefix="1. Dual-Loop Safe Multi-Agent Architecture: "
    )
    add_bullet(
        doc,
        "An onboard kinematic residual extraction pipeline that maps physical motion errors into a 14-dimensional feature vector, classified by "
        "a lightweight gradient-boosted tree (XGBoost) within 4.8 ms, achieving 97.4% precision without requiring external diagnostic ground-truth.",
        bold_prefix="2. 14-Residual Real-Time Marine FDI: "
    )
    add_bullet(
        doc,
        "A mathematically proven hysteresis deadband margin (ΔH = 4.0 m) embedded within the Kuhn-Munkres Hungarian role allocation algorithm, "
        "eliminating role chattering and preventing out-of-distribution role allocations that otherwise cause swarm clumping.",
        bold_prefix="3. Chatter-Free Resilient Role Reallocation: "
    )
    add_bullet(
        doc,
        "A distributed, asynchronous ROS 2 Humble node graph evaluated on the development workstation with computational feasibility established for "
        "recommended NVIDIA Jetson Orin Nano edge deployment, demonstrating empirical mission success (95.8%) and MTTR (1.70s) backed by formal 95% Wilson score confidence intervals.",
        bold_prefix="4. Open-Source Embedded Implementation & Empirical Benchmarks: "
    )

    # -------------------------------------------------------------------------
    # 1.7 Organization of the Report
    # -------------------------------------------------------------------------
    add_section_header(doc, "1.7", "Organization of the report")

    add_paragraph(
        doc,
        "The remainder of this report is organized into seven comprehensive chapters and four formal appendices:"
    )

    add_bullet(
        doc,
        "Chapter 2 provides a systematic literature review covering marine hydrodynamics, MARL target encirclement, fault-tolerant control, "
        "and marine wireless contention, presenting the literature timeline, role failure taxonomy, and comparative matrix.",
        bold_prefix="Chapter 2 (Literature Review & Background): "
    )
    add_bullet(
        doc,
        "Chapter 3 establishes the formal mathematical formulations of the 3-DOF Fossen hydrodynamic model, target encirclement orbit geometry, "
        "14-residual FDI engine, Kuhn-Munkres hysteresis matching, CBF quadratic program safety filter, and Algorithms 1 and 2.",
        bold_prefix="Chapter 3 (Problem Formulation & Proposed Architecture): "
    )
    add_bullet(
        doc,
        "Chapter 4 presents the complete system architecture, dual-loop design, four-layer wireless mitigation pipeline, ROS 2 node graph, "
        "provenance mapping, and physical implementation checklist.",
        bold_prefix="Chapter 4 (System Design & Implementation): "
    )
    add_bullet(
        doc,
        "Chapter 5 details the experimental methodology, benchmark scenario partitions, baseline configurations (C0–C5), statistical uncertainty "
        "formulations (Wilson intervals and z-tests), and threats to validity.",
        bold_prefix="Chapter 5 (Experimental Setup & Evaluation Methodology): "
    )
    add_bullet(
        doc,
        "Chapter 6 presents the quantitative results, validation vs real-time deployment gap, Pareto rankings, Wilson confidence bounds, paired "
        "ablations, embedded hardware profiling, and root-cause analysis.",
        bold_prefix="Chapter 6 (Results, Analysis & Discussion): "
    )
    add_bullet(
        doc,
        "Chapter 7 concludes the thesis with a summary of findings, formal verification of objectives against deliverables, operational limitations, "
        "and prioritized future engineering roadmap.",
        bold_prefix="Chapter 7 (Conclusion & Future Work): "
    )
    add_bullet(
        doc,
        "Appendices A through D provide the Source and Missing-Information Register, Numerical Data Underlying Figures, Additional Pseudocode "
        "(Algorithms 3, 4, 5), and Formal Mathematical Proofs of CBF forward invariance and hysteresis stability.",
        bold_prefix="Appendices A–D: "
    )

    doc.add_page_break()
