#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo " Starting TritonSwarm Stage 2 Battle Simulation (ROS 2)"
echo "=========================================================="

# Activate conda environment
eval "$(/home/pritish_3110/miniconda3/bin/conda shell.bash hook)"
conda activate usv_swarm_env

# Source ROS 2 and workspace
source /opt/ros/lyrical/setup.bash
source "$SCRIPT_DIR/install/setup.bash"

# Usage: ./run_stage2_sim.sh [num_agents] [target_mode] [num_targets] [scenario]
#   scenario: role_attrition (default) | staggered_multi | single
NUM_AGENTS=${1:-12}
TARGET_MODE=${2:-reactive}
NUM_TARGETS=${3:-3}
SCENARIO=${4:-role_attrition}

echo "Launching $NUM_AGENTS ally drones against $NUM_TARGETS hostile drones"
echo "  target_mode=$TARGET_MODE  scenario=$SCENARIO"
ros2 launch usv_bringup swarm_simulation.launch.py \
  num_agents:="$NUM_AGENTS" \
  target_mode:="$TARGET_MODE" \
  num_targets:="$NUM_TARGETS" \
  scenario:="$SCENARIO"
