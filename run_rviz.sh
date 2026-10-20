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

echo "=========================================================="
echo " Launching High-Fidelity 3D Swarm Visualizer (RViz2)"
echo " Render Mode: XWayland GLX (FBO Acceleration, Flicker-Free)"
echo " Display Config: 4K Display Scaled 1:1, Zero-Strobe"
echo "=========================================================="

exec rviz2 --fullscreen -d "$SCRIPT_DIR/src/usv_bringup/config/rviz2_swarm.rviz"
