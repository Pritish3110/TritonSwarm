"""
USV Swarm Simulation - Offline Fault Diagnosis Training Pipeline
Author: Principal Enterprise Engineer
Stage: 1 (Supervised XGBoost Training)

Generates synthetic flight/voyage data across diverse fault scenarios,
extracts sliding-window residual features, trains the XGBoost classifier,
and serializes the frozen model to checkpoints/xgb_diagnosis.json.
"""

from __future__ import annotations
import math
import os
import random
import numpy as np
from sklearn.metrics import classification_report, f1_score

from training.env.dynamics import USVDynamics3DOF, USVState, TargetVessel, TargetMode, VesselParams
from training.env.fault_injector import FaultInjector, FaultType
from training.models.fault_classifier import FaultClassifier, RawTelemetrySample, TelemetryFeatureExtractor


def generate_diagnosis_dataset(num_episodes: int = 40, episode_len: int = 250, num_agents: int = 6):
    """Simulates USVs under injected faults and extracts labeled sliding-window features."""
    print(f"Generating telemetry dataset: {num_episodes} episodes x {episode_len} steps x {num_agents} USVs...")

    dynamics = USVDynamics3DOF(VesselParams())
    dt = 0.1

    X_list = []
    y_list = []

    for ep in range(num_episodes):
        fault_injector = FaultInjector(num_agents=num_agents, node_fault_ratio=0.35, max_link_loss_rate=0.45, seed=ep)
        target = TargetVessel(initial_x=0.0, initial_y=0.0, mode=TargetMode.SINUSOIDAL)

        extractors = {i: TelemetryFeatureExtractor(window_size=10) for i in range(num_agents)}
        states = {}
        dr_positions = {}

        # Init agents
        for i in range(num_agents):
            angle = (2.0 * math.pi / num_agents) * i
            dist = 35.0
            states[i] = USVState(
                agent_id=i,
                x=dist * math.cos(angle),
                y=dist * math.sin(angle),
                psi=angle + math.pi
            )
            dr_positions[i] = [states[i].x, states[i].y]

        current_time = 0.0

        for step in range(episode_len):
            current_time += dt
            fault_injector.update(current_time, dt)

            # Target step
            target_pos = [(s.x, s.y) for s in states.values()]
            t_state = target.step(dt, target_pos)

            for i in range(num_agents):
                state = states[i]
                prof = fault_injector.profiles[i]
                state.thrust_efficiency = prof.thrust_efficiency

                # Simple proportional guidance toward target (20m standoff)
                dx = t_state.x - state.x
                dy = t_state.y - state.y
                dist = math.hypot(dx, dy)
                desired_heading = math.atan2(dy, dx)
                heading_err = (desired_heading - state.psi + math.pi) % (2 * math.pi) - math.pi

                u_cmd = float(np.clip(1.5 * (dist - 20.0) / 10.0 + 1.2, 0.2, 2.5))
                r_cmd = float(np.clip(heading_err * 1.5, -0.8, 0.8))

                # Advance vessel physics
                dynamics.step(state, u_cmd=u_cmd, r_cmd=r_cmd, dt=dt)

                # Dead-reckoning update based on commanded inputs & compass
                dr_positions[i][0] += u_cmd * math.cos(state.psi) * dt
                dr_positions[i][1] += u_cmd * math.sin(state.psi) * dt

                # Sensor readings
                meas_x, meas_y = fault_injector.corrupt_gps(i, state.x, state.y)
                meas_r = fault_injector.corrupt_imu(i, state.r)
                det_dropped = fault_injector.is_detection_dropped(i)

                # Network packet exchange check
                lost_packets = 0
                expected_packets = num_agents - 1
                for j in range(num_agents):
                    if i != j:
                        d_ij = math.hypot(state.x - states[j].x, state.y - states[j].y)
                        if not fault_injector.can_communicate(i, j, d_ij):
                            lost_packets += 1

                # Telemetry sample
                sample = RawTelemetrySample(
                    gps_pos=(meas_x, meas_y),
                    dr_pos=(dr_positions[i][0], dr_positions[i][1]),
                    cmd_surge=u_cmd,
                    meas_surge=state.u,
                    cmd_yaw_rate=r_cmd,
                    meas_yaw_rate=meas_r,
                    detection_missing=det_dropped,
                    packets_lost=lost_packets,
                    packets_expected=expected_packets,
                    battery_level=prof.battery_level
                )

                extractor = extractors[i]
                extractor.add_sample(sample)

                # Record feature vector and label once sliding buffer is primed
                if extractor.is_ready():
                    feat = extractor.extract_features()
                    if prof.fault_start_time > 0 and current_time < prof.fault_start_time:
                        gt_fault = int(FaultType.HEALTHY)
                    else:
                        gt_enum, _ = fault_injector.get_ground_truth_health(i)
                        gt_fault = int(gt_enum)
                    X_list.append(feat)
                    y_list.append(gt_fault)

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int32)

    # Balance classes so under-represented fault modes have sufficient signal
    unique_classes, counts = np.unique(y, return_counts=True)
    min_count = min(counts)
    # Cap healthy to at most 2.5x the minimum minority class to balance Macro-F1
    balanced_indices = []
    for cls in unique_classes:
        cls_indices = np.where(y == cls)[0]
        max_samples = min(len(cls_indices), max(min_count * 2, 2000))
        selected = np.random.choice(cls_indices, size=max_samples, replace=False)
        balanced_indices.extend(selected)

    np.random.shuffle(balanced_indices)
    X = X[balanced_indices]
    y = y[balanced_indices]
    print(f"Dataset ready. Balanced Samples: {X.shape[0]}, Features: {X.shape[1]}")
    for cls, count in zip(*np.unique(y, return_counts=True)):
        print(f"  Class {FaultClassifier.CLASS_NAMES[cls]}: {count} samples")
    return X, y


def main():
    X, y = generate_diagnosis_dataset(num_episodes=50, episode_len=250, num_agents=6)

    # Train-test split (80-20)
    split_idx = int(0.80 * len(X))
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    classifier = FaultClassifier()
    print("\nTraining XGBoost Fault Classifier...")
    metrics = classifier.train(X_train, y_train)

    # Evaluate on holdout test set
    import xgboost as xgb
    y_pred = classifier.model.predict(X_test)
    test_f1 = f1_score(y_test, y_pred, average="macro")
    print(f"\n--- Classification Evaluation ---")
    print(f"Train Macro-F1: {metrics['macro_f1']:.4f}")
    print(f"Test  Macro-F1: {test_f1:.4f}")
    print("\nDetailed Test Classification Report:")
    print(classification_report(y_test, y_pred, target_names=FaultClassifier.CLASS_NAMES))

    # Save frozen checkpoint
    checkpoint_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, "xgb_diagnosis.json")
    classifier.save(checkpoint_path)
    print(f"Saved frozen fault diagnosis model to: {checkpoint_path}")


if __name__ == "__main__":
    main()
