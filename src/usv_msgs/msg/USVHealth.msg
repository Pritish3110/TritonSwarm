# Telemetry health state for one USV
builtin_interfaces/Time stamp
uint8 agent_id
float32 health_index         # h_i(t) in [0.0, 1.0]
uint8 fault_class            # 0: HEALTHY, 1: GPS_FAULT, 2: THRUSTER_FAULT, 3: LINK_LOSS
float32 fault_confidence     # [0.0, 1.0]
string fault_description
float32 thrust_efficiency    # [0.0, 1.0]
float32 battery_level        # [0.0, 1.0]
float32 packet_loss_rate     # [0.0, 1.0]
