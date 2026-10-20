"""
USV Swarm Simulation - ROS 2 Fault & Scenario Injector Node
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Responsibilities:
- Injects domain-randomized or scheduled faults into the swarm (0-30% nodes, 0-40% links)
- Emulates thruster degradation, GPS bias, perception dropout, and link partitioning
- Supports single-fault, multi-fault staggered, and role-attrition demonstration scenarios
- Publishes ground-truth /usv_{i}/injected_fault for logging and evaluation

The 'role_attrition' scenario exists to prove the no-downtime invariant: instead of naming a
fixed vessel, each event names a ROLE and destroys whichever vessel currently holds it, so the
establishment must backfill that role from the surviving fleet.

Autonomous damage control (self_healing, on by default) closes the loop: a vessel knocked out
of the fight is recovered after a repair delay, its health and thrust ramp back along a smooth
Hermite profile, and it re-enters the tactical establishment to be re-tasked back into the
formation. Every fault in this node is therefore survivable unless self_healing is disabled,
which is what makes the swarm's degradation transient rather than cumulative.
"""

from __future__ import annotations
import math
import random
from typing import Dict, List, Optional, Tuple
import numpy as np

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from usv_msgs.msg import RoleAssignment, USVHealth

# Health and thrust of a vessel knocked out of the fight entirely.
COMBAT_LOSS_HEALTH = 0.0
COMBAT_LOSS_THRUST = 0.0


