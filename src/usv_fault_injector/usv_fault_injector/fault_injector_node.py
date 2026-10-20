"""
USV Swarm Simulation - ROS 2 Fault & Scenario Injector Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Responsibilities:
- Injects domain-randomized or scheduled faults into the swarm (0-30% nodes, 0-40% links)
- Emulates thruster degradation, GPS bias, perception dropout, and link partitioning
- Supports single-fault and multi-fault staggered demonstration scenarios
- Publishes ground-truth /usv_{i}/injected_fault for logging and evaluation
"""

from __future__ import annotations
import math
import random
from typing import Dict, List, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from usv_msgs.msg import USVHealth


class USVFaultInjectorNode(Node):
    """ROS 2 Node for controlling and injecting synthetic faults into the fleet."""

    def __init__(self):
        super().__init__('usv_fault_injector_node')

        self.declare_parameter('num_agents', 6)
        self.declare_parameter('scenario', 'staggered_multi') # 'staggered_multi' or 'single'
        self.declare_parameter('auto_inject_at_time', 15.0)   # used in 'single' mode
        self.declare_parameter('target_faulty_agent', 1)      # used in 'single' mode
        self.declare_parameter('fault_type', 2)               # used in 'single' mode

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.scenario = self.get_parameter('scenario').get_parameter_value().string_value
        self.inject_time = self.get_parameter('auto_inject_at_time').get_parameter_value().double_value
        self.faulty_agent = self.get_parameter('target_faulty_agent').get_parameter_value().integer_value
        self.fault_type = self.get_parameter('fault_type').get_parameter_value().integer_value

        self.sim_time = 0.0

        # Define staggered multi-fault schedule (3 distinct fault categories)
        if self.scenario == 'staggered_multi':
            self.schedule = [
                {
                    'time': 12.0,
                    'agent': 1,
                    'type': 2,
                    'desc': 'THRUSTER_DEGRADATION',
                    'health': 0.35,
                    'thrust': 0.35,
                    'injected': False
                },
                {
                    'time': 24.0,
                    'agent': 2,
                    'type': 1,
                    'desc': 'GPS_BIAS',
                    'health': 0.50,
                    'thrust': 1.0,
                    'injected': False
                },
                {
                    'time': 36.0,
                    'agent': 5,
                    'type': 3,
                    'desc': 'LINK_DEGRADATION',
                    'health': 0.40,
                    'thrust': 1.0,
                    'injected': False
                }
            ]
            self.get_logger().info(
                f"Initializing USVFaultInjectorNode in 'staggered_multi' scenario: "
                f"USV-1 (Thruster @ 12s), USV-2 (GPS @ 24s), USV-5 (Link @ 36s)."
            )
        else:
            self.schedule = [
                {
                    'time': self.inject_time,
                    'agent': self.faulty_agent,
                    'type': self.fault_type,
                    'desc': 'THRUSTER_DEGRADATION' if self.fault_type == 2 else 'GPS_BIAS',
                    'health': 0.35 if self.fault_type == 2 else 0.50,
                    'thrust': 0.35 if self.fault_type == 2 else 1.0,
                    'injected': False
                }
            ]
            self.get_logger().info(
                f"Initializing USVFaultInjectorNode in 'single' scenario: "
                f"USV-{self.faulty_agent} (type={self.fault_type} @ {self.inject_time}s)."
            )

        # Active faults per agent
        self.active_faults: Dict[int, dict] = {}

        # Publishers per agent
        self.injected_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        for i in range(self.num_agents):
            self.injected_pubs[i] = self.create_publisher(USVHealth, f'/usv_{i}/injected_fault', 10)

        # 10 Hz injection loop
        self.timer = self.create_timer(0.1, self._injector_step)

    def _injector_step(self):
        self.sim_time += 0.1
        now = self.get_clock().now().to_msg()

        # Check schedule for triggers
        for item in self.schedule:
            if not item['injected'] and self.sim_time >= item['time']:
                item['injected'] = True
                aid = item['agent']
                self.active_faults[aid] = item
                self.get_logger().warning(
                    f"\n[FAULT INJECTION] >>> Event triggered at t={self.sim_time:4.1f}s: "
                    f"Injecting {item['desc']} (Class {item['type']}) into USV-{aid} (Health -> {item['health']:.2f})!"
                )

        # Broadcast ground-truth health states
        for i in range(self.num_agents):
            msg = USVHealth()
            msg.stamp = now
            msg.agent_id = i

            if i in self.active_faults:
                fault = self.active_faults[i]
                msg.fault_class = fault['type']
                msg.health_index = float(fault['health'])
                msg.fault_confidence = 1.0
                msg.thrust_efficiency = float(fault['thrust'])
                msg.fault_description = fault['desc']
            else:
                msg.fault_class = 0 # HEALTHY
                msg.health_index = 1.0
                msg.fault_confidence = 1.0
                msg.thrust_efficiency = 1.0
                msg.fault_description = "HEALTHY"

            self.injected_pubs[i].publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = USVFaultInjectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
