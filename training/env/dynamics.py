"""
USV Swarm Simulation - 3-DOF Vessel Dynamics & Target Evasion
Author: Principal Enterprise Engineer
Stage: 1 (Vectorized Environment Dynamics)

Models:
- 3-DOF USV Dynamics (Surge, Sway, Yaw) with hydrodynamics, drag, drift, and thrust saturation.
- Target Vessel with 3 modes: Nominal Straight-Line, Sinusoidal Evasive, and Reactive Evasion.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
import numpy as np


class TargetMode(Enum):
    NOMINAL = "nominal"
    SINUSOIDAL = "sinusoidal"
    REACTIVE = "reactive"


@dataclass
class VesselParams:
    # Mass and inertia properties (approx. WAM-V scale)
    mass: float = 180.0          # kg
    I_z: float = 400.0           # kg*m^2
    X_u_dot: float = -20.0       # Added mass surge (kg)
    Y_v_dot: float = -30.0       # Added mass sway (kg)
    N_r_dot: float = -50.0       # Added mass yaw (kg*m^2)

    # Hydrodynamic drag coefficients
    X_u: float = 70.0            # Linear surge drag (N*s/m)
    X_uu: float = 100.0          # Quadratic surge drag (N*s^2/m^2)
    Y_v: float = 100.0           # Linear sway drag (N*s/m)
    Y_vv: float = 200.0          # Quadratic sway drag (N*s^2/m^2)
    N_r: float = 120.0           # Linear yaw drag (N*m*s/rad)
    N_rr: float = 150.0          # Quadratic yaw drag (N*m*s^2/rad^2)

    # Actuator limits
    max_surge_speed: float = 2.5 # m/s
    max_yaw_rate: float = 0.8    # rad/s
    max_thrust: float = 600.0    # N
    max_torque: float = 400.0    # N*m

    # Low-level PID tracking gains for kinematic setpoints (u_cmd, r_cmd)
    kp_u: float = 400.0
    kd_u: float = 80.0
    kp_r: float = 350.0
    kd_r: float = 70.0


@dataclass
class USVState:
    agent_id: int
    # Inertial frame pose [x, y, psi] (meters, radians)
    x: float = 0.0
    y: float = 0.0
    psi: float = 0.0

    # Body frame velocities [u, v, r] (surge, sway, yaw-rate)
    u: float = 0.0
    v: float = 0.0
    r: float = 0.0

    # Actuator health scaling factor [0.0 = total thruster kill, 1.0 = nominal]
    thrust_efficiency: float = 1.0

    # Commanded kinematic setpoints from policy/safety shield
    u_cmd: float = 0.0
    r_cmd: float = 0.0

    def copy(self) -> USVState:
        return USVState(
            agent_id=self.agent_id,
            x=self.x,
            y=self.y,
            psi=self.psi,
            u=self.u,
            v=self.v,
            r=self.r,
            thrust_efficiency=self.thrust_efficiency,
            u_cmd=self.u_cmd,
            r_cmd=self.r_cmd
        )


class USVDynamics3DOF:
    """Simulates 3-DOF non-linear vessel dynamics using Runge-Kutta 4 (RK4) integration."""

    def __init__(self, params: Optional[VesselParams] = None):
        self.params = params or VesselParams()
        # Effective inertia matrix components
        self.m11 = self.params.mass - self.params.X_u_dot
        self.m22 = self.params.mass - self.params.Y_v_dot
        self.m33 = self.params.I_z - self.params.N_r_dot

    def step(
        self,
        state: USVState,
        u_cmd: float,
        r_cmd: float,
        dt: float,
        drift_x: float = 0.0,
        drift_y: float = 0.0
    ) -> USVState:
        """Integrates the 3-DOF state forward by dt under commanded kinematic setpoints."""
        # Clamp commanded setpoints
        u_cmd = float(np.clip(u_cmd, 0.0, self.params.max_surge_speed))
        r_cmd = float(np.clip(r_cmd, -self.params.max_yaw_rate, self.params.max_yaw_rate))
        state.u_cmd = u_cmd
        state.r_cmd = r_cmd

        # Low-level PID generates body control force/torque
        error_u = u_cmd - state.u
        error_r = r_cmd - state.r

        tau_u = self.params.kp_u * error_u - self.params.kd_u * state.u
        tau_r = self.params.kp_r * error_r - self.params.kd_r * state.r

        # Scale by thrust efficiency (thruster degradation fault)
        tau_u *= state.thrust_efficiency
        tau_r *= state.thrust_efficiency

        # Actuator saturation
        tau_u = float(np.clip(tau_u, -self.params.max_thrust * 0.5, self.params.max_thrust))
        tau_r = float(np.clip(tau_r, -self.params.max_torque, self.params.max_torque))

        # RK4 state integration
        y0 = np.array([state.x, state.y, state.psi, state.u, state.v, state.r], dtype=np.float64)

        def derivatives(y):
            _x, _y, _psi, _u, _v, _r = y
            # Kinematics with ambient environmental drift
            x_dot = _u * math.cos(_psi) - _v * math.sin(_psi) + drift_x
            y_dot = _u * math.sin(_psi) + _v * math.cos(_psi) + drift_y
            psi_dot = _r

            # Coriolis terms
            c13 = -self.m22 * _v
            c23 = self.m11 * _u
            coriolis_u = c13 * _r
            coriolis_v = c23 * _r

            # Hydrodynamic drag forces
            d_u = (self.params.X_u + self.params.X_uu * abs(_u)) * _u
            d_v = (self.params.Y_v + self.params.Y_vv * abs(_v)) * _v
            d_r = (self.params.N_r + self.params.N_rr * abs(_r)) * _r

            # Accelerations
            u_dot = (tau_u - coriolis_u - d_u) / self.m11
            v_dot = (-coriolis_v - d_v) / self.m22
            r_dot = (tau_r - d_r) / self.m33

            return np.array([x_dot, y_dot, psi_dot, u_dot, v_dot, r_dot], dtype=np.float64)

        k1 = derivatives(y0)
        k2 = derivatives(y0 + 0.5 * dt * k1)
        k3 = derivatives(y0 + 0.5 * dt * k2)
        k4 = derivatives(y0 + dt * k3)

        y_next = y0 + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)

        # Normalize heading to [-pi, pi]
        next_psi = (y_next[2] + math.pi) % (2 * math.pi) - math.pi

        state.x = float(y_next[0])
        state.y = float(y_next[1])
        state.psi = float(next_psi)
        state.u = float(np.clip(y_next[3], 0.0, self.params.max_surge_speed * 1.1))
        state.v = float(y_next[4])
        state.r = float(np.clip(y_next[5], -self.params.max_yaw_rate * 1.1, self.params.max_yaw_rate * 1.1))

        return state


@dataclass
class TargetState:
    x: float = 0.0
    y: float = 0.0
    psi: float = 0.0
    speed: float = 1.2
    mode: TargetMode = TargetMode.NOMINAL
    speed_history: List[float] = field(default_factory=list)
    time_elapsed: float = 0.0


class TargetVessel:
    """Simulates the evasive surface target vessel with 3 modes matching the specification."""

    def __init__(
        self,
        initial_x: float = 0.0,
        initial_y: float = 0.0,
        initial_psi: float = 0.0,
        mode: TargetMode = TargetMode.NOMINAL
    ):
        self.state = TargetState(x=initial_x, y=initial_y, psi=initial_psi, mode=mode)
        self.nominal_speed = 1.2    # m/s (48% of USV max speed 2.5 m/s)
        self.sinusoidal_base = 1.5  # m/s
        self.sinusoidal_amp = 4.0   # lateral amp (m)
        self.sinusoidal_period = 15.0 # seconds
        self.max_evasion_speed = 1.5 # m/s cap (60% of USV max)
        self.evasion_trigger_radius = 12.0 # m from any Tracker/Interceptor
        self.max_yaw_rate = 0.4     # rad/s

    def step(
        self,
        dt: float,
        tracker_positions: List[Tuple[float, float]],
        drift_x: float = 0.0,
        drift_y: float = 0.0
    ) -> TargetState:
        self.state.time_elapsed += dt

        # Mode execution
        if self.state.mode == TargetMode.NOMINAL:
            self.state.speed = self.nominal_speed
            target_yaw_rate = 0.0

        elif self.state.mode == TargetMode.SINUSOIDAL:
            self.state.speed = self.sinusoidal_base
            omega = 2.0 * math.pi / self.sinusoidal_period
            target_yaw_rate = (self.sinusoidal_amp * omega) * math.cos(omega * self.state.time_elapsed) * 0.1
            target_yaw_rate = float(np.clip(target_yaw_rate, -self.max_yaw_rate, self.max_yaw_rate))

        elif self.state.mode == TargetMode.REACTIVE:
            # Check proximity to any Tracker/Interceptor (25m containment ring)
            close_positions = []
            for tx, ty in tracker_positions:
                d = math.hypot(tx - self.state.x, ty - self.state.y)
                if d <= 25.0:
                    close_positions.append((tx, ty))

            if len(close_positions) >= 3:
                # Check if centroid of surrounding trackers is centered on target (containment)
                c_x = sum(p[0] for p in close_positions) / len(close_positions)
                c_y = sum(p[1] for p in close_positions) / len(close_positions)
                c_dist = math.hypot(self.state.x - c_x, self.state.y - c_y)
                if c_dist < 6.0:
                    # Encircled from all quadrants: decelerate to non-destructive arrest
                    self.state.speed = max(0.08, self.state.speed - 0.3 * dt)
                    target_yaw_rate = 0.0
                else:
                    # Escape toward open sector
                    self.state.speed = min(self.max_evasion_speed, self.state.speed + 0.3 * dt)
                    escape_angle = math.atan2(self.state.y - c_y, self.state.x - c_x)
                    angle_diff = (escape_angle - self.state.psi + math.pi) % (2 * math.pi) - math.pi
                    target_yaw_rate = float(np.clip(angle_diff * 1.5, -self.max_yaw_rate, self.max_yaw_rate))
            elif close_positions:
                c_x = sum(p[0] for p in close_positions) / len(close_positions)
                c_y = sum(p[1] for p in close_positions) / len(close_positions)
                escape_angle = math.atan2(self.state.y - c_y, self.state.x - c_x)
                angle_diff = (escape_angle - self.state.psi + math.pi) % (2 * math.pi) - math.pi
                target_yaw_rate = float(np.clip(angle_diff * 1.5, -self.max_yaw_rate, self.max_yaw_rate))
                self.state.speed = min(self.max_evasion_speed, self.state.speed + 0.3 * dt)
            else:
                self.state.speed = self.nominal_speed
                target_yaw_rate = 0.0

        # Update pose
        self.state.psi = (self.state.psi + target_yaw_rate * dt + math.pi) % (2 * math.pi) - math.pi
        self.state.x += (self.state.speed * math.cos(self.state.psi) + drift_x) * dt
        self.state.y += (self.state.speed * math.sin(self.state.psi) + drift_y) * dt

        window_len = max(2, int(2.0 / max(1e-4, dt)))
        self.state.speed_history.append(self.state.speed)
        if len(self.state.speed_history) > window_len:
            self.state.speed_history.pop(0)

        return self.state
