"""
USV Swarm Simulation - Multi-Node Bringup Launch File
Author: Principal Enterprise Engineer
Stage: 2 (ROS 2 Modular Deployment)

Orchestrates complete closed-loop architecture:
1. usv_sim_core (3-DOF vessel physics + reactive evasive target)
2. usv_perception_mesh (EKF state estimation + ad-hoc gossip network)
3. usv_health_diagnosis (temporal sliding window + frozen XGBoost)
4. usv_recovery_planner (Hungarian role allocator + Groq/Ollama LLM arbiter + mission verifier)
5. usv_policy_marl (frozen FA-MAPPO policy executor)
6. usv_safety_shield (hard CBF collision barrier filter)
7. usv_fault_injector (runtime fault scheduler)
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    scenario_arg = DeclareLaunchArgument('scenario', default_value='staggered_multi')
    num_agents_arg = DeclareLaunchArgument('num_agents', default_value='6')
    target_mode_arg = DeclareLaunchArgument('target_mode', default_value='reactive')
    llm_backend_arg = DeclareLaunchArgument('llm_backend', default_value='auto')
    inject_time_arg = DeclareLaunchArgument('auto_inject_at_time', default_value='15.0')
    faulty_agent_arg = DeclareLaunchArgument('target_faulty_agent', default_value='1')
    fault_type_arg = DeclareLaunchArgument('fault_type', default_value='2')

    scenario = LaunchConfiguration('scenario')
    num_agents = LaunchConfiguration('num_agents')
    target_mode = LaunchConfiguration('target_mode')
    llm_backend = LaunchConfiguration('llm_backend')
    inject_time = LaunchConfiguration('auto_inject_at_time')
    faulty_agent = LaunchConfiguration('target_faulty_agent')
    fault_type = LaunchConfiguration('fault_type')

    # 1. Physics Simulator Core
    sim_node = Node(
        package='usv_sim_core',
        executable='sim_node',
        name='usv_sim_core',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'target_mode': target_mode,
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
            'num_agents': num_agents
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
            'fault_type': fault_type
        }]
    )

    return LaunchDescription([
        scenario_arg,
        num_agents_arg,
        target_mode_arg,
        llm_backend_arg,
        inject_time_arg,
        faulty_agent_arg,
        fault_type_arg,
        sim_node,
        perception_node,
        diagnosis_node,
        planner_node,
        policy_node,
        safety_node,
        injector_node
    ])
