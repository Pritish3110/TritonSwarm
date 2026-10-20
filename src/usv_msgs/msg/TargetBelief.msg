# Fused cooperative target belief
builtin_interfaces/Time stamp
uint8 source_agent_id
geometry_msgs/Point position
geometry_msgs/Vector3 velocity
float32[4] covariance        # 2x2 position covariance [Pxx, Pxy, Pyx, Pyy]
float32 estimation_confidence
bool target_visible
