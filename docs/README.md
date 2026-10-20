# USV Swarm Capstone — Documentation Hub

Welcome to the complete documentation repository for the **Fault-Aware Multi-Agent USV Swarm** capstone project. This repository contains the complete theoretical, architectural, implementation, and empirical verification record for a cooperative maritime swarm tracking and encircling an evasive target under severe node degradation (up to 30%) and communication loss (up to 40%).

---

## Documentation Navigation Map

| Document | Description | Key Topics |
| :--- | :--- | :--- |
| **[FINAL_CAPSTONE_REPORT.md](FINAL_CAPSTONE_REPORT.md)** | **Master Project Report** (All-inclusive) | Complete academic & enterprise capstone report bundling background, theory, code, benchmarks, and logs. |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | **System Architecture & Theory** | 3-DOF vessel dynamics, FT-Attn MARL, CBF safety filters, EKF tracker, Hungarian allocator, Groq LLM arbiter. |
| **[TECH_STACK.md](TECH_STACK.md)** | **Tech Stack & Environment Specs** | Ubuntu 26.04, ROS 2 Lyrical, Conda Python 3.11, PyTorch, XGBoost, PettingZoo, RTX 3060 VRAM budget. |
| **[IMPLEMENTATION_WALKTHROUGH.md](IMPLEMENTATION_WALKTHROUGH.md)** | **Codebase Walkthrough** | File-by-file deep-dive across all modules in `training/` and all 9 ROS 2 packages in `src/`. |
| **[TESTING_AND_BENCHMARKS.md](TESTING_AND_BENCHMARKS.md)** | **Testing & Verification Guide** | Test pipeline execution guide, log file analyses, Section 5 verification rubric, and reproduction steps. |
| **[CAPSTONE_PERFORMANCE_REPORT.md](CAPSTONE_PERFORMANCE_REPORT.md)** | **Empirical Performance Report** | Raw benchmark tables, confusion matrices, and verification status across Stage 1 & Stage 2. |

---

## Empirical Test Logs Directory

The `docs/` directory contains raw, timestamped logs generated directly from automated test executions:

1. **[`unit_test_results.log`](unit_test_results.log)**: Full output of the automated PyTest suite (**6/6 tests passing in 0.36s**).
2. **[`fault_diagnosis_evaluation.log`](fault_diagnosis_evaluation.log)**: Holdout evaluation of the trained XGBoost fault classifier (**98.44% Accuracy, 0.9880 Macro-F1** with complete 4x4 confusion matrix).
3. **[`stage1_benchmark_matrix.log`](stage1_benchmark_matrix.log)**: Complete Stage 1  	imes 3$ fault-sweep benchmark across 9 fault regimes with **0.0% collision rate**.
4. **[`stage2_ros2_live_mission.log`](stage2_ros2_live_mission.log)**: Real-time telemetry trace of live ROS 2 simulation during runtime thruster fault injection and autonomous role reallocation.

---

## Quick-Start Reproduction Guide

All components can be executed independently using dedicated helper scripts in the workspace root:

```bash
# 1. Run unit regression tests
./run_tests.sh

# 2. Run Stage 1 fault-sweep benchmark (3x3 matrix)
./run_stage1.sh

# 3. Launch Stage 2 distributed ROS 2 simulation
./run_stage2_sim.sh
```
