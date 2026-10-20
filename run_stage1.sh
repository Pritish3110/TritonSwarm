#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo " Starting USV Swarm Stage 1 Pipeline"
echo "=========================================================="

# Activate conda environment
eval "$(/home/pritish_3110/miniconda3/bin/conda shell.bash hook)"
conda activate usv_swarm_env

# 1. Train supervised XGBoost fault diagnosis model
echo -e "\n[1/3] Training XGBoost Fault Diagnosis Model..."
python -m training.scripts.train_diagnosis

# 2. Train FA-MAPPO Policy
echo -e "\n[2/3] Training FA-MAPPO Swarm Policy..."
python -m training.scripts.train_policy

# 3. Evaluate Section 5 Benchmark Matrix
echo -e "\n[3/3] Evaluating 3x3 Fault Sweep Benchmark (Section 5 Rubric)..."
python -m training.scripts.evaluate_stage1

echo -e "\n Stage 1 Pipeline Complete. Checkpoints saved in training/checkpoints/."
