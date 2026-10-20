#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Source ROS 2 and workspace
source /opt/ros/lyrical/setup.bash
source "$SCRIPT_DIR/install/setup.bash"

# ==============================================================================
# ANTI-FLICKER RENDERING STABILIZATION FOR WAYLAND + NVIDIA
# ==============================================================================
# 1. Force Qt to render via XWayland GLX
export QT_QPA_PLATFORM=xcb

# 2. Disable VRR, G-Sync, and VSync locks to prevent 4K fullscreen black strobe flicker
export __GL_GSYNC_ALLOWED=0
export __GL_VRR_ALLOWED=0
export __GL_SYNC_TO_VBLANK=0

# 3. Use standard hardware-accelerated Framebuffer Objects (FBO); NEVER use Copy mode (causes crash/freeze on Wayland)
unset OGRE_RTT_MODE

# 4. Standardize screen scaling to 1.0 (prevents Wayland fractional scale buffer churn)
export QT_AUTO_SCREEN_SCALE_FACTOR=0
export QT_SCALE_FACTOR=1
export QT_SCREEN_SCALE_FACTORS=1
export GDK_SCALE=1
export GDK_DPI_SCALE=1

# ==============================================================================
# VIEW SELECTION
# ==============================================================================
# Usage: ./run_rviz.sh [full|baseline] [--fullscreen]
#   full      (default) the full TritonSwarm architecture
#   baseline  the ablated comparison swarm
#
# RViz opens WINDOWED by default. Fullscreen hides the Displays / Views docks and, under
# XWayland, swallows the mouse drags the Orbit camera needs - which is why the camera could
# not be moved before. Pass --fullscreen explicitly if you want it anyway (F11 toggles it
# from inside RViz too).
MODE="full"
FULLSCREEN=""
for arg in "$@"; do
  case "$arg" in
    full|baseline) MODE="$arg" ;;
    --fullscreen|-f) FULLSCREEN="--fullscreen" ;;
    *) echo "Unknown argument: $arg (expected: full | baseline | --fullscreen)"; exit 2 ;;
  esac
done

CONFIG="$SCRIPT_DIR/src/usv_bringup/config/rviz2_swarm.rviz"
if [ "$MODE" = "baseline" ]; then
  CONFIG="$SCRIPT_DIR/src/usv_bringup/config/rviz2_baseline.rviz"
fi

echo "=========================================================="
echo " Launching High-Fidelity 3D Swarm Visualizer (RViz2)"
echo " Scenario config : $MODE"
echo " Render Mode     : XWayland GLX (FBO Acceleration, Flicker-Free)"
echo " Window          : ${FULLSCREEN:-windowed 1760x1000} (F11 toggles fullscreen)"
echo "----------------------------------------------------------"
echo " CAMERA CONTROLS (the 'Move Camera' tool is active by default)"
echo "   Left-drag        : orbit / change viewing angle"
echo "   Scroll wheel     : zoom in / out"
echo "   Middle-drag      : pan the focal point"
echo "   Right-drag       : zoom (drag up/down)"
echo "   Shift + drag     : pan"
echo "   Views panel      : pick a preset (Battle Overview, Tactical Plan View,"
echo "                      Engagement Close-Up, Full Theatre, Free Flight)"
echo "   'f' then click   : focus the camera on whatever you clicked"
echo "=========================================================="

exec rviz2 $FULLSCREEN -d "$CONFIG"
