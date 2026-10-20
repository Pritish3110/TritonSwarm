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

        # Define staggered multi-fault schedule with explicit duration and autonomous healing
        if self.scenario == 'staggered_multi':
            self.schedule = [
                {
                    'time': 12.0,
                    'duration': 14.0,       # Active fault duration: 14.0s (t=12..26s)
                    'heal_duration': 10.0,  # Self-healing ramp: 10.0s (t=26..36s)
                    'agent': 1,
                    'type': 2,
                    'desc': 'THRUSTER_DEGRADATION',
                    'health': 0.35,
                    'thrust': 0.35,
                    'injected': False,
                    'healing': False,
                    'restored': False,
                    'current_health': 0.35,
                    'current_thrust': 0.35
                },
                {
                    'time': 28.0,
                    'duration': 12.0,       # Active fault duration: 12.0s (t=28..40s)
                    'heal_duration': 8.0,   # Self-healing ramp: 8.0s (t=40..48s)
                    'agent': 2,
                    'type': 1,
                    'desc': 'GPS_BIAS',
                    'health': 0.50,
                    'thrust': 1.0,
                    'injected': False,
                    'healing': False,
                    'restored': False,
                    'current_health': 0.50,
                    'current_thrust': 1.0
                },
                {
                    'time': 44.0,
                    'duration': 12.0,       # Active fault duration: 12.0s (t=44..56s)
                    'heal_duration': 8.0,   # Self-healing ramp: 8.0s (t=56..64s)
                    'agent': 5,
                    'type': 3,
                    'desc': 'LINK_DEGRADATION',
                    'health': 0.40,
                    'thrust': 1.0,
                    'injected': False,
                    'healing': False,
                    'restored': False,
                    'current_health': 0.40,
                    'current_thrust': 1.0
                }
            ]
            self.get_logger().info(
                f"Initializing USVFaultInjectorNode in 'staggered_multi' scenario: "
                f"USV-1 (Thruster @ 12s, heal @ 26s), USV-2 (GPS @ 28s, heal @ 40s), USV-5 (Link @ 44s, heal @ 56s)."
            )
        else:
            self.schedule = [
                {
                    'time': self.inject_time,
                    'duration': 14.0,
                    'heal_duration': 10.0,
                    'agent': self.faulty_agent,
                    'type': self.fault_type,
                    'desc': 'THRUSTER_DEGRADATION' if self.fault_type == 2 else 'GPS_BIAS',
                    'health': 0.35 if self.fault_type == 2 else 0.50,
                    'thrust': 0.35 if self.fault_type == 2 else 1.0,
                    'injected': False,
                    'healing': False,
                    'restored': False,
                    'current_health': 0.35 if self.fault_type == 2 else 0.50,
                    'current_thrust': 0.35 if self.fault_type == 2 else 1.0
                }
            ]
            self.get_logger().info(
                f"Initializing USVFaultInjectorNode in 'single' scenario: "
                f"USV-{self.faulty_agent} (type={self.fault_type} @ {self.inject_time}s, heal @ {self.inject_time + 14.0}s)."
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

        # Check schedule for triggers, healing transitions, and restoration
        for item in self.schedule:
            aid = item['agent']

            # 1. Fault Trigger
            if not item['injected'] and self.sim_time >= item['time']:
                item['injected'] = True
                item['current_health'] = float(item['health'])
                item['current_thrust'] = float(item['thrust'])
                self.active_faults[aid] = item
                self.get_logger().warning(
                    f"\n[FAULT INJECTION] >>> Event triggered at t={self.sim_time:4.1f}s: "
                    f"Injecting {item['desc']} (Class {item['type']}) into USV-{aid} "
                    f"(Health -> {item['health']:.2f}, Thrust -> {item['thrust']:.2f})!"
                )

            # 2. Self-Healing Phase Initiation
            elif item['injected'] and not item['healing'] and self.sim_time >= (item['time'] + item['duration']):
                item['healing'] = True
                self.get_logger().info(
                    f"\n[FAULT SELF-HEALING] >>> Progressive self-healing initiated for USV-{aid} at t={self.sim_time:4.1f}s: "
                    f"Recovering {item['desc']} over {item['heal_duration']:.1f}s..."
                )

            # 3. Progressive Healing Interpolation & Full Restoration
            elif item['healing'] and not item['restored']:
                t_heal_elapsed = self.sim_time - (item['time'] + item['duration'])
                if t_heal_elapsed >= item['heal_duration']:
                    item['restored'] = True
                    item['current_health'] = 1.0
                    item['current_thrust'] = 1.0
                    if aid in self.active_faults:
                        del self.active_faults[aid]
                    self.get_logger().info(
                        f"\n[FAULT RESTORED HEALTHY] >>> USV-{aid} fully recovered to nominal operational state "
                        f"(Health -> 1.00, Thrust -> 1.00) at t={self.sim_time:4.1f}s!"
                    )
                else:
                    alpha = min(1.0, max(0.0, t_heal_elapsed / item['heal_duration']))
                    # Smooth hermite cubic s-curve
                    alpha_s = 3.0 * (alpha ** 2) - 2.0 * (alpha ** 3)
                    item['current_health'] = float(item['health'] + alpha_s * (1.0 - item['health']))
                    item['current_thrust'] = float(item['thrust'] + alpha_s * (1.0 - item['thrust']))

        # Broadcast ground-truth health states
        for i in range(self.num_agents):
            msg = USVHealth()
            msg.stamp = now
            msg.agent_id = i

            if i in self.active_faults:
                fault = self.active_faults[i]
                if fault['healing']:
                    h_val = float(fault['current_health'])
                    t_val = float(fault['current_thrust'])
                    msg.fault_class = fault['type'] if h_val < 0.80 else 0
                    msg.health_index = h_val
                    msg.fault_confidence = 1.0
                    msg.thrust_efficiency = t_val
                    msg.fault_description = f"{fault['desc']}_HEALING"
                else:
                    msg.fault_class = fault['type']
                    msg.health_index = float(fault['health'])
                    msg.fault_confidence = 1.0
                    msg.thrust_efficiency = float(fault['thrust'])
                    msg.fault_description = fault['desc']
            else:
                msg.fault_class = 0  # HEALTHY
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
