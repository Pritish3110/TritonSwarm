#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Usage: ./run_comparison.sh [duration_seconds] [num_agents] [num_targets] [scenario]
DURATION=${1:-170}
NUM_AGENTS=${2:-12}
NUM_TARGETS=${3:-3}
SCENARIO=${4:-role_attrition}

OUT_DIR="$SCRIPT_DIR/docs/comparison_runs"
mkdir -p "$OUT_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
FULL_LOG="$OUT_DIR/full_architecture_$STAMP.log"
BASE_LOG="$OUT_DIR/baseline_ablated_$STAMP.log"
REPORT="$OUT_DIR/comparison_report_$STAMP.txt"

echo "=========================================================================="
echo " TRITONSWARM ARCHITECTURE ABLATION STUDY"
echo "--------------------------------------------------------------------------"
echo " Duration per condition : ${DURATION}s"
echo " Fleet                  : $NUM_AGENTS drones vs $NUM_TARGETS hostiles"
echo " Fault scenario         : $SCENARIO"
echo " Both conditions share the same physics, hostile AI, fault schedule,"
echo " win condition and impartial scorer. Only the architecture differs."
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

run_condition () {
  local label="$1"; shift
  local launch_file="$1"; shift
  local log="$1"; shift

  echo
  echo "--- CONDITION: $label ---------------------------------------------------"
  echo "    launch : $launch_file"
  echo "    log    : $log"
  # SIGINT lets every node shut down cleanly; a non-zero timeout exit is expected.
  timeout -s INT "$DURATION" ros2 launch usv_bringup "$launch_file" \
      num_agents:="$NUM_AGENTS" \
      num_targets:="$NUM_TARGETS" \
      scenario:="$SCENARIO" > "$log" 2>&1 || true
  echo "    done   : $(grep -c '\[SCORECARD\]' "$log" || true) scorecard samples captured"
}

run_condition "A - FULL TRITONSWARM ARCHITECTURE" swarm_simulation.launch.py "$FULL_LOG"
run_condition "B - ABLATED BASELINE SWARM"        baseline_simulation.launch.py "$BASE_LOG"

echo
echo "=========================================================================="
ros2 run usv_comparative_eval compare_runs "$FULL_LOG" "$BASE_LOG" | tee "$REPORT"

echo
echo "Artifacts written to:"
echo "  $FULL_LOG"
echo "  $BASE_LOG"
echo "  $REPORT"
