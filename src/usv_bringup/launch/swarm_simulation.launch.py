"""
USV Swarm Simulation - Multi-Node Bringup Launch File
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Orchestrates complete closed-loop architecture:
1. usv_sim_core (3-DOF vessel physics + coordinated multi-hostile evasion AI)
2. usv_perception_mesh (EKF state estimation + ad-hoc gossip network)
3. usv_health_diagnosis (temporal sliding window + frozen XGBoost)
4. usv_recovery_planner (establishment-driven multi-wing Hungarian allocator + Groq/Ollama
   LLM arbiter + per-hostile mission verifier)
5. usv_policy_marl (frozen FA-MAPPO policy executor)
6. usv_safety_shield (hard CBF collision barrier filter)
7. usv_fault_injector (runtime fault scheduler + autonomous damage control)
8. usv_comparative_eval scorecard_node (impartial comparative scorer, identical to the one
   launched alongside the ablated baseline in baseline_simulation.launch.py)
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    # 'role_attrition' destroys whichever vessel currently holds each role, demonstrating the
    # no-downtime invariant. 'staggered_multi' (degrade-and-heal) and 'single' remain available.
    scenario_arg = DeclareLaunchArgument('scenario', default_value='role_attrition')
    num_agents_arg = DeclareLaunchArgument('num_agents', default_value='12')
    num_targets_arg = DeclareLaunchArgument('num_targets', default_value='3')
    target_mode_arg = DeclareLaunchArgument('target_mode', default_value='reactive')
    hostile_tactic_arg = DeclareLaunchArgument('hostile_tactic', default_value='coordinated')
    spawn_mode_arg = DeclareLaunchArgument('spawn_mode', default_value='launch_corridor')
    launch_bearing_arg = DeclareLaunchArgument('launch_bearing', default_value='2.60')
    launch_standoff_arg = DeclareLaunchArgument('launch_standoff', default_value='85.0')
    llm_backend_arg = DeclareLaunchArgument('llm_backend', default_value='auto')
    inject_time_arg = DeclareLaunchArgument('auto_inject_at_time', default_value='15.0')
    faulty_agent_arg = DeclareLaunchArgument('target_faulty_agent', default_value='1')
    fault_type_arg = DeclareLaunchArgument('fault_type', default_value='2')
    self_healing_arg = DeclareLaunchArgument('self_healing', default_value='true')
    score_period_arg = DeclareLaunchArgument('score_period', default_value='10.0')
    repair_delay_arg = DeclareLaunchArgument('repair_delay', default_value='16.0')
    repair_duration_arg = DeclareLaunchArgument('repair_duration', default_value='12.0')

    scenario = LaunchConfiguration('scenario')
    num_agents = LaunchConfiguration('num_agents')
    num_targets = LaunchConfiguration('num_targets')
    target_mode = LaunchConfiguration('target_mode')
    hostile_tactic = LaunchConfiguration('hostile_tactic')
    spawn_mode = LaunchConfiguration('spawn_mode')
    launch_bearing = LaunchConfiguration('launch_bearing')
    launch_standoff = LaunchConfiguration('launch_standoff')
    llm_backend = LaunchConfiguration('llm_backend')
    inject_time = LaunchConfiguration('auto_inject_at_time')
    faulty_agent = LaunchConfiguration('target_faulty_agent')
    fault_type = LaunchConfiguration('fault_type')
    self_healing = LaunchConfiguration('self_healing')
    score_period = LaunchConfiguration('score_period')
    repair_delay = LaunchConfiguration('repair_delay')
    repair_duration = LaunchConfiguration('repair_duration')

    # 1. Physics Simulator Core
    sim_node = Node(
        package='usv_sim_core',
        executable='sim_node',
        name='usv_sim_core',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'num_targets': num_targets,
            'target_mode': target_mode,
            'hostile_tactic': hostile_tactic,
            'spawn_mode': spawn_mode,
            'launch_bearing': launch_bearing,
            'launch_standoff': launch_standoff,
            'stack_label': 'FULL TRITONSWARM ARCHITECTURE',
            'dt': 0.05
        }]
    )

    # 2. Perception & Gossip Mesh
    perception_node = Node(
        package='usv_perception_mesh',
        executable='perception_node',
        name='usv_perception_mesh',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'num_targets': num_targets,
            'comm_range': 80.0,
            'packet_loss_rate': 0.05
        }]
    )

    # 3. Health Diagnosis Node
    diagnosis_node = Node(
        package='usv_health_diagnosis',
        executable='diagnosis_node',
        name='usv_health_diagnosis',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'window_size': 10
        }]
    )

    # 4. Recovery Planner & Mission Verifier Node
    planner_node = Node(
        package='usv_recovery_planner',
        executable='planner_node',
        name='usv_recovery_planner',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'num_targets': num_targets,
            'r_ring': 20.0,
            'delta_r': 3.0,
            'llm_backend': llm_backend
        }]
    )

    # 5. MARL Policy Executor Node
    policy_node = Node(
        package='usv_policy_marl',
        executable='policy_executor_node',
        name='usv_policy_marl',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'num_targets': num_targets
        }]
    )

    # 6. CBF Hard Safety Shield Node
    safety_node = Node(
        package='usv_safety_shield',
        executable='safety_node',
        name='usv_safety_shield',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'num_targets': num_targets,
            'd_safe': 4.0,
            'd_warning': 8.0
        }]
    )

    # 7. Fault Injector Node
    injector_node = Node(
        package='usv_fault_injector',
        executable='injector_node',
        name='usv_fault_injector',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'scenario': scenario,
            'auto_inject_at_time': inject_time,
            'target_faulty_agent': faulty_agent,
            'fault_type': fault_type,
            'self_healing': self_healing,
            'repair_delay': repair_delay,
            'repair_duration': repair_duration
        }]
    )

    # 8. Impartial Comparative Scorecard (identical node and thresholds in both stacks)
    scorecard_node = Node(
        package='usv_comparative_eval',
        executable='scorecard_node',
        name='usv_scorecard',
        output='screen',
        parameters=[{
            'architecture': 'FULL_ARCHITECTURE',
            'num_agents': num_agents,
            'num_targets': num_targets,
            'score_period': score_period,
            'd_safe': 4.0
        }]
    )

    return LaunchDescription([
        scenario_arg,
        num_agents_arg,
        num_targets_arg,
        target_mode_arg,
        hostile_tactic_arg,
        spawn_mode_arg,
        launch_bearing_arg,
        launch_standoff_arg,
        llm_backend_arg,
        inject_time_arg,
        faulty_agent_arg,
        fault_type_arg,
        self_healing_arg,
        repair_delay_arg,
        repair_duration_arg,
        score_period_arg,
        sim_node,
        perception_node,
        diagnosis_node,
        planner_node,
        policy_node,
        safety_node,
        injector_node,
        scorecard_node
    ])
