#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================================="
echo " Starting ABLATED BASELINE Swarm Simulation (control condition)"
echo "--------------------------------------------------------------------------"
echo " Same fleet, same hostiles, same physics, same win condition as the full"
echo " architecture - but with the TritonSwarm intelligence stack REMOVED:"
echo "   NO establishment allocator   -> roles die with the drone that held them"
echo "   NO wing partitioning         -> the fleet dogpiles one hostile"
echo "   NO PN / orbit guidance       -> tail-chasing, no encirclement"
echo "   NO CBF safety shield         -> hull strikes are mission kills"
echo "   NO EKF / gossip mesh         -> raw noisy sensing, stale tracks"
echo "   NO fault diagnosis           -> crippled drones keep flying their task"
echo "   NO damage control            -> every loss is permanent"
echo "=========================================================================="

# Activate conda environment
eval "$(/home/pritish_3110/miniconda3/bin/conda shell.bash hook)"
conda activate usv_swarm_env

# Source ROS 2 and workspace
source /opt/ros/lyrical/setup.bash
source "$SCRIPT_DIR/install/setup.bash"

# ------------------------------------------------------------------------------
# PRE-FLIGHT: refuse to start if simulation nodes are already running.
# A surviving sim_core from an earlier run publishes onto the same topics as this
# one, so two simulators fight over /target_{m}/odom and the run is silently
# corrupted -- hostiles appear frozen at another run's final positions.
# ------------------------------------------------------------------------------
STALE="$(pgrep -f "install/usv_[a-z_]*/lib/usv_" || true)"
if [ -n "$STALE" ]; then
  echo "ERROR: simulation nodes from a previous run are still alive:" >&2
  pgrep -af "install/usv_[a-z_]*/lib/usv_" >&2
  echo >&2
  echo "Clear them first, then re-run:" >&2
  echo "  pkill -f 'install/usv_[a-z_]*/lib/usv_'" >&2
  exit 1
fi

# Usage: ./run_baseline_sim.sh [num_agents] [target_mode] [num_targets] [scenario]
NUM_AGENTS=${1:-12}
TARGET_MODE=${2:-reactive}
NUM_TARGETS=${3:-3}
SCENARIO=${4:-role_attrition}

echo "Launching $NUM_AGENTS baseline drones against $NUM_TARGETS hostile drones"
echo "  target_mode=$TARGET_MODE  scenario=$SCENARIO"
echo "Visualize with:  ./run_rviz.sh baseline"
ros2 launch usv_bringup baseline_simulation.launch.py \
  num_agents:="$NUM_AGENTS" \
  target_mode:="$TARGET_MODE" \
  num_targets:="$NUM_TARGETS" \
  scenario:="$SCENARIO"
