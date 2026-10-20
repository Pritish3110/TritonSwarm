"""
USV Swarm Simulation - Cooperative EKF Target Tracker & Gossip Fusion
Author: Principal Enterprise Engineer
Stage: 1 & 2 (Perception & State Estimation Engine)

Features:
- Individual Extended Kalman Filter (EKF) tracking 2D target position & velocity.
- Handles range-bearing measurements with non-linear or Cartesian linearized updates.
- Robust to intermittent dropouts and noise.
- Decentralized gossip fusion / Covariance Intersection (CI) across communicating neighbors.
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
import numpy as np


class EKFTracker:
    """EKF for tracking target kinematics [x, y, vx, vy]^T from USV local measurements."""

    def __init__(
        self,
        initial_x: float = 0.0,
        initial_y: float = 0.0,
        dt: float = 0.1,
        process_noise_std: float = 0.5
    ):
        self.dt = dt
        # State: [x, y, vx, vy]
        self.state = np.array([initial_x, initial_y, 0.0, 0.0], dtype=np.float64)

        # State transition matrix F
        self.F = np.array([
            [1.0, 0.0, dt,  0.0],
            [0.0, 1.0, 0.0, dt ],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float64)

        # Process covariance Q (discrete white noise acceleration model)
        q = process_noise_std ** 2
        dt2 = 0.5 * (dt ** 2)
        dt3 = (dt ** 3) / 3.0
        dt4 = (dt ** 4) / 4.0
        self.Q = q * np.array([
            [dt4, 0.0, dt3, 0.0],
            [0.0, dt4, 0.0, dt3],
            [dt3, 0.0, dt2, 0.0],
            [0.0, dt3, 0.0, dt2]
        ], dtype=np.float64)

        # State error covariance P
        self.P = np.diag([25.0, 25.0, 4.0, 4.0]).astype(np.float64)

        # Measurement matrix H for [x, y] observations
        self.H = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0]
        ], dtype=np.float64)

    def predict(self, dt: Optional[float] = None) -> np.ndarray:
        """Propagates state and covariance forward in time."""
        if dt is not None and dt != self.dt:
            self.dt = dt
            self.F[0, 2] = dt
            self.F[1, 3] = dt

        self.state = self.F @ self.state
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.state.copy()

    def update_cartesian(self, z_x: float, z_y: float, r_std: float = 1.0) -> np.ndarray:
        """Corrects state using Cartesian position measurement."""
        z = np.array([z_x, z_y], dtype=np.float64)
        R = np.eye(2, dtype=np.float64) * (r_std ** 2)

        # Innovation
        y = z - (self.H @ self.state)
        S = self.H @ self.P @ self.H.T + R

        # Kalman gain
        K = self.P @ self.H.T @ np.linalg.inv(S)

        # State and covariance update (Joseph form for numerical stability)
        self.state = self.state + K @ y
        I = np.eye(4, dtype=np.float64)
        I_KH = I - K @ self.H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T

        return self.state.copy()

    def update_range_bearing(
        self,
        usv_x: float,
        usv_y: float,
        range_m: float,
        bearing_rad: float,
        range_std: float = 0.8,
        bearing_std: float = 0.05
    ) -> np.ndarray:
        """Converts polar range-bearing measurement to Cartesian with covariance propagation."""
        z_x = usv_x + range_m * math.cos(bearing_rad)
        z_y = usv_y + range_m * math.sin(bearing_rad)

        # Approximate position standard deviation accounting for range and bearing uncertainty
        r_std = math.hypot(range_std, range_m * bearing_std)
        return self.update_cartesian(z_x, z_y, r_std=max(0.5, r_std))

    def fuse_neighbor_estimate(self, neighbor_state: np.ndarray, neighbor_P: np.ndarray, weight: float = 0.5) -> None:
        """Fuses neighbor belief via convex covariance intersection."""
        weight = float(np.clip(weight, 0.05, 0.95))
        try:
            inv_P = np.linalg.inv(self.P)
            inv_nP = np.linalg.inv(neighbor_P)
            fused_inv_P = weight * inv_P + (1.0 - weight) * inv_nP
            fused_P = np.linalg.inv(fused_inv_P)
            fused_state = fused_P @ (weight * inv_P @ self.state + (1.0 - weight) * inv_nP @ neighbor_state)

            self.state = fused_state
            self.P = fused_P
        except np.linalg.LinAlgError:
            # Fallback in case of singular covariance
            self.state = weight * self.state + (1.0 - weight) * neighbor_state
