"""
USV Swarm Simulation - Temporal Sliding Window & XGBoost Fault Diagnosis
Author: Principal Enterprise Engineer
Stage: 1 & 2 (Offline Supervised Model & Online Inference Node)

Features:
- Sliding window buffer (W = 10 steps ~ 1.0s at 10 Hz) for telemetry residuals.
- Extracts statistical features: mean, variance, max, and trend (rate-of-change).
- XGBoost classifier with JSON checkpoint serialization.
- Emits fault class probabilities and continuous health belief h_i(t) in [0.0, 1.0].
"""

from __future__ import annotations
import os
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass
class RawTelemetrySample:
    gps_pos: Tuple[float, float]      # (meas_x, meas_y)
    dr_pos: Tuple[float, float]       # dead-reckoned (dr_x, dr_y)
    cmd_surge: float                  # commanded u_cmd
    meas_surge: float                 # measured u
    cmd_yaw_rate: float               # commanded r_cmd
    meas_yaw_rate: float              # measured r
    detection_missing: bool           # true if perception dropped this tick
    packets_lost: int                 # count of lost packets
    packets_expected: int             # count of expected packets
    battery_level: float              # battery fraction [0, 1]


class TelemetryFeatureExtractor:
    """Computes statistical features over a temporal sliding window for one USV."""

    def __init__(self, window_size: int = 10):
        self.window_size = window_size
        self.buffer: deque[RawTelemetrySample] = deque(maxlen=window_size)

    def add_sample(self, sample: RawTelemetrySample) -> None:
        self.buffer.append(sample)

    def is_ready(self) -> bool:
        return len(self.buffer) >= max(3, self.window_size // 2)

    def extract_features(self) -> np.ndarray:
        """Extracts 14 statistical residual features from the sliding window."""
        if not self.buffer:
            return np.zeros(14, dtype=np.float32)

        # 1. Position residuals ||gps - dr||
        pos_residuals = [
            np.hypot(s.gps_pos[0] - s.dr_pos[0], s.gps_pos[1] - s.dr_pos[1])
            for s in self.buffer
        ]
        mean_pos_res = float(np.mean(pos_residuals))
        var_pos_res = float(np.var(pos_residuals))
        max_pos_res = float(np.max(pos_residuals))
        trend_pos_res = float(pos_residuals[-1] - pos_residuals[0])

        # 2. Surge velocity errors |u_cmd - u_meas|
        surge_errors = [abs(s.cmd_surge - s.meas_surge) for s in self.buffer]
        mean_u_err = float(np.mean(surge_errors))
        var_u_err = float(np.var(surge_errors))
        max_u_err = float(np.max(surge_errors))

        # 3. Yaw rate errors |r_cmd - r_meas|
        yaw_errors = [abs(s.cmd_yaw_rate - s.meas_yaw_rate) for s in self.buffer]
        mean_r_err = float(np.mean(yaw_errors))
        var_r_err = float(np.var(yaw_errors))

        # 4. Perception detection dropout rate
        drop_rate = float(sum(1 for s in self.buffer if s.detection_missing) / len(self.buffer))

        # 5. Packet loss rate
        total_lost = sum(s.packets_lost for s in self.buffer)
        total_exp = sum(s.packets_expected for s in self.buffer)
        packet_loss_rate = float(total_lost / max(1, total_exp))

        # 6. Battery drain trend
        batt_trend = float(self.buffer[0].battery_level - self.buffer[-1].battery_level)
        current_batt = float(self.buffer[-1].battery_level)

        features = np.array([
            mean_pos_res, var_pos_res, max_pos_res, trend_pos_res,
            mean_u_err, var_u_err, max_u_err,
            mean_r_err, var_r_err,
            drop_rate,
            packet_loss_rate,
            batt_trend, current_batt,
            float(self.buffer[-1].meas_surge)
        ], dtype=np.float32)

        return features


class FaultClassifier:
    """XGBoost fault diagnosis model supporting training, JSON export, and fast CPU inference."""

    CLASS_NAMES = ["HEALTHY", "GPS_FAULT", "THRUSTER_FAULT", "LINK_LOSS"]

    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.is_trained = False
        if model_path and os.path.exists(model_path):
            self.load(model_path)

    def train(self, X: np.ndarray, y: np.ndarray) -> Dict[str, float]:
        """Trains the multi-class XGBoost classifier."""
        import xgboost as xgb
        from sklearn.metrics import classification_report, f1_score

        self.model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.08,
            objective="multi:softprob",
            num_class=4,
            eval_metric="mlogloss",
            tree_method="hist",
            random_state=42
        )
        self.model.fit(X, y)
        self.is_trained = True

        y_pred = self.model.predict(X)
        macro_f1 = float(f1_score(y, y_pred, average="macro"))
        return {"macro_f1": macro_f1}

    def predict_probabilities(self, features: np.ndarray) -> np.ndarray:
        """Emits class probabilities [P(HEALTHY), P(GPS), P(THRUSTER), P(LINK)]."""
        if not self.is_trained or self.model is None:
            # Deterministic heuristic fallback if untrained
            mean_pos_res = features[0]
            mean_u_err = features[4]
            pkt_loss = features[10]

            p = np.array([0.9, 0.03, 0.04, 0.03], dtype=np.float32)
            if mean_pos_res > 5.0:
                p = np.array([0.05, 0.85, 0.05, 0.05], dtype=np.float32)
            elif mean_u_err > 0.8:
                p = np.array([0.05, 0.05, 0.85, 0.05], dtype=np.float32)
            elif pkt_loss > 0.25:
                p = np.array([0.05, 0.05, 0.05, 0.85], dtype=np.float32)
            return p

        X = features.reshape(1, -1)
        probs = self.model.predict_proba(X)[0]
        return np.asarray(probs, dtype=np.float32)

    def diagnose(self, features: np.ndarray) -> Tuple[int, float, float]:
        """Returns: (predicted_class, confidence, health_belief_h_i)."""
        probs = self.predict_probabilities(features)
        pred_class = int(np.argmax(probs))
        confidence = float(probs[pred_class])

        p_healthy = float(probs[0])
        p_gps = float(probs[1])
        p_thrust = float(probs[2])
        p_link = float(probs[3])

        # Health belief h_i(t) in [0.0, 1.0]
        # Thruster error scales thrust efficiency; GPS scales perception health
        surge_err = float(features[4]) if len(features) > 4 else 0.0
        thrust_health_factor = max(0.2, 1.0 - min(1.0, surge_err / 2.0))

        h_i = (
            p_healthy * 1.0 +
            p_gps * 0.50 +
            p_thrust * thrust_health_factor +
            p_link * 0.45
        )
        h_i = float(np.clip(h_i, 0.05, 1.0))

        return pred_class, confidence, h_i

    def save(self, file_path: str) -> None:
        """Serializes model to JSON format."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if self.model is not None:
            self.model.save_model(file_path)

    def load(self, file_path: str) -> None:
        """Loads serialized XGBoost model from JSON."""
        import xgboost as xgb
        self.model = xgb.XGBClassifier()
        self.model.load_model(file_path)
        self.is_trained = True
