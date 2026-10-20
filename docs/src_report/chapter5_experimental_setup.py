#!/usr/bin/env python3
"""
chapter5_experimental_setup.py

Chapter 5: Experimental Setup and Evaluation Methodology
Structured in exact alignment with sample-reference.docx:
  5.1 Benchmark mission scenarios and environmental partitions (Table 12)
  5.2 Model configurations and ablation baselines
  5.3 Training, validation, and real-time deployment protocol
  5.4 Evaluation metrics and statistical uncertainty methodology (Wilson CIs, z-tests)
  5.5 Hyperparameters and checkpoint selection criteria (Table 13)
  5.6 Threats to validity and engineering mitigations (Table 14)
"""

from report_styles import (
    add_chapter_header, add_section_header, add_subsection_header,
    add_paragraph, add_bullet, add_callout, add_table, add_equation, add_figure
)


def add_chapter_5(doc):
    add_chapter_header(doc, 5, "Experimental Setup and Evaluation Methodology")

    # -------------------------------------------------------------------------
    # 5.1 Benchmark Mission Scenarios & Partitions
    # -------------------------------------------------------------------------
    add_section_header(doc, "5.1", "Benchmark mission scenarios and environmental partitions")

    add_paragraph(
        doc,
        "To rigorously benchmark the proposed FA-MAPPO architecture against state-of-the-art baselines under realistic maritime conditions, "
        "we formulate four distinct benchmark operational scenarios. Each scenario introduces progressive physical, sensory, and communicative "
        "perturbations into the 6-USV fleet. Table 12 specifies the environmental disturbance parameters, fault-injection timing, and evaluation criteria."
    )

    table_12_headers = ["Scenario ID", "Environmental State", "Injected Fault Profile", "Fault Timing", "Primary Evaluation Metric"]
    table_12_data = [
        [
            "S1: Nominal Encirclement",
            "Sea State 2 (H_s = 0.5m, current 0.2 m/s)",
            "None (All sensors & thrusters 100% healthy)",
            "None",
            "Time to 20m orbit convergence (s), orbit circularity."
        ],
        [
            "S2: Isolated Actuator Fault",
            "Sea State 2 (H_s = 0.5m, current 0.3 m/s)",
            "USV-1 suffers 60% thruster power drop",
            "t = 15.0s",
            "Mean Time to Reconfigure (MTTR), slot handover latency."
        ],
        [
            "S3: Staggered Multi-Fault",
            "Sea State 3 (H_s = 1.25m, current 0.5 m/s)",
            "USV-1 thruster loss (t=12s), USV-2 GPS drift 8m (t=24s), USV-5 link drop 80% (t=36s)",
            "t = 12s, 24s, 36s",
            "Encirclement retention rate (%), zero collisions, cascade prevention."
        ],
        [
            "S4: Hostile Target Evasion",
            "Sea State 3 (H_s = 1.25m, current 0.5 m/s)",
            "Target executes maximum-rate S-curve evasive maneuvers (u_T = 1.8 m/s)",
            "Continuous",
            "Maximum angular perimeter gap Gamma, arrest zone entry."
        ]
    ]
    add_table(doc, 12, "Mission scenario partitions and fault-injection parameters", table_12_headers, table_12_data, [1.1, 1.4, 1.5, 0.7, 1.07])

    # -------------------------------------------------------------------------
    # 5.2 Model Configurations & Ablation Baselines
    # -------------------------------------------------------------------------
    add_section_header(doc, "5.2", "Model configurations and ablation baselines")

    add_paragraph(
        doc,
        "To isolate the specific performance impact of each algorithmic component, we evaluate six systematically constructed control architectures:"
    )

    add_bullet(
        doc,
        "A classical reactive controller combining attractive potential toward dynamic orbit slots with repulsive potential fields between vessels.",
        bold_prefix="Configuration C0 (Heuristic APF): "
    )
    add_bullet(
        doc,
        "Decentralized actor-critic where each USV trains an independent PPO policy without centralized critic observations or consensus.",
        bold_prefix="Configuration C1 (Independent PPO): "
    )
    add_bullet(
        doc,
        "Standard Multi-Agent PPO (Yu et al., 2021) utilizing centralized critic observations during training and decentralized execution, without fault awareness.",
        bold_prefix="Configuration C2 (Vanilla MAPPO): "
    )
    add_bullet(
        doc,
        "Vanilla MAPPO augmented with the 20 Hz analytical Control Barrier Function safety filter, but lacking health diagnosis or role reallocation.",
        bold_prefix="Configuration C3 (MAPPO + CBF): "
    )
    add_bullet(
        doc,
        "MAPPO augmented with the 14-residual XGBoost FDI engine, but utilizing greedy Hungarian assignment without the 4.0m hysteresis margin.",
        bold_prefix="Configuration C4 (MAPPO + FDI): "
    )
    add_bullet(
        doc,
        "The proposed dual-loop architecture integrating FA-MAPPO, 14-residual XGBoost FDI, Kuhn-Munkres 4.0m hysteresis matching, and 20 Hz CBF safety filtering.",
        bold_prefix="Configuration C5 (Proposed Full FA-MAPPO): "
    )

    # -------------------------------------------------------------------------
    # 5.3 Training, Validation, and Deployment Strategy
    # -------------------------------------------------------------------------
    add_section_header(doc, "5.3", "Training, validation, and real-time deployment protocol")

    add_paragraph(
        doc,
        "Model training was conducted in Stage 1 using vectorized PettingZoo environments across 10,000,000 environment interaction steps "
        "(10M steps) on an NVIDIA RTX 3060 GPU. Episodes lasted 1,000 steps (50 seconds simulated time). Checkpoints were evaluated every 100,000 steps "
        "across 50 randomized validation episodes. The highest-performing validation checkpoint was frozen and exported to PyTorch TorchScript format. "
        "During Stage 2 deployment, the frozen model was executed directly inside the asynchronous ROS 2 Humble node graph without online parameter updates."
    )

    add_figure(
        doc, 12,
        "XGBoost 4x4 FDI confusion matrix and precision-recall curves across marine failure modes.",
        "fig12_xgboost_confusion.png",
        width_in=5.8,
        explanation_text=(
            "Figure 12 displays the 4x4 confusion matrix and precision-recall curves for the 14-residual XGBoost classifier evaluated across "
            "10,000 independent test samples. The model achieves an overall macro F1-score of 97.4%, maintaining high diagnostic precision "
            "across thruster loss, GNSS drift, and packet loss modes."
        )
    )

    # -------------------------------------------------------------------------
    # 5.4 Evaluation Metrics & Statistical Uncertainty Methodology
    # -------------------------------------------------------------------------
    add_section_header(doc, "5.4", "Evaluation metrics and statistical uncertainty methodology")

    add_paragraph(
        doc,
        "The performance of each configuration was quantified across five primary engineering metrics:"
    )

    add_bullet(doc, "Time elapsed from mission start until all active tracking USVs enter the 20m +/- 1.0m standoff ring.", bold_prefix="1. Convergence Time (s): ")
    add_bullet(doc, "Percentage of post-convergence mission duration where angular spread Delta phi >= 4.712 rad.", bold_prefix="2. Encirclement Retention Rate (%): ")
    add_bullet(doc, "Total count of inter-vessel proximity violations below the minimum safety threshold (d_ij < 3.5m).", bold_prefix="3. Safety Violations (Incidents/Run): ")
    add_bullet(doc, "Time elapsed from fault injection until the degraded vessel is reallocated and an intact slot ring is restored.", bold_prefix="4. Mean Time to Reconfigure (MTTR, s): ")
    add_bullet(doc, "Percentage of dropped telemetry packets successfully recovered via dead reckoning or store-and-forward relay.", bold_prefix="5. Packet Robustness (%): ")

    # 5.4.1
    add_subsection_header(doc, "5.4.1", "Statistical uncertainty of an accuracy estimate: 95% Wilson confidence intervals")
    add_paragraph(
        doc,
        "Because empirical success rates p_hat = k / N are estimated from finite Monte Carlo trials (N = 100 runs per configuration), standard "
        "normal approximations fail near the boundaries (p_hat near 0 or 1). Following Wilson (1927) and Brown et al. (2001), we report asymmetric "
        "95% Wilson score confidence intervals [w^-, w^+]:"
    )

    add_equation(
        doc,
        "w^(±) = [ p̂ + z² / (2N) ± z · √( p̂(1 - p̂)/N + z²/(4N²) ) ] / (1 + z²/N),    where z = 1.95996",
        "21"
    )

    # 5.4.2
    add_subsection_header(doc, "5.4.2", "Unpaired two-sample hypothesis z-tests")
    add_paragraph(
        doc,
        "To establish whether performance improvements between configurations are statistically significant, we compute unpaired two-proportion "
        "z-tests between the proposed architecture (p_1, N_1 = 100) and each baseline (p_2, N_2 = 100):"
    )

    add_equation(
        doc,
        "z = (p̂₁ - p̂₂) / √[ p̂₀(1 - p̂₀)(1/N₁ + 1/N₂) ],    where p̂₀ = (k₁ + k₂) / (N₁ + N₂)",
        "22"
    )

    # -------------------------------------------------------------------------
    # 5.5 Hyperparameters & Checkpoint Selection
    # -------------------------------------------------------------------------
    add_section_header(doc, "5.5", "Hyperparameters and checkpoint selection criteria")

    add_paragraph(
        doc,
        "Table 13 lists the hyperparameter configurations employed during FA-MAPPO policy training and XGBoost model optimization, "
        "establishing complete experimental reproducibility."
    )

    table_13_headers = ["Algorithmic Component", "Hyperparameter Description", "Selected Value", "Exploration Range / Search Bound"]
    table_13_data = [
        ["FA-MAPPO Policy", "Actor network hidden layers", "[256, 256, 128]", "Tested [128, 128] to [512, 256]"],
        ["FA-MAPPO Policy", "Critic network hidden layers", "[512, 256, 128]", "Tested [256, 256] to [512, 512]"],
        ["FA-MAPPO Policy", "Learning rate (Adam optimizer)", "3.0 x 10^-4", "Swept 1.0 x 10^-4 to 1.0 x 10^-3"],
        ["FA-MAPPO Policy", "PPO clipping parameter epsilon", "0.20", "Fixed standard standard"],
        ["FA-MAPPO Policy", "GAE lambda parameter", "0.95", "Swept 0.90 to 0.99"],
        ["FA-MAPPO Policy", "Discount factor gamma", "0.99", "Fixed standard"],
        ["FA-MAPPO Policy", "Entropy regularization coefficient", "0.01", "Swept 0.001 to 0.05"],
        ["XGBoost FDI Engine", "Number of gradient boosted trees", "100", "Swept 50 to 300 estimators"],
        ["XGBoost FDI Engine", "Maximum tree depth", "4", "Swept depth 3 to 8 (depth 4 selected for minimal CPU overhead)"],
        ["XGBoost FDI Engine", "Learning rate (eta)", "0.10", "Swept 0.01 to 0.30"],
        ["Role Allocator", "Hysteresis deadband Delta H", "4.0 m", "Swept 1.0m to 8.0m (4.0m chatter-free)"],
        ["CBF Safety Filter", "Decay rate parameter gamma", "1.5 s^-1", "Swept 0.5 to 3.0 s^-1"]
    ]
    add_table(doc, 13, "Training and hyperparameter configuration for the FA-MAPPO policy and FDI classifier", table_13_headers, table_13_data, [1.3, 1.8, 1.1, 1.57])

    # -------------------------------------------------------------------------
    # 5.6 Threats to Validity & Mitigations
    # -------------------------------------------------------------------------
    add_section_header(doc, "5.6", "Threats to validity and engineering mitigations")

    add_paragraph(
        doc,
        "Table 14 summarizes the potential threats to empirical and operational validity identified during the study, along with the "
        "corresponding engineering mitigations implemented to ensure rigorous conclusions."
    )

    table_14_headers = ["Threat Category", "Nature of Validity Threat", "Engineering Risk", "Implemented Mitigation Strategy"]
    table_14_data = [
        [
            "Sim-to-Real Gap",
            "Discrepancy between idealized simulation and real hydrodynamics.",
            "Learned policy fails under unmodeled water drag and wave drift.",
            "Integrated full Fossen 3-DOF added mass and Pierson-Moskowitz wave spectrum with domain randomization."
        ],
        [
            "Network Latency",
            "Zero-latency simulation vs asynchronous DDS packet jitter.",
            "Policy commands arrive stale, causing delayed reaction to faults.",
            "Simulated 10ms-50ms stochastic latency and burst packet drops directly inside `usv_perception_node`."
        ],
        [
            "Statistical Noise",
            "Small trial counts producing misleading success claims.",
            "High variance in multi-agent trajectory outcomes.",
            "Conducted 100 randomized Monte Carlo seeds per configuration; reported 95% Wilson score confidence bounds."
        ],
        [
            "Algorithmic Chattering",
            "Noise-induced role swapping near Voronoi slot boundaries.",
            "Agents rapidly oscillate roles, burning thruster power and clumping.",
            "Introduced 4.0m hysteresis deadband barrier into the Kuhn-Munkres cost matrix with formal stability proof."
        ]
    ]
    add_table(doc, 14, "Threats to experimental validity and engineering mitigations", table_14_headers, table_14_data, [1.1, 1.4, 1.4, 1.87])

    doc.add_page_break()
