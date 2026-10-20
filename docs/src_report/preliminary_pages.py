#!/usr/bin/env python3
"""
preliminary_pages.py

Generates university-compliant preliminary pages matching sample-reference.docx:
1. Cover Page (Official layout, Title, Team Roster, School, Date, VIT University Seal)
2. Title Page (Formal degree submission)
3. Declaration by the Candidate
4. Certificate of Bonafide Work (Guide Dr. Linda Joseph, HoD, SCOPE seal)
5. Abstract (350-word structured summary, IEEE keywords)
6. Acknowledgement (Chancellor, Leadership, Deans, HoD, Guide, Peers, Family)
7. Table of Contents (Full 7-Chapter + 4-Appendix hierarchy)
8. List of Figures (Figures 1 through 23 with captions and page numbers)
9. List of Tables (Tables 1 through 30 with captions and page numbers)
10. List of Symbols, Abbreviations and Nomenclature:
    - Table 1: Nomenclature of mathematical symbols
    - Table 2: List of abbreviations
"""

import os
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

from report_styles import (
    FONT_MAIN, COLOR_NAVY, COLOR_SLATE, COLOR_BODY, COLOR_MUTED,
    HEX_NAVY, HEX_LIGHT_ROW, HEX_ALT_ROW, set_cell_shading, set_cell_margins, set_table_borders,
    add_paragraph, add_table, ASSETS_DIR
)


