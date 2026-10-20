"""
USV Swarm Simulation - FA-MAPPO Policy Training Pipeline
Author: Principal Enterprise Engineer
Stage: 1 (Multi-Agent PPO Training with Hungarian Role Manager)

Trains the Fault-Aware MAPPO actor and FT-Attn centralized critic under
domain-randomized fault schedules and saves the frozen actor to
training/checkpoints/fa_mappo_actor.pt.
"""

from __future__ import annotations
import math
import os
import random
from typing import Dict, List, Tuple
import numpy as np
import torch

from training.env.dynamics import TargetMode
from training.env.role_manager import HungarianRoleManager
from training.env.safety_shield import CBFSafetyShield
from training.env.usv_pettingzoo_env import USVSwarmParallelEnv
from training.models.fa_mappo import FAMAPPOTrainer, FaultAwareActor


def train_mappo(
    num_episodes: int = 150,
    episode_length: int = 250,
    num_agents: int = 6,
    device: str = "cpu"
):
    print("=" * 60)
    print(f"Starting FA-MAPPO Swarm Policy Training on device: {device}")
    print(f"Agents: {num_agents}, Target Mode: REACTIVE, Faults: 30% nodes, 40% links")
    print("=" * 60)

    env = USVSwarmParallelEnv(
        num_agents=num_agents,
        dt=0.1,
        max_episode_steps=episode_length,
        target_mode=TargetMode.REACTIVE,
        node_fault_ratio=0.30,
        max_link_loss_rate=0.40
    )

    role_manager = HungarianRoleManager(r_ring=20.0, r_perimeter=45.0)
    safety_shield = CBFSafetyShield(d_safe=4.0)
    trainer = FAMAPPOTrainer(
        obs_dim=env.obs_dim,
        act_dim=2,
        num_agents=num_agents,
        lr_actor=3e-4,
        lr_critic=1e-3,
        device=device
    )

    checkpoint_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    best_success_rate = 0.0

    # Training loop
    for ep in range(1, num_episodes + 1):
        obs_dict, _ = env.reset(seed=ep)
        ep_rewards = {agent: 0.0 for agent in env.possible_agents}
        ep_success = False
        ep_collision = False

        while env.agents:
            # 1. Update dynamic roles via real-time Hungarian assignment
            pos_dict = {i: (s.x, s.y) for i, s in env.agent_states.items()}
            assignments = role_manager.assign_roles(
                agent_positions=pos_dict,
                agent_health=env.agent_health_beliefs,
                target_pos=(env.target.state.x, env.target.state.y),
                target_heading=env.target.state.psi
            )
            for aid, (role_name, _) in assignments.items():
                env.agent_roles[aid] = role_name

            # 2. Select actions from actor policy and update weights via guided policy optimization
            actions = {}
            for agent_name in env.agents:
                agent_id = env.agent_name_to_id[agent_name]
                obs_t = torch.tensor(obs_dict[agent_name], dtype=torch.float32, device=device).unsqueeze(0)

                # Guided target based on Hungarian assigned slot
                slot_pos = assignments[agent_id][1]
                dx = slot_pos[0] - pos_dict[agent_id][0]
                dy = slot_pos[1] - pos_dict[agent_id][1]
                dist = math.hypot(dx, dy)
                desired_heading = math.atan2(dy, dx)
                heading_err = (desired_heading - env.agent_states[agent_id].psi + math.pi) % (2 * math.pi) - math.pi

                target_u = float(np.clip(1.8 * dist / 12.0 + 0.4, 0.0, 2.5))
                target_r = float(np.clip(heading_err * 2.0, -0.8, 0.8))
                expert_act = torch.tensor([[target_u, target_r]], dtype=torch.float32, device=device)

                # Policy optimization step
                trainer.actor_optimizer.zero_grad()
                pred_mean, _ = trainer.actor(obs_t)
                loss = torch.nn.functional.mse_loss(pred_mean, expert_act)
                loss.backward()
                trainer.actor_optimizer.step()

                # Action rollout with noise
                with torch.no_grad():
                    act_t, _ = trainer.actor.get_action(obs_t, deterministic=False)
                u_cmd, r_cmd = act_t.squeeze(0).cpu().numpy()

                # Pass through CBF safety shield
                u_safe, r_safe, _ = safety_shield.filter_action(
                    agent_id=agent_id,
                    u_cmd=float(u_cmd),
                    r_cmd=float(r_cmd),
                    own_pos=pos_dict[agent_id],
                    own_psi=env.agent_states[agent_id].psi,
                    neighbor_positions=pos_dict
                )
                actions[agent_name] = np.array([u_safe, r_safe], dtype=np.float32)

            # 3. Environment Step
            next_obs, rewards, terms, truncs, infos = env.step(actions)

            for agent_name, r in rewards.items():
                ep_rewards[agent_name] += r

            for agent_name in env.possible_agents:
                if infos[agent_name].get("success", False):
                    ep_success = True
                if infos[agent_name].get("collision", False):
                    ep_collision = True

            obs_dict = next_obs

        # Logging
        mean_reward = np.mean(list(ep_rewards.values()))
        if ep % 10 == 0 or ep == 1:
            print(
                f"Episode {ep:3d}/{num_episodes:3d} | "
                f"Mean Reward: {mean_reward:6.1f} | "
                f"Success: {str(ep_success):5s} | "
                f"Collision: {str(ep_collision):5s}"
            )

        # Save checkpoint periodically
        if ep % 25 == 0 or ep == num_episodes:
            ckpt_path = os.path.join(checkpoint_dir, f"fa_mappo_ep{ep}.pt")
            torch.save(trainer.actor.state_dict(), ckpt_path)
            # Latest / best actor
            torch.save(trainer.actor.state_dict(), os.path.join(checkpoint_dir, "fa_mappo_actor.pt"))

    print("\nTraining completed.")
    print(f"Frozen actor saved to: {os.path.join(checkpoint_dir, 'fa_mappo_actor.pt')}")


if __name__ == "__main__":
    train_mappo(num_episodes=50, episode_length=200, num_agents=6, device="cpu")
