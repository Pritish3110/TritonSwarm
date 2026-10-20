# Dynamic role assignment per USV
builtin_interfaces/Time stamp
uint8 agent_id
string role                  # "tracker", "interceptor", "relay", "scout"
geometry_msgs/Point target_slot
float32 allocation_cost
bool is_degraded_reallocation
uint8 assigned_target_id     # tactical wing affiliation: index of the hostile this vessel is committed to