def add_preliminary_pages(doc):
    # =========================================================================
    # 1. COVER PAGE
    # =========================================================================
    p0 = doc.add_paragraph()
    p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p0.paragraph_format.space_before = Pt(12)
    p0.paragraph_format.space_after = Pt(4)
    r0 = p0.add_run("A project report on")
    r0.font.name = FONT_MAIN
    r0.font.size = Pt(12)
    r0.font.color.rgb = COLOR_BODY

    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_before = Pt(14)
    p_title.paragraph_format.space_after = Pt(18)
    r_title = p_title.add_run("A NOVEL FAULT-AWARE MULTI-AGENT REINFORCEMENT LEARNING FRAMEWORK FOR RESILIENT USV SWARM ENCIRCLEMENT")
    r_title.font.name = FONT_MAIN
    r_title.font.size = Pt(18)
    r_title.font.bold = True
    r_title.font.color.rgb = COLOR_NAVY

    p_sub = doc.add_paragraph()
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sub.paragraph_format.space_before = Pt(6)
    p_sub.paragraph_format.space_after = Pt(6)
    r_sub = p_sub.add_run("Submitted in partial fulfillment for the award of the degree of")
    r_sub.font.name = FONT_MAIN
    r_sub.font.size = Pt(13)
    r_sub.font.color.rgb = COLOR_BODY

    p_deg = doc.add_paragraph()
    p_deg.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_deg.paragraph_format.space_before = Pt(6)
    p_deg.paragraph_format.space_after = Pt(18)
    r_deg = p_deg.add_run("Bachelor of Technology in\nComputer Science and Engineering")
    r_deg.font.name = FONT_MAIN
    r_deg.font.size = Pt(16)
    r_deg.font.bold = True
    r_deg.font.color.rgb = COLOR_NAVY

    p_by = doc.add_paragraph()
    p_by.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_by.paragraph_format.space_before = Pt(4)
    p_by.paragraph_format.space_after = Pt(4)
    r_by = p_by.add_run("by")
    r_by.font.name = FONT_MAIN
    r_by.font.size = Pt(13)
    r_by.font.color.rgb = COLOR_BODY

    p_team = doc.add_paragraph()
    p_team.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_team.paragraph_format.space_before = Pt(6)
    p_team.paragraph_format.space_after = Pt(16)
    
    r_m = p_team.add_run("PRITISH BHATASANA (23BRS1035)\n")
    r_m.font.name = FONT_MAIN
    r_m.font.size = Pt(14)
    r_m.font.bold = True
    r_m.font.color.rgb = COLOR_NAVY

    # University Seal
    p_logo = doc.add_paragraph()
    p_logo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_logo.paragraph_format.space_before = Pt(8)
    p_logo.paragraph_format.space_after = Pt(12)
    logo_path = os.path.join(ASSETS_DIR, "vit_logo.png")
    if os.path.exists(logo_path):
        p_logo.add_run().add_picture(logo_path, width=Inches(3.2))

    p_sch = doc.add_paragraph()
    p_sch.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sch.paragraph_format.space_before = Pt(10)
    p_sch.paragraph_format.space_after = Pt(4)
    r_sch = p_sch.add_run("SCHOOL OF COMPUTER SCIENCE AND ENGINEERING\nVELLORE INSTITUTE OF TECHNOLOGY, CHENNAI")
    r_sch.font.name = FONT_MAIN
    r_sch.font.size = Pt(13)
    r_sch.font.bold = True
    r_sch.font.color.rgb = COLOR_NAVY

    p_date = doc.add_paragraph()
    p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_date.paragraph_format.space_before = Pt(4)
    p_date.paragraph_format.space_after = Pt(0)
    r_date = p_date.add_run("October, 2026")
    r_date.font.name = FONT_MAIN
    r_date.font.size = Pt(12)
    r_date.font.color.rgb = COLOR_BODY

    doc.add_page_break()

    # =========================================================================
    # 2. TITLE PAGE
    # =========================================================================
    p0_2 = doc.add_paragraph()
    p0_2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p0_2.paragraph_format.space_before = Pt(12)
    p0_2.paragraph_format.space_after = Pt(4)
    r0_2 = p0_2.add_run("A project report on")
    r0_2.font.name = FONT_MAIN
    r0_2.font.size = Pt(12)

    p_title2 = doc.add_paragraph()
    p_title2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title2.paragraph_format.space_before = Pt(14)
    p_title2.paragraph_format.space_after = Pt(18)
    r_title2 = p_title2.add_run("A NOVEL FAULT-AWARE MULTI-AGENT REINFORCEMENT LEARNING FRAMEWORK FOR RESILIENT USV SWARM ENCIRCLEMENT")
    r_title2.font.name = FONT_MAIN
    r_title2.font.size = Pt(18)
    r_title2.font.bold = True
    r_title2.font.color.rgb = COLOR_NAVY

    p_sub2 = doc.add_paragraph()
    p_sub2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sub2.paragraph_format.space_before = Pt(6)
    p_sub2.paragraph_format.space_after = Pt(6)
    r_sub2 = p_sub2.add_run("Submitted in partial fulfillment for the award of the degree of")
    r_sub2.font.name = FONT_MAIN
    r_sub2.font.size = Pt(13)

    p_deg2 = doc.add_paragraph()
    p_deg2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_deg2.paragraph_format.space_before = Pt(6)
    p_deg2.paragraph_format.space_after = Pt(18)
    r_deg2 = p_deg2.add_run("Bachelor of Technology in\nComputer Science and Engineering")
    r_deg2.font.name = FONT_MAIN
    r_deg2.font.size = Pt(16)
    r_deg2.font.bold = True
    r_deg2.font.color.rgb = COLOR_NAVY

    p_by2 = doc.add_paragraph()
    p_by2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_by2.paragraph_format.space_before = Pt(4)
    p_by2.paragraph_format.space_after = Pt(4)
    r_by2 = p_by2.add_run("by")
    r_by2.font.name = FONT_MAIN
    r_by2.font.size = Pt(13)

    p_team2 = doc.add_paragraph()
    p_team2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_team2.paragraph_format.space_before = Pt(6)
    p_team2.paragraph_format.space_after = Pt(16)
    r_m2 = p_team2.add_run("PRITISH BHATASANA (23BRS1035)\n")
    r_m2.font.name = FONT_MAIN
    r_m2.font.size = Pt(14)
    r_m2.font.bold = True
    r_m2.font.color.rgb = COLOR_NAVY

    if os.path.exists(logo_path):
        p_logo2 = doc.add_paragraph()
        p_logo2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_logo2.paragraph_format.space_before = Pt(8)
        p_logo2.paragraph_format.space_after = Pt(12)
        p_logo2.add_run().add_picture(logo_path, width=Inches(3.2))

    p_sch2 = doc.add_paragraph()
    p_sch2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_sch2.paragraph_format.space_before = Pt(10)
    p_sch2.paragraph_format.space_after = Pt(4)
    r_sch2 = p_sch2.add_run("SCHOOL OF COMPUTER SCIENCE AND ENGINEERING\nVELLORE INSTITUTE OF TECHNOLOGY, CHENNAI")
    r_sch2.font.name = FONT_MAIN
    r_sch2.font.size = Pt(13)
    r_sch2.font.bold = True
    r_sch2.font.color.rgb = COLOR_NAVY

    p_date2 = doc.add_paragraph()
    p_date2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_date2.paragraph_format.space_before = Pt(4)
    p_date2.paragraph_format.space_after = Pt(0)
    r_date2 = p_date2.add_run("October, 2026")
    r_date2.font.name = FONT_MAIN
    r_date2.font.size = Pt(12)

    doc.add_page_break()

    # =========================================================================
    # 3. DECLARATION
    # =========================================================================
    p_dec_h = doc.add_paragraph()
    p_dec_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_dec_h.paragraph_format.space_before = Pt(36)
    p_dec_h.paragraph_format.space_after = Pt(24)
    r_dh = p_dec_h.add_run("Declaration")
    r_dh.font.name = FONT_MAIN
    r_dh.font.size = Pt(14)
    r_dh.font.bold = True
    r_dh.font.color.rgb = COLOR_NAVY

    add_paragraph(
        doc,
        "I hereby declare that the project report entitled “A Novel Fault-Aware Multi-Agent Reinforcement Learning "
        "Framework for Resilient USV Swarm Encirclement” submitted by PRITISH BHATASANA (Register No.: 23BRS1035) to "
        "Vellore Institute of Technology, Chennai, for the degree of Bachelor of Technology in Computer Science and Engineering "
        "is an authentic record of original project work carried out by me under the guidance of Dr. Linda Joseph, "
        "School of Computer Science and Engineering.",
        space_after=14
    )
    add_paragraph(
        doc,
        "I also declare that the material presented in this report has not been submitted previously, in whole or in part, "
        "to this or any other academic institution for the award of any degree or diploma.",
        space_after=36
    )

    p_sig = doc.add_paragraph()
    p_sig.paragraph_format.space_before = Pt(30)
    p_sig.paragraph_format.line_spacing = 1.3
    r_sig1 = p_sig.add_run("Place: Chennai\nDate: 16-10-2026\n\n\n\n")
    r_sig1.font.name = FONT_MAIN
    r_sig1.font.size = Pt(12)

    r_sig2 = p_sig.add_run(
        "_____________________________________\n"
        "Signature of the Candidate:\n"
        "PRITISH BHATASANA (23BRS1035)"
    )
    r_sig2.font.name = FONT_MAIN
    r_sig2.font.size = Pt(11)
    r_sig2.font.bold = True
    r_sig2.font.color.rgb = COLOR_NAVY

    doc.add_page_break()

    # =========================================================================
    # 4. CERTIFICATE
    # =========================================================================
    p_cert_sch = doc.add_paragraph()
    p_cert_sch.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_cert_sch.paragraph_format.space_before = Pt(24)
    p_cert_sch.paragraph_format.space_after = Pt(4)
    r_csch = p_cert_sch.add_run("School of Computer Science and Engineering")
    r_csch.font.name = FONT_MAIN
    r_csch.font.size = Pt(13)
    r_csch.font.bold = True
    r_csch.font.color.rgb = COLOR_NAVY

    p_cert_h = doc.add_paragraph()
    p_cert_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_cert_h.paragraph_format.space_before = Pt(8)
    p_cert_h.paragraph_format.space_after = Pt(24)
    r_ch = p_cert_h.add_run("Certificate")
    r_ch.font.name = FONT_MAIN
    r_ch.font.size = Pt(14)
    r_ch.font.bold = True
    r_ch.font.color.rgb = COLOR_NAVY

    add_paragraph(
        doc,
        "This is to certify that the project report entitled “A Novel Fault-Aware Multi-Agent Reinforcement Learning "
        "Framework for Resilient USV Swarm Encirclement” submitted by PRITISH BHATASANA (Register Number: 23BRS1035) to "
        "Vellore Institute of Technology, Chennai, for the award of Bachelor of Technology in Computer Science and Engineering, "
        "represents bonafide project work carried out under my supervision during the academic year 2025–2026.",
        space_after=14
    )
    add_paragraph(
        doc,
        "To the best of my knowledge, the findings and implementations reported herein have not been submitted elsewhere "
        "for the conferral of any other academic degree or qualification.",
        space_after=24
    )

    p_guide = doc.add_paragraph()
    p_guide.paragraph_format.space_before = Pt(24)
    p_guide.paragraph_format.line_spacing = 1.3
    r_g = p_guide.add_run(
        "Signature of the Guide: ______________________\n"
        "Name: Dr. Linda Joseph\n"
        "Designation: Associate Professor, SCOPE\n"
        "Date: 16-10-2026\n\n\n"
        "Signature of Internal Examiner: ______________________\t\tSignature of External Examiner: ______________________\n"
        "Date: 16-10-2026\t\t\t\t\t\t\tDate: 16-10-2026\n\n\n"
        "Approved by the Head of Department,\n"
        "B.Tech. Computer Science and Engineering\n"
        "Name: Dr. Sathian D.\n"
        "Date: 16-10-2026\n\n"
        "(Seal of the School)"
    )
    r_g.font.name = FONT_MAIN
    r_g.font.size = Pt(11)

    doc.add_page_break()

    # =========================================================================
    # 5. ABSTRACT
    # =========================================================================
    p_abs_h = doc.add_paragraph()
    p_abs_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_abs_h.paragraph_format.space_before = Pt(36)
    p_abs_h.paragraph_format.space_after = Pt(20)
    r_ah = p_abs_h.add_run("Abstract")
    r_ah.font.name = FONT_MAIN
    r_ah.font.size = Pt(14)
    r_ah.font.bold = True
    r_ah.font.color.rgb = COLOR_NAVY

    add_paragraph(
        doc,
        "Littoral multi-vessel missions require autonomous surface fleets to maintain coordinated encircling formations "
        "around dynamic marine targets. While multi-agent reinforcement learning (MARL) algorithms coordinate nominal multi-USV "
        "formations effectively, they degrade rapidly when real-world faults strike. Physical thruster entanglements, GNSS spoofing "
        "drifts, and RF maritime link dropouts perturb individual vessel trajectories, frequently causing unhandled policy cascades, "
        "formation clumping, and boundary clearance violations.",
        space_after=8
    )
    add_paragraph(
        doc,
        "This capstone report details FA-MAPPO, an integrated fault-resilient swarm encirclement architecture developed and "
        "validated in ROS 2 Humble. The architecture decouples tactical swarm reconfiguration from instantaneous kinematic collision "
        "avoidance through four core modules. First, a 14-dimensional kinematic residual engine continuously monitors vessel states, "
        "feeding a 100-estimator XGBoost classifier running at 5 Hz that isolates actuator, sensor, and link faults with 97.4% accuracy. "
        "Second, a Kuhn-Munkres bipartite slot allocator reassigns formation orbits across remaining healthy craft, regulated by a 4.0 m "
        "analytical hysteresis deadband to suppress role chattering. Third, a 20 Hz Control Barrier Function (CBF) Quadratic Program "
        "enforces strict pairwise vessel separation (d_safe ≥ 3.5 m) as a provably forward-invariant safety filter. Fourth, an adaptive "
        "wireless channel manager employs TDMA time-slots and dead-reckoning state propagation to withstand RF shadowing up to 2.5 seconds.",
        space_after=8
    )
    add_paragraph(
        doc,
        "Across 120 Monte Carlo simulation runs in non-linear 3-DOF Fossen hydrodynamics, FA-MAPPO achieved a 95.8% target encirclement "
        "retention rate under multi-fault conditions, cutting Mean Time to Reconfigure to 1.70 s (a 70% reduction relative to unaugmented "
        "MAPPO). The safety filter recorded zero inter-vessel barrier violations. Empirical execution latency on the host development "
        "workstation demonstrated a critical path of 21.35 ms (0.85 ms for the safety shield), and edge timing budget modeling confirms "
        "that real-time fault-tolerant swarm control is computationally feasible within recommended embedded platforms such as the NVIDIA Jetson Orin Nano.",
        space_after=12
    )

    p_kw = doc.add_paragraph()
    p_kw.paragraph_format.space_before = Pt(8)
    p_kw.paragraph_format.space_after = Pt(12)
    p_kw.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    r_kw_lbl = p_kw.add_run("Keywords: ")
    r_kw_lbl.font.name = FONT_MAIN
    r_kw_lbl.font.size = Pt(11)
    r_kw_lbl.font.bold = True
    r_kw_lbl.font.color.rgb = COLOR_NAVY
    r_kw_txt = p_kw.add_run(
        "Unmanned Surface Vehicles (USVs), Multi-Agent Reinforcement Learning (MARL), Fault Detection and Isolation (FDI), "
        "Control Barrier Functions (CBFs), Kuhn-Munkres Hungarian Matching, Fossen Hydrodynamics, Resilient Mesh Networking, ROS 2 Humble."
    )
    r_kw_txt.font.name = FONT_MAIN
    r_kw_txt.font.size = Pt(11)
    r_kw_txt.font.italic = True

    doc.add_page_break()

    # =========================================================================
    # 6. ACKNOWLEDGEMENT
    # =========================================================================
    p_ack_h = doc.add_paragraph()
    p_ack_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_ack_h.paragraph_format.space_before = Pt(36)
    p_ack_h.paragraph_format.space_after = Pt(20)
    r_akh = p_ack_h.add_run("Acknowledgement")
    r_akh.font.name = FONT_MAIN
    r_akh.font.size = Pt(14)
    r_akh.font.bold = True
    r_akh.font.color.rgb = COLOR_NAVY

    add_paragraph(
        doc,
        "I express my sincere gratitude to my project guide, Dr. Linda Joseph, Associate Professor in the School of Computer "
        "Science and Engineering, Vellore Institute of Technology, Chennai. Her continuous guidance, thoughtful technical critiques, "
        "and encouragement throughout the lifecycle of this capstone project proved invaluable in formulating the fault-diagnosis "
        "pipeline and refining the mathematical formulations.",
        space_after=8
    )
    add_paragraph(
        doc,
        "I am grateful to Dr. Sathian D., Head of the Department, and the faculty members of the School of Computer Science and "
        "Engineering for providing the academic curriculum, institutional resources, and laboratory infrastructure required to "
        "undertake this robotics project.",
        space_after=8
    )
    add_paragraph(
        doc,
        "I also thank the leadership of Vellore Institute of Technology for maintaining high-performance computing facilities and "
        "simulation environments that made the extensive Monte Carlo experimental runs possible.",
        space_after=8
    )
    add_paragraph(
        doc,
        "Lastly, I thank my parents, family, and peers for their steady support and encouragement throughout my undergraduate studies.",
        space_after=24
    )

    p_acksig = doc.add_paragraph()
    p_acksig.paragraph_format.space_before = Pt(20)
    p_acksig.paragraph_format.line_spacing = 1.3
    r_as = p_acksig.add_run(
        "Place: Chennai\n"
        "Date: 16-10-2026\n\n"
        "PRITISH BHATASANA (23BRS1035)"
    )
    r_as.font.name = FONT_MAIN
    r_as.font.size = Pt(11)
    r_as.font.bold = True
    r_as.font.color.rgb = COLOR_NAVY

    doc.add_page_break()

    # =========================================================================
    # 7. TABLE OF CONTENTS
    # =========================================================================
    p_toc_h = doc.add_paragraph()
    p_toc_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_toc_h.paragraph_format.space_before = Pt(36)
    p_toc_h.paragraph_format.space_after = Pt(20)
    r_th = p_toc_h.add_run("Contents")
    r_th.font.name = FONT_MAIN
    r_th.font.size = Pt(16)
    r_th.font.bold = True
    r_th.font.color.rgb = COLOR_NAVY

    toc_entries = [
        ("Declaration", "iii", False),
        ("Certificate", "iv", False),
        ("Abstract", "v", False),
        ("Acknowledgement", "vi", False),
        ("LIST OF TABLES", "xi", True),
        ("LIST OF FIGURES", "xiii", True),
        ("LIST OF SYMBOLS, ABBREVIATIONS AND NOMENCLATURE", "xv", True),
        ("CHAPTER 1: INTRODUCTION", "1", True),
        ("  1.1  Background and motivation", "1", False),
        ("  1.2  Problem statement", "2", False),
        ("  1.3  Research gap", "2", False),
        ("  1.4  Aim and objectives", "3", False),
        ("  1.5  Scope and assumptions", "4", False),
        ("  1.6  Contributions", "4", False),
        ("  1.7  Organization of the report", "5", False),
        ("CHAPTER 2: LITERATURE REVIEW AND BACKGROUND", "6", True),
        ("  2.1  Thematic sub-reviews", "6", False),
        ("    2.1.1  Maritime USV hydrodynamics and control", "6", False),
        ("    2.1.2  Multi-agent reinforcement learning for target encirclement", "7", False),
        ("    2.1.3  Fault detection, isolation, and resilient reconfiguration", "7", False),
        ("    2.1.4  Safety filters and control barrier functions", "7", False),
        ("    2.1.5  Maritime RF propagation and co-channel contention", "8", False),
        ("  2.2  Literature timeline and method convergence", "8", False),
        ("  2.3  Complementary swarm roles and characteristic failure modes", "8", False),
        ("  2.4  Taxonomy of maritime swarm fault modes and recovery levels", "9", False),
        ("  2.5  Comparative summary and identified gap", "9", False),
        ("CHAPTER 3: PROBLEM FORMULATION AND PROPOSED ARCHITECTURE", "12", True),
        ("  3.1  Task definition and mathematical notation", "12", False),
        ("  3.2  Nonlinear Fossen 3-DOF hydrodynamic equations of motion", "12", False),
        ("  3.3  Target encirclement geometry and standoff orbit metrics", "14", False),
        ("  3.4  Dual-loop fault-aware cooperative control architecture", "15", False),
        ("  3.5  14-residual kinematic feature extraction and FDI formulation", "16", False),
        ("  3.6  Kuhn-Munkres bipartite matching with hysteresis barrier", "17", False),
        ("  3.7  Control barrier functions and forward invariance safety filter", "18", False),
        ("  3.8  Marine RF two-ray multipath reflection and Fresnel zone model", "19", False),
        ("  3.9  Multi-task FA-MAPPO objective and centralized value function", "20", False),
        ("  3.10 Training and inference procedure (Algorithms 1 & 2)", "21", False),
        ("  3.11 Complexity considerations", "21", False),
        ("CHAPTER 4: SYSTEM DESIGN AND IMPLEMENTATION", "22", True),
        ("  4.1  Design goals and architectural segregation", "22", False),
        ("  4.2  Four-layer wireless degradation mitigation architecture", "23", False),
        ("  4.3  Data pipeline, vessel parameters, and simulation preprocessing", "23", False),
        ("  4.4  Distributed ROS 2 node organization and DDS interfaces", "24", False),
        ("  4.5  Swarm configuration variants and component activity", "26", False),
        ("  4.6  Mathematical equation provenance and derivation mapping", "26", False),
        ("  4.7  Software and hardware deployment environment", "27", False),
        ("  4.8  Implementation evidence and engineering checklist", "28", False),
        ("CHAPTER 5: EXPERIMENTAL SETUP AND EVALUATION METHODOLOGY", "29", True),
        ("  5.1  Benchmark mission scenarios and environmental partitions", "29", False),
        ("  5.2  Model configurations and ablation baselines", "29", False),
        ("  5.3  Training, validation, and real-time deployment protocol", "30", False),
        ("  5.4  Evaluation metrics and statistical uncertainty methodology", "31", False),
        ("    5.4.1  Statistical uncertainty of accuracy: 95% Wilson confidence intervals", "31", False),
        ("    5.4.2  Unpaired two-sample hypothesis z-tests", "31", False),
        ("  5.5  Hyperparameters and checkpoint selection criteria", "31", False),
        ("  5.6  Threats to validity and engineering mitigations", "32", False),
        ("CHAPTER 6: RESULTS, ANALYSIS AND DISCUSSION", "34", True),
        ("  6.1  Overall quantitative results matrix across configurations", "34", False),
        ("  6.2  Simulation training versus real-time ROS 2 deployment gap", "36", False),
        ("  6.3  Multi-metric Pareto ranking across configurations", "37", False),
        ("  6.4  Statistical uncertainty of reported metrics (Wilson bounds)", "38", False),
        ("  6.5  Hypothesis testing across ablation baselines", "39", False),
        ("  6.6  Paired component ablation study", "40", False),
        ("  6.7  Dynamic mission performance and trajectory stability", "40", False),
        ("  6.8  Multi-metric performance heatmap across all variants", "42", False),
        ("  6.9  Computational profiling and real-time latency verification", "43", False),
        ("  6.10 Trade-off frontier: Encirclement success vs compute overhead", "44", False),
        ("  6.11 Qualitative discussion and root-cause analysis", "44", False),
        ("  6.12 Operational limitations", "45", False),
        ("CHAPTER 7: CONCLUSION AND FUTURE WORK", "46", True),
        ("  7.1  Summary of work and key findings", "46", False),
        ("  7.2  Formal status of project objectives (O1 through O8)", "46", False),
        ("  7.3  Engineering limitations", "47", False),
        ("  7.4  Prioritized future field-testing roadmap", "47", False),
        ("  7.5  Closing remarks", "48", False),
        ("REFERENCES", "49", True),
        ("APPENDIX A: SOURCE AND MISSING-INFORMATION REGISTER", "51", True),
        ("APPENDIX B: NUMERICAL DATA UNDERLYING THE FIGURES", "53", True),
        ("APPENDIX C: ADDITIONAL ALGORITHMIC SUBROUTINES", "54", True),
        ("APPENDIX D: FORMAL MATHEMATICAL PROOFS AND DERIVATIONS", "55", True),
    ]

    toc_table = doc.add_table(rows=len(toc_entries), cols=2)
    toc_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_borders(toc_table, val="none")

    for i, (entry_title, page_num, is_major) in enumerate(toc_entries):
        c_title = toc_table.rows[i].cells[0]
        c_page = toc_table.rows[i].cells[1]
        c_title.width = Inches(5.1)
        c_page.width = Inches(0.67)

        p_t = c_title.paragraphs[0]
        p_t.paragraph_format.space_before = Pt(3 if is_major else 1)
        p_t.paragraph_format.space_after = Pt(3 if is_major else 1)
        p_t.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r_t = p_t.add_run(entry_title)
        r_t.font.name = FONT_MAIN
        r_t.font.size = Pt(10.5 if is_major else 10)
        r_t.font.bold = is_major
        r_t.font.color.rgb = COLOR_NAVY if is_major else COLOR_BODY

        p_p = c_page.paragraphs[0]
        p_p.paragraph_format.space_before = Pt(3 if is_major else 1)
        p_p.paragraph_format.space_after = Pt(3 if is_major else 1)
        p_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_p = p_p.add_run(page_num)
        r_p.font.name = FONT_MAIN
        r_p.font.size = Pt(10.5 if is_major else 10)
        r_p.font.bold = is_major
        r_p.font.color.rgb = COLOR_NAVY if is_major else COLOR_BODY

    doc.add_page_break()

    # =========================================================================
    # 8. LIST OF TABLES
    # =========================================================================
    p_lot_h = doc.add_paragraph()
    p_lot_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_lot_h.paragraph_format.space_before = Pt(36)
    p_lot_h.paragraph_format.space_after = Pt(20)
    r_lth = p_lot_h.add_run("LIST OF TABLES")
    r_lth.font.name = FONT_MAIN
    r_lth.font.size = Pt(16)
    r_lth.font.bold = True
    r_lth.font.color.rgb = COLOR_NAVY

    tables_catalog = [
        ("Table 1", "Nomenclature of mathematical symbols and physical dimensions", "xv"),
        ("Table 2", "List of abbreviations and technical acronyms", "xvii"),
        ("Table 3", "Project objectives mapped to deliverables and report chapters", "3"),
        ("Table 4", "Complementary strengths and failure modes of swarm roles", "8"),
        ("Table 5", "Comprehensive comparison of reviewed maritime multi-agent works", "9"),
        ("Table 6", "Feature and sensor state vector dimensions in swarm pipeline", "16"),
        ("Table 7", "Fossen hydrodynamic simulation and Clearpath Heron vessel parameters", "23"),
        ("Table 8", "Distributed ROS 2 node organization and DDS topic interfaces", "25"),
        ("Table 9", "Active architectural components across swarm configuration variants", "26"),
        ("Table 10", "Mathematical equation provenance and derivation mapping", "26"),
        ("Table 11", "Development workstation environment and recommended embedded edge target specifications", "27"),
        ("Table 12", "Mission scenario partitions and fault-injection parameters", "29"),
        ("Table 13", "Training and hyperparameter configuration for the FA-MAPPO policy and FDI classifier", "31"),
        ("Table 14", "Threats to experimental validity and engineering mitigations", "32"),
        ("Table 15", "Master quantitative benchmark results across all 6 swarm configurations", "34"),
        ("Table 16", "Validation simulation versus real-time ROS 2 deployment gap", "36"),
        ("Table 17", "Multi-metric Pareto ranking of swarm control architectures", "37"),
        ("Table 18", "Encirclement success rates with 95% Wilson confidence intervals", "38"),
        ("Table 19", "Unpaired two-sample z-test hypothesis comparisons across baselines", "39"),
        ("Table 20", "Paired ablation differences in accuracy and safety violations", "40"),
        ("Table 21", "Computational profiling: per-node execution latency on host and recommended edge budget", "43"),
        ("Table 22", "Root-cause fault cascade hypotheses and empirical verification", "44"),
        ("Table 23", "Synthesis of supported empirical claims and architectural evidence", "45"),
        ("Table 24", "Formal status of project objectives (O1 through O8)", "46"),
        ("Table 25", "Prioritized future engineering and field-testing roadmap", "47"),
        ("Table 26", "Register of architectural parameters derived from literature", "51"),
        ("Table 27", "Register of missing real-world operational parameters and assumptions", "51"),
        ("Table 28", "Traceability matrix: conflicts identified, analyzed, and resolved", "52"),
        ("Table 29", "Exact numerical benchmark raw data underlying Figures 12 through 23", "53"),
        ("Table 30", "Analytical derivation parameter definitions for CBF safety proofs", "55")
    ]

    lot_table = doc.add_table(rows=len(tables_catalog), cols=3)
    lot_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_borders(lot_table, val="none")

    for i, (t_num, t_cap, t_pg) in enumerate(tables_catalog):
        c_num = lot_table.rows[i].cells[0]
        c_cap = lot_table.rows[i].cells[1]
        c_pg = lot_table.rows[i].cells[2]
        c_num.width = Inches(1.0)
        c_cap.width = Inches(4.1)
        c_pg.width = Inches(0.67)

        p_n = c_num.paragraphs[0]
        p_n.paragraph_format.space_before = Pt(2)
        p_n.paragraph_format.space_after = Pt(2)
        r_n = p_n.add_run(t_num)
        r_n.font.name = FONT_MAIN
        r_n.font.size = Pt(10)
        r_n.font.bold = True
        r_n.font.color.rgb = COLOR_NAVY

        p_c = c_cap.paragraphs[0]
        p_c.paragraph_format.space_before = Pt(2)
        p_c.paragraph_format.space_after = Pt(2)
        r_c = p_c.add_run(t_cap)
        r_c.font.name = FONT_MAIN
        r_c.font.size = Pt(10)
        r_c.font.color.rgb = COLOR_BODY

        p_p = c_pg.paragraphs[0]
        p_p.paragraph_format.space_before = Pt(2)
        p_p.paragraph_format.space_after = Pt(2)
        p_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_p = p_p.add_run(t_pg)
        r_p.font.name = FONT_MAIN
        r_p.font.size = Pt(10)
        r_p.font.color.rgb = COLOR_SLATE

    doc.add_page_break()

    # =========================================================================
    # 9. LIST OF FIGURES
    # =========================================================================
    p_lof_h = doc.add_paragraph()
    p_lof_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_lof_h.paragraph_format.space_before = Pt(36)
    p_lof_h.paragraph_format.space_after = Pt(20)
    r_lfh = p_lof_h.add_run("LIST OF FIGURES")
    r_lfh.font.name = FONT_MAIN
    r_lfh.font.size = Pt(16)
    r_lfh.font.bold = True
    r_lfh.font.color.rgb = COLOR_NAVY

    figures_catalog = [
        ("Figure 1", "Timeline of maritime robotics, MARL, and FDI methodologies (1994–2026)", "8"),
        ("Figure 2", "Taxonomy of maritime swarm fault modes and tiered recovery hierarchy", "9"),
        ("Figure 3", "Fossen 3-DOF hydrodynamic coordinate frames and free-body force diagram", "14"),
        ("Figure 4", "Swarm target encirclement orbital geometry, dynamic slots, and angular spread", "15"),
        ("Figure 5", "Complete fault-aware architecture: dual-loop FA-MAPPO planner and 20 Hz CBF", "15"),
        ("Figure 6", "Multi-stage FDI feature extraction pipeline to XGBoost multi-class classifier", "17"),
        ("Figure 7", "Kuhn-Munkres bipartite matching and 4.0m hysteresis margin deadband", "18"),
        ("Figure 8", "Control barrier function (CBF) forward invariance and QP projection geometry", "19"),
        ("Figure 9", "Marine RF two-ray multipath reflection and wave-induced Fresnel obstruction", "20"),
        ("Figure 10", "Four-layer wireless degradation mitigation architecture for disrupted meshes", "23"),
        ("Figure 11", "ROS 2 distributed 9-package node graph and intra-swarm DDS topic topology", "25"),
        ("Figure 12", "XGBoost 4x4 FDI confusion matrix and precision-recall curves across fault modes", "30"),
        ("Figure 13", "3x3 benchmark grid: multi-scenario quantitative comparison across baselines", "36"),
        ("Figure 14", "Simulation-to-ROS 2 performance gap across swarm configurations", "37"),
        ("Figure 15", "Consistency of architectural Pareto ranking across idealized and real-time mesh", "37"),
        ("Figure 16", "MTTR versus final encirclement tightness scatter across 120 simulation trials", "38"),
        ("Figure 17", "Statistical uncertainty of mission success: 95% Wilson confidence intervals", "38"),
        ("Figure 18", "Paired component ablation study: quantitative impact of recovery modules", "40"),
        ("Figure 19", "Formation retention macro-F1 trajectory during staggered multi-fault cascade", "41"),
        ("Figure 20", "Standoff ring radial tracking error and orbit convergence trajectory", "41"),
        ("Figure 21", "Multi-metric operational heatmap: normalized evaluation across key indicators", "42"),
        ("Figure 22", "Computational profiling & real-time hard-deadline verification (log scale)", "43"),
        ("Figure 23", "Mission success versus embedded execution latency trade-off frontier", "44")
    ]

    lof_table = doc.add_table(rows=len(figures_catalog), cols=3)
    lof_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_borders(lof_table, val="none")

    for i, (f_num, f_cap, f_pg) in enumerate(figures_catalog):
        c_num = lof_table.rows[i].cells[0]
        c_cap = lof_table.rows[i].cells[1]
        c_pg = lof_table.rows[i].cells[2]
        c_num.width = Inches(1.0)
        c_cap.width = Inches(4.1)
        c_pg.width = Inches(0.67)

        p_n = c_num.paragraphs[0]
        p_n.paragraph_format.space_before = Pt(2)
        p_n.paragraph_format.space_after = Pt(2)
        r_n = p_n.add_run(f_num)
        r_n.font.name = FONT_MAIN
        r_n.font.size = Pt(10)
        r_n.font.bold = True
        r_n.font.color.rgb = COLOR_NAVY

        p_c = c_cap.paragraphs[0]
        p_c.paragraph_format.space_before = Pt(2)
        p_c.paragraph_format.space_after = Pt(2)
        r_c = p_c.add_run(f_cap)
        r_c.font.name = FONT_MAIN
        r_c.font.size = Pt(10)
        r_c.font.color.rgb = COLOR_BODY

        p_p = c_pg.paragraphs[0]
        p_p.paragraph_format.space_before = Pt(2)
        p_p.paragraph_format.space_after = Pt(2)
        p_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_p = p_p.add_run(f_pg)
        r_p.font.name = FONT_MAIN
        r_p.font.size = Pt(10)
        r_p.font.color.rgb = COLOR_SLATE

    doc.add_page_break()

    # =========================================================================
    # 10. LIST OF SYMBOLS, ABBREVIATIONS AND NOMENCLATURE
    # =========================================================================
    p_nom_h = doc.add_paragraph()
    p_nom_h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_nom_h.paragraph_format.space_before = Pt(36)
    p_nom_h.paragraph_format.space_after = Pt(14)
    r_nh = p_nom_h.add_run("LIST OF SYMBOLS, ABBREVIATIONS AND NOMENCLATURE")
    r_nh.font.name = FONT_MAIN
    r_nh.font.size = Pt(14)
    r_nh.font.bold = True
    r_nh.font.color.rgb = COLOR_NAVY

    add_paragraph(
        doc,
        "Symbols are listed in the order in which they appear in the mathematical formulations of Chapter 3. "
        "Physical units and tensor shapes correspond to the 3-DOF Fossen marine hydrodynamics simulation and ROS 2 coordinate conventions.",
        space_after=8
    )

    nom_headers = ["Symbol", "Meaning", "Shape / Physical Unit"]
    nom_data = [
        ["{n}", "Earth-fixed North-East-Down (NED) inertial reference frame", "3-D Coordinate Frame"],
        ["{b}", "Body-fixed vessel-centered reference frame (origin at center of gravity)", "3-D Coordinate Frame"],
        ["η", "Vessel pose vector in NED frame [x_N, y_E, ψ]^T", "R^3 [m, m, rad]"],
        ["ν", "Vessel body-fixed generalized velocity vector [u, v, r]^T", "R^3 [m/s, m/s, rad/s]"],
        ["u, v, r", "Surge velocity, sway velocity, and yaw angular rate", "Scalars [m/s, m/s, rad/s]"],
        ["J(ψ)", "Kinematic Euler-angle rotation transformation matrix", "R^(3×3) Orthogonal Matrix"],
        ["M_RB", "Rigid-body inertia matrix of the vessel hull", "R^(3×3) Symmetric Pos. Def."],
        ["M_A", "Hydrodynamic added mass matrix due to displaced fluid entrainment", "R^(3×3) Symmetric Matrix"],
        ["C_RB(ν)", "Coriolis and centripetal matrix of the rigid-body mass", "R^(3×3) Skew-Symmetric"],
        ["C_A(ν)", "Hydrodynamic Coriolis and centripetal matrix of added mass", "R^(3×3) Skew-Symmetric"],
        ["D(ν)", "Hydrodynamic damping matrix combining linear and quadratic drag terms", "R^(3×3) Strictly Positive Def."],
        ["τ", "Generalized thruster control force and yaw moment vector [τ_u, 0, τ_r]^T", "R^3 [N, 0, N·m]"],
        ["w_env", "Unmodeled environmental disturbance forces (wave drift and wind gusts)", "R^3 [N, N, N·m]"],
        ["R_orbit", "Target encirclement standoff ring radius", "Scalar: 20.0 m"],
        ["ψ_k(t)", "Dynamic angular assignment coordinate for encirclement slot k", "Scalar [rad]"],
        ["Δφ", "Angular spread between adjacent encircling vessels", "Scalar [rad] (Thresh: ≥ 4.712)"],
        ["Γ", "Maximum angular encirclement gap around perimeter", "Scalar [rad] (Thresh: ≤ 1.571)"],
        ["h_k", "Multi-class health classification vector for vessel k", "R^4 (Values in [0, 1])"],
        ["r_k^(pos)", "Kinematic residual between measured GPS and EKF projected pose", "Scalar [m]"],
        ["C_ij", "Kuhn-Munkres cost matrix between agent i and slot j", "R^(N×N) [m]"],
        ["ΔH", "Role transition hysteresis deadband margin", "Scalar: 4.0 m"],
        ["h(x)", "Zeroing Control Barrier Function (ZCBF) safety margin", "Scalar (Safe set: h(x) ≥ 0)"],
        ["d_safe", "Pairwise inter-vessel minimum clearance threshold", "Scalar: 3.5 m"],
        ["γ", "Extended CBF class-K linear decay convergence rate parameter", "Scalar: 1.5 s^(-1)"],
        ["u_des", "Nominal unconstrained control input from high-level FA-MAPPO policy", "R^2 [N, N·m]"],
        ["u*", "Safe control input projected via minimum-deviation Quadratic Program", "R^2 [N, N·m]"],
        ["H_s", "Significant wave height of dynamic sea state", "Scalar: 1.25 m (Sea State 3)"]
    ]
    add_table(doc, 1, "Nomenclature of mathematical symbols and physical dimensions", nom_headers, nom_data, [1.0, 3.2, 1.57])

    p_abb_h = doc.add_paragraph()
    p_abb_h.paragraph_format.space_before = Pt(14)
    p_abb_h.paragraph_format.space_after = Pt(4)
    r_abh = p_abb_h.add_run("Abbreviations")
    r_abh.font.name = FONT_MAIN
    r_abh.font.size = Pt(12)
    r_abh.font.bold = True
    r_abh.font.color.rgb = COLOR_NAVY

    abb_headers = ["Abbreviation", "Complete Formal Expansion"]
    abb_data = [
        ["USV", "Unmanned Surface Vehicle"],
        ["MARL", "Multi-Agent Reinforcement Learning"],
        ["FA-MAPPO", "Fault-Aware Multi-Agent Proximal Policy Optimization"],
        ["CBF", "Control Barrier Function"],
        ["ZCBF", "Zeroing Control Barrier Function"],
        ["FDI", "Fault Detection and Isolation"],
        ["DOF", "Degrees of Freedom"],
        ["NED", "North-East-Down (Inertial Navigation Coordinate System)"],
        ["RK4", "4th-Order Runge-Kutta Numerical Integration"],
        ["EKF", "Extended Kalman Filter"],
        ["DDS", "Data Distribution Service (OMG Standard for Distributed Middleware)"],
        ["ROS 2", "Robot Operating System 2 (Humble Hawksbill LTS Distribution)"],
        ["TDMA", "Time-Division Multiple Access"],
        ["MTTR", "Mean Time To Reconfigure / Recover"],
        ["QP", "Quadratic Programming"],
        ["APF", "Artificial Potential Field"],
        ["IPPO", "Independent Proximal Policy Optimization"],
        ["PPO", "Proximal Policy Optimization"],
        ["ROC", "Receiver Operating Characteristic"],
        ["PR", "Precision-Recall"],
        ["CI", "Confidence Interval (Wilson Score Method)"],
        ["ISM", "Industrial, Scientific and Medical (2.4 GHz RF Band)"]
    ]
    add_table(doc, 2, "List of abbreviations and technical acronyms", abb_headers, abb_data, [1.5, 4.27])

    doc.add_page_break()
