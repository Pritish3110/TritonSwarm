#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo " Starting USV Swarm Stage 2 Simulation (ROS 2)"
echo "=========================================================="

# Activate conda environment
eval "$(/home/pritish_3110/miniconda3/bin/conda shell.bash hook)"
conda activate usv_swarm_env

# Source ROS 2 and workspace
source /opt/ros/lyrical/setup.bash
source "$SCRIPT_DIR/install/setup.bash"

NUM_AGENTS=${1:-6}
TARGET_MODE=${2:-reactive}

echo "Launching swarm with $NUM_AGENTS USVs and target_mode=$TARGET_MODE..."
ros2 launch usv_bringup swarm_simulation.launch.py num_agents:="$NUM_AGENTS" target_mode:="$TARGET_MODE"