class USVFaultInjectorNode(Node):
    """ROS 2 Node for controlling and injecting synthetic faults into the fleet."""

    def __init__(self):
        super().__init__('usv_fault_injector_node')

        self.declare_parameter('num_agents', 12)
        self.declare_parameter('scenario', 'staggered_multi') # 'staggered_multi' or 'single'
        self.declare_parameter('auto_inject_at_time', 15.0)   # used in 'single' mode
        self.declare_parameter('target_faulty_agent', 1)      # used in 'single' mode
        self.declare_parameter('fault_type', 2)               # used in 'single' mode
        # Autonomous damage control: recover knocked-out vessels and return them to formation.
        self.declare_parameter('self_healing', True)
        self.declare_parameter('repair_delay', 16.0)      # s adrift before damage control begins
        self.declare_parameter('repair_duration', 12.0)   # s of repair ramp back to nominal

        self.num_agents = self.get_parameter('num_agents').get_parameter_value().integer_value
        self.scenario = self.get_parameter('scenario').get_parameter_value().string_value
        self.inject_time = self.get_parameter('auto_inject_at_time').get_parameter_value().double_value
        self.faulty_agent = self.get_parameter('target_faulty_agent').get_parameter_value().integer_value
        self.fault_type = self.get_parameter('fault_type').get_parameter_value().integer_value
        self.self_healing = self.get_parameter('self_healing').get_parameter_value().bool_value
        self.repair_delay = self.get_parameter('repair_delay').get_parameter_value().double_value
        self.repair_duration = self.get_parameter('repair_duration').get_parameter_value().double_value

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
        elif self.scenario == 'role_attrition':
            # Each event names a role, not a hull. The holder is resolved at trigger time, so
            # the swarm must re-man that role from whoever is left.
            self.schedule = [
                {
                    'time': 22.0, 'duration': 0.0, 'heal_duration': 0.0,
                    'agent': None, 'target_role': 'relay', 'permanent': True,
                    'type': 3, 'desc': 'COMBAT_LOSS_RELAY',
                    'health': COMBAT_LOSS_HEALTH, 'thrust': COMBAT_LOSS_THRUST,
                    'injected': False, 'healing': False, 'restored': False,
                    'current_health': COMBAT_LOSS_HEALTH, 'current_thrust': COMBAT_LOSS_THRUST
                },
                {
                    'time': 40.0, 'duration': 0.0, 'heal_duration': 0.0,
                    'agent': None, 'target_role': 'scout', 'permanent': True,
                    'type': 3, 'desc': 'COMBAT_LOSS_SCOUT',
                    'health': COMBAT_LOSS_HEALTH, 'thrust': COMBAT_LOSS_THRUST,
                    'injected': False, 'healing': False, 'restored': False,
                    'current_health': COMBAT_LOSS_HEALTH, 'current_thrust': COMBAT_LOSS_THRUST
                },
                {
                    'time': 58.0, 'duration': 12.0, 'heal_duration': 8.0,
                    'agent': None, 'target_role': 'interceptor', 'permanent': False,
                    'type': 2, 'desc': 'THRUSTER_DEGRADATION',
                    'health': 0.35, 'thrust': 0.35,
                    'injected': False, 'healing': False, 'restored': False,
                    'current_health': 0.35, 'current_thrust': 0.35
                },
                {
                    'time': 76.0, 'duration': 0.0, 'heal_duration': 0.0,
                    'agent': None, 'target_role': 'tracker', 'permanent': True,
                    'type': 2, 'desc': 'COMBAT_LOSS_TRACKER',
                    'health': COMBAT_LOSS_HEALTH, 'thrust': COMBAT_LOSS_THRUST,
                    'injected': False, 'healing': False, 'restored': False,
                    'current_health': COMBAT_LOSS_HEALTH, 'current_thrust': COMBAT_LOSS_THRUST
                },
            ]
            self.get_logger().info(
                "Initializing USVFaultInjectorNode in 'role_attrition' scenario: "
                "RELAY destroyed @ 22s, SCOUT destroyed @ 40s, INTERCEPTOR crippled @ 58s "
                "(heals @ 70s), TRACKER destroyed @ 76s. Each event targets the CURRENT holder."
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

        # Autonomous damage control pass: a hard kill becomes a RECOVERABLE casualty. The hull
        # still goes dead in the water (health and thrust zero) and still loses its role to the
        # establishment, but after `repair_delay` damage control brings it back and it is
        # re-tasked into the formation. With self_healing disabled the loss stays permanent.
        for item in self.schedule:
            if item.get('permanent'):
                item['combat_loss'] = True
                if self.self_healing:
                    item['permanent'] = False
                    item['duration'] = self.repair_delay
                    item['heal_duration'] = self.repair_duration

        if self.self_healing:
            self.get_logger().info(
                f"Autonomous damage control ENABLED: knocked-out vessels are recovered after "
                f"{self.repair_delay:.1f}s adrift and ramp back to nominal over "
                f"{self.repair_duration:.1f}s, then rejoin the formation."
            )
        else:
            self.get_logger().warning(
                "Autonomous damage control DISABLED: combat losses are permanent and the fleet "
                "degrades cumulatively."
            )

        # Active faults per agent
        self.active_faults: Dict[int, dict] = {}

        # Live role picture, used to resolve role-targeted attrition events.
        self.agent_roles: Dict[int, str] = {}
        self.role_subs: Dict[int, rclpy.subscription.Subscription] = {}
        for i in range(self.num_agents):
            self.role_subs[i] = self.create_subscription(
                RoleAssignment, f'/usv_{i}/role', self._make_role_cb(i), 10
            )

        # Publishers per agent
        self.injected_pubs: Dict[int, rclpy.publisher.Publisher] = {}
        for i in range(self.num_agents):
            self.injected_pubs[i] = self.create_publisher(USVHealth, f'/usv_{i}/injected_fault', 10)

        # 10 Hz injection loop
        self.timer = self.create_timer(0.1, self._injector_step)

    def _make_role_cb(self, agent_id: int):
        def callback(msg: RoleAssignment):
            self.agent_roles[agent_id] = msg.role.lower()
        return callback

    def _resolve_role_victim(self, role: str) -> Optional[int]:
        """
        Picks the vessel currently holding `role` and not already faulted. Returns None when
        nobody holds it yet, so the event waits rather than firing on an arbitrary hull.
        """
        holders = [
            aid for aid, r in sorted(self.agent_roles.items())
            if r == role and aid not in self.active_faults
        ]
        return holders[0] if holders else None

    def _injector_step(self):
        self.sim_time += 0.1
        now = self.get_clock().now().to_msg()

        # Check schedule for triggers, healing transitions, and restoration
        for item in self.schedule:
            aid = item['agent']

            # 1. Fault Trigger
            if not item['injected'] and self.sim_time >= item['time']:
                target_role = item.get('target_role')
                if target_role is not None:
                    aid = self._resolve_role_victim(target_role)
                    if aid is None:
                        # Nobody holds that role yet; retry on the next tick.
                        continue
                    item['agent'] = aid
                item['injected'] = True
                item['current_health'] = float(item['health'])
                item['current_thrust'] = float(item['thrust'])
                self.active_faults[aid] = item
                if item.get('permanent'):
                    self.get_logger().error(
                        f"\n[COMBAT LOSS] >>> t={self.sim_time:4.1f}s: USV-{aid} "
                        f"[{item.get('target_role', '?').upper()}] DESTROYED by {item['desc']}. "
                        f"Dead in the water (health -> 0.00, thrust -> 0.00). "
                        f"Its role slot must now be re-manned by the surviving fleet."
                    )
                else:
                    self.get_logger().warning(
                        f"\n[FAULT INJECTION] >>> Event triggered at t={self.sim_time:4.1f}s: "
                        f"Injecting {item['desc']} (Class {item['type']}) into USV-{aid} "
                        f"(Health -> {item['health']:.2f}, Thrust -> {item['thrust']:.2f})!"
                    )

            # 2. Self-Healing Phase Initiation (a permanent combat loss never heals)
            elif (item['injected'] and not item.get('permanent')
                  and not item['healing']
                  and self.sim_time >= (item['time'] + item['duration'])):
                item['healing'] = True
                if item.get('combat_loss'):
                    self.get_logger().warning(
                        f"\n[DAMAGE CONTROL] >>> t={self.sim_time:4.1f}s: Recovery of USV-{aid} "
                        f"has begun after {item['duration']:.1f}s adrift. Health and thrust ramp "
                        f"back to nominal over {item['heal_duration']:.1f}s; the vessel will "
                        f"re-enter the establishment and be re-tasked into the formation."
                    )
                else:
                    self.get_logger().info(
                        f"\n[FAULT SELF-HEALING] >>> Progressive self-healing initiated for USV-{aid} at t={self.sim_time:4.1f}s: "
                        f"Recovering {item['desc']} over {item['heal_duration']:.1f}s..."
                    )

            # 3. Progressive Healing Interpolation & Full Restoration
            elif item['healing'] and not item['restored'] and not item.get('permanent'):
                t_heal_elapsed = self.sim_time - (item['time'] + item['duration'])
                if t_heal_elapsed >= item['heal_duration']:
                    item['restored'] = True
                    item['current_health'] = 1.0
                    item['current_thrust'] = 1.0
                    if aid in self.active_faults:
                        del self.active_faults[aid]
                    tag = "DAMAGE CONTROL COMPLETE" if item.get('combat_loss') else "FAULT RESTORED HEALTHY"
                    self.get_logger().info(
                        f"\n[{tag}] >>> USV-{aid} fully recovered to nominal operational state "
                        f"(Health -> 1.00, Thrust -> 1.00) at t={self.sim_time:4.1f}s"
                        f"{' and is back in the fight.' if item.get('combat_loss') else '!'}"
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
                if fault.get('permanent'):
                    msg.fault_class = fault['type']
                    msg.health_index = COMBAT_LOSS_HEALTH
                    msg.fault_confidence = 1.0
                    msg.thrust_efficiency = COMBAT_LOSS_THRUST
                    msg.fault_description = fault['desc']
                elif fault['healing']:
                    h_val = float(fault['current_health'])
                    t_val = float(fault['current_thrust'])
                    msg.fault_class = fault['type'] if h_val < 0.80 else 0
                    msg.health_index = h_val
                    msg.fault_confidence = 1.0
                    msg.thrust_efficiency = t_val
                    msg.fault_description = (
                        "DAMAGE_CONTROL_REPAIR" if fault.get('combat_loss')
                        else f"{fault['desc']}_HEALING"
                    )
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
