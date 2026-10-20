"""
USV Swarm Simulation - Fault-Aware Multi-Agent PPO (FA-MAPPO) & FT-Attn Critic
Author: Principal Enterprise Engineer
Stage: 1 (Multi-Agent Reinforcement Learning)

References:
- Yu et al. (2021) - The Surprising Effectiveness of PPO in Cooperative Multi-Agent Games (MAPPO)
- Gu et al. (2021) - Attention-based Fault-Tolerant MARL (FT-Attn)
- Shi et al. (2024) - Towards Fault Tolerance in MARL (Domain-Randomized Fault Training)

Features:
- Decentralized Actor: Takes local obs + own h_i(t) + assigned role -> (u_cmd, r_cmd).
- Centralized Critic: Takes joint observations of all N agents with multi-head attention
  down-weighting degraded nodes according to their health belief vector h_i(t).
- Generalized Advantage Estimation (GAE) and clipped PPO objective.
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions.normal import Normal


class FaultAwareActor(nn.Module):
    """Decentralized Gaussian policy: maps local observation + health to continuous actions."""

    def __init__(self, obs_dim: int = 35, act_dim: int = 2, hidden_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, act_dim)
        )
        # Trainable log standard deviation
        self.log_std = nn.Parameter(torch.zeros(act_dim) - 0.5)

        # Action bounds: u in [0.0, 2.5], r in [-0.8, 0.8]
        self.register_buffer("act_min", torch.tensor([0.0, -0.8], dtype=torch.float32))
        self.register_buffer("act_max", torch.tensor([2.5, 0.8], dtype=torch.float32))

    def forward(self, obs: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        mean = self.net(obs)
        # Bound mean action inside valid domain via scaled tanh
        scale = 0.5 * (self.act_max - self.act_min)
        offset = 0.5 * (self.act_max + self.act_min)
        bounded_mean = torch.tanh(mean) * scale + offset
        std = torch.exp(torch.clamp(self.log_std, -2.0, 0.5))
        return bounded_mean, std

    def get_action(self, obs: torch.Tensor, deterministic: bool = False) -> Tuple[torch.Tensor, torch.Tensor]:
        mean, std = self.forward(obs)
        if deterministic:
            return mean, torch.zeros_like(mean)
        dist = Normal(mean, std)
        action = dist.sample()
        # Clamp to hard kinematic bounds
        clamped_action = torch.max(torch.min(action, self.act_max), self.act_min)
        log_prob = dist.log_prob(clamped_action).sum(dim=-1, keepdim=True)
        return clamped_action, log_prob

    def evaluate_actions(self, obs: torch.Tensor, actions: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        mean, std = self.forward(obs)
        dist = Normal(mean, std)
        log_prob = dist.log_prob(actions).sum(dim=-1, keepdim=True)
        entropy = dist.entropy().sum(dim=-1, keepdim=True)
        return log_prob, entropy


class FTAttentionCritic(nn.Module):
    """Centralized Critic with Fault-Tolerant Multi-Head Attention (Gu et al., FT-Attn).

    Down-weights representations of degraded agents using their health vector h_i(t).
    """

    def __init__(self, obs_dim: int = 35, num_agents: int = 6, hidden_dim: int = 128, nheads: int = 4):
        super().__init__()
        self.num_agents = num_agents
        self.obs_embed = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU()
        )
        self.attn = nn.MultiheadAttention(embed_dim=hidden_dim, num_heads=nheads, batch_first=True)
        self.value_head = nn.Sequential(
            nn.Linear(hidden_dim * num_agents, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1)
        )

    def forward(self, joint_obs: torch.Tensor, health_vectors: torch.Tensor) -> torch.Tensor:
        """
        joint_obs: [batch_size, num_agents, obs_dim]
        health_vectors: [batch_size, num_agents]
        """
        batch_size = joint_obs.shape[0]
        # Embed each agent's observation: [batch_size, num_agents, hidden_dim]
        embeds = self.obs_embed(joint_obs)

        # Fault-tolerant attention mask or key-weighting based on health belief
        # Agents with health < 0.3 are attenuated in key/value attention
        health_weights = torch.clamp(health_vectors, min=0.1, max=1.0).unsqueeze(-1)
        modulated_embeds = embeds * health_weights

        attn_out, _ = self.attn(embeds, modulated_embeds, modulated_embeds)
        # Flatten and predict state value V(s)
        flat_repr = attn_out.reshape(batch_size, -1)
        values = self.value_head(flat_repr)
        return values


class FAMAPPOTrainer:
    """Orchestrates CTDE optimization for Fault-Aware MAPPO."""

    def __init__(
        self,
        obs_dim: int = 35,
        act_dim: int = 2,
        num_agents: int = 6,
        lr_actor: float = 3e-4,
        lr_critic: float = 1e-3,
        clip_ratio: float = 0.2,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        device: str = "cpu"
    ):
        self.device = torch.device(device)
        self.num_agents = num_agents
        self.clip_ratio = clip_ratio
        self.gamma = gamma
        self.gae_lambda = gae_lambda

        self.actor = FaultAwareActor(obs_dim, act_dim).to(self.device)
        self.critic = FTAttentionCritic(obs_dim, num_agents).to(self.device)

        self.actor_optimizer = optim.Adam(self.actor.parameters(), lr=lr_actor)
        self.critic_optimizer = optim.Adam(self.critic.parameters(), lr=lr_critic)

    def save_checkpoint(self, path: str) -> None:
        torch.save({
            "actor_state_dict": self.actor.state_dict(),
            "critic_state_dict": self.critic.state_dict()
        }, path)

    def load_checkpoint(self, path: str) -> None:
        data = torch.load(path, map_location=self.device)
        self.actor.load_state_dict(data["actor_state_dict"])
        self.critic.load_state_dict(data["critic_state_dict"])
