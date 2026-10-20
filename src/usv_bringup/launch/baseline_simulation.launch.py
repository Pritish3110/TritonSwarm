"""
USV Swarm Simulation - Ablated Baseline Comparison Launch File
Author: Principal Enterprise Engineer
Stage: 2 (Architecture Ablation Study)

CONTROL CONDITION for the architecture comparison. Same fleet, same hostiles, same physics,
same win condition as swarm_simulation.launch.py -- but the entire TritonSwarm intelligence
stack is removed and replaced by one conventional swarm controller.

Nodes launched:
1. usv_sim_core            IDENTICAL world: 3-DOF physics, coordinated hostile AI, RViz visuals
2. usv_comparative_eval    baseline_node: static roles, greedy pursuit, raw sensing, no shield
3. usv_fault_injector      IDENTICAL fault schedule, but self_healing:=false (no damage control)
4. usv_comparative_eval    scorecard_node: the SAME impartial scorer the full stack is judged by

Nodes deliberately ABSENT compared with the full architecture:
- usv_perception_mesh      (no EKF bank, no gossip fusion)      -> TRACK_ERROR
- usv_health_diagnosis     (no XGBoost fault diagnosis)         -> UNDETECTED_FAULT
- usv_recovery_planner     (no establishment, no Hungarian)     -> ROLE_VACANCY, FORCE_DOGPILE
- usv_policy_marl          (no FA-MAPPO, no PN, no orbit field) -> TAIL_CHASE, CONTAINMENT_FAIL
- usv_safety_shield        (no control barrier functions; the baseline keeps conventional
                            boids separation, so this compares a formal guarantee against a
                            soft heuristic rather than against nothing at all) -> COLLISION
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    # Identical world parameters to the full-architecture launch, so only the
    # architecture differs between the two runs.
    scenario_arg = DeclareLaunchArgument('scenario', default_value='role_attrition')
    num_agents_arg = DeclareLaunchArgument('num_agents', default_value='12')
    num_targets_arg = DeclareLaunchArgument('num_targets', default_value='3')
    target_mode_arg = DeclareLaunchArgument('target_mode', default_value='reactive')
    hostile_tactic_arg = DeclareLaunchArgument('hostile_tactic', default_value='coordinated')
    spawn_mode_arg = DeclareLaunchArgument('spawn_mode', default_value='launch_corridor')
    launch_bearing_arg = DeclareLaunchArgument('launch_bearing', default_value='2.60')
    launch_standoff_arg = DeclareLaunchArgument('launch_standoff', default_value='85.0')
    noise_arg = DeclareLaunchArgument('sensor_noise_std', default_value='2.5')
    dropout_arg = DeclareLaunchArgument('dropout_rate', default_value='0.25')
    score_period_arg = DeclareLaunchArgument('score_period', default_value='10.0')

    scenario = LaunchConfiguration('scenario')
    num_agents = LaunchConfiguration('num_agents')
    num_targets = LaunchConfiguration('num_targets')
    target_mode = LaunchConfiguration('target_mode')
    hostile_tactic = LaunchConfiguration('hostile_tactic')
    spawn_mode = LaunchConfiguration('spawn_mode')
    launch_bearing = LaunchConfiguration('launch_bearing')
    launch_standoff = LaunchConfiguration('launch_standoff')
    sensor_noise_std = LaunchConfiguration('sensor_noise_std')
    dropout_rate = LaunchConfiguration('dropout_rate')
    score_period = LaunchConfiguration('score_period')

    # 1. Physics Simulator Core (identical to the full architecture)
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
            'stack_label': 'BASELINE - NO ARCHITECTURE (ABLATED CONTROL)',
            'dt': 0.05
        }]
    )

    # 2. Ablated Baseline Swarm Controller (replaces planner + policy + shield + perception)
    baseline_node = Node(
        package='usv_comparative_eval',
        executable='baseline_node',
        name='usv_baseline_swarm',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'num_targets': num_targets,
            'sensor_noise_std': sensor_noise_std,
            'dropout_rate': dropout_rate,
            'sensor_range': 70.0,
            'collision_radius': 3.0,
            'separation_radius': 7.0,
            'separation_gain': 1.8
        }]
    )

    # 3. Fault Injector: identical schedule, with autonomous damage control DISABLED
    injector_node = Node(
        package='usv_fault_injector',
        executable='injector_node',
        name='usv_fault_injector',
        output='screen',
        parameters=[{
            'num_agents': num_agents,
            'scenario': scenario,
            'auto_inject_at_time': 15.0,
            'target_faulty_agent': 1,
            'fault_type': 2,
            'self_healing': False
        }]
    )

    # 4. Impartial Comparative Scorecard (identical node and thresholds in both stacks)
    scorecard_node = Node(
        package='usv_comparative_eval',
        executable='scorecard_node',
        name='usv_scorecard',
        output='screen',
        parameters=[{
            'architecture': 'BASELINE_ABLATED',
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
        noise_arg,
        dropout_arg,
        score_period_arg,
        sim_node,
        baseline_node,
        injector_node,
        scorecard_node
    ])
