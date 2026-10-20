# Swarm mission status matching Section 5 evaluation criteria
builtin_interfaces/Time stamp
uint8 total_agents
uint8 healthy_agents
uint8 participating_trackers
float32 angular_spread       # radians
float32 spread_threshold     # 2*pi*(1 - 1/N)
bool criterion_1_pass
float32 max_angular_gap      # radians
float32 gap_threshold        # (2*pi/N)*1.5
bool criterion_2_pass
float32 target_trailing_speed # m/s
float32 target_dwell_time    # seconds
bool arrest_condition_pass
bool debounced_success       # True when criteria 1 & 2 & arrest held >= 5s
float32 debounce_timer       # seconds accumulated
float32 min_agent_distance   # meters (must be >= 4.0m)
bool zero_collision_invariant
