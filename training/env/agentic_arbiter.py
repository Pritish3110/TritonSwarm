"""
USV Swarm Simulation - Event-Triggered Agentic Recovery Arbiter
Author: Principal Enterprise Engineer
Stage: 1 & 2 (Agentic Anomaly Reasoning Engine)

Features:
- Event-triggered: Fires ONLY on fault-anomaly events (h_i < 0.3 or network partition).
- Supports 3 backends seamlessly:
  1. Groq API (ultra-fast cloud, reads os.environ.get("GROQ_API_KEY"))
  2. Local Ollama (qwen2.5:3b-instruct-q4_K_M or llama3.1:8b via langchain-ollama)
  3. Deterministic Fallback (offline testing, instant execution)
- Strictly bounded: Generates diagnostic rationale and approve/fallback verdicts.
- NEVER commands thrusters and NEVER overrides CBF safety shield (Raptis et al. 2025).
"""

from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class AnomalyEvent:
    timestamp: float
    agent_id: int
    fault_type: str
    health_index: float
    current_role: str
    active_mesh_links: int
    total_fleet_size: int
    encirclement_held: bool


@dataclass
class ArbiterDecision:
    timestamp: float
    agent_id: int
    approved: bool
    fallback_recommended: bool
    rationale: str
    recommended_role: str
    execution_time_ms: float


class AgenticRecoveryArbiter:
    """High-level event-triggered anomaly reasoning arbiter."""

    SYSTEM_PROMPT = """You are the Senior Recovery Arbiter for an autonomous marine USV swarm.
Your duty is to evaluate hardware/network faults and approve role reallocations.
Rules:
1. When a Tracker or Interceptor suffers propulsion or severe sensor failure (health < 0.3), approve demoting it to Relay or Scout.
2. Ensure healthy agents (health >= 0.7) are promoted to Tracker/Interceptor to preserve the 20m encirclement ring.
3. If mesh links drop drastically, prioritize establishing a Relay.
4. Output JSON strictly with keys: "approved" (bool), "fallback_recommended" (bool), "recommended_role" (str: "tracker"|"interceptor"|"relay"|"scout"), "rationale" (str)."""

    def __init__(
        self,
        default_backend: str = "auto",
        ollama_model: str = "qwen2.5:3b-instruct-q4_K_M",
        groq_model: str = "openai/gpt-oss-120b"
    ):
        self.default_backend = default_backend
        self.ollama_model = ollama_model
        self.groq_model = groq_model
        self.event_log: List[ArbiterDecision] = []

    def evaluate_anomaly(self, event: AnomalyEvent) -> ArbiterDecision:
        """Evaluates an anomaly event and produces diagnostic rationale and approval."""
        t0 = time.time()

        # Check environment configuration
        groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
        backend = self.default_backend

        if backend == "auto":
            if groq_api_key:
                backend = "groq"
            else:
                backend = "ollama"

        decision: Optional[ArbiterDecision] = None

        if backend == "groq" and groq_api_key:
            decision = self._call_groq(event, groq_api_key)

        if decision is None and backend in ["auto", "ollama"]:
            decision = self._call_ollama(event)

        if decision is None:
            # Deterministic fallback
            decision = self._deterministic_fallback(event)

        dt_ms = (time.time() - t0) * 1000.0
        decision.execution_time_ms = dt_ms
        self.event_log.append(decision)
        return decision

    def _call_groq(self, event: AnomalyEvent, api_key: str) -> Optional[ArbiterDecision]:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=api_key
            )
            prompt = self._format_prompt(event)
            response = client.chat.completions.create(
                model=self.groq_model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=250
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)
            return ArbiterDecision(
                timestamp=event.timestamp,
                agent_id=event.agent_id,
                approved=bool(parsed.get("approved", True)),
                fallback_recommended=bool(parsed.get("fallback_recommended", False)),
                rationale=str(parsed.get("rationale", "Approved via Groq LLM")),
                recommended_role=str(parsed.get("recommended_role", "relay")),
                execution_time_ms=0.0
            )
        except Exception:
            return None

    def _call_ollama(self, event: AnomalyEvent) -> Optional[ArbiterDecision]:
        try:
            import ollama
            prompt = self._format_prompt(event)
            response = ollama.chat(
                model=self.ollama_model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                format="json",
                options={"temperature": 0.1, "num_predict": 200}
            )
            content = response["message"]["content"]
            parsed = json.loads(content)
            return ArbiterDecision(
                timestamp=event.timestamp,
                agent_id=event.agent_id,
                approved=bool(parsed.get("approved", True)),
                fallback_recommended=bool(parsed.get("fallback_recommended", False)),
                rationale=str(parsed.get("rationale", "Approved via Ollama LLM")),
                recommended_role=str(parsed.get("recommended_role", "relay")),
                execution_time_ms=0.0
            )
        except Exception:
            return None

    def _deterministic_fallback(self, event: AnomalyEvent) -> ArbiterDecision:
        """Deterministic rule-based reasoning engine ensuring immediate recovery."""
        if event.health_index < 0.35:
            rec_role = "relay" if event.active_mesh_links < 3 else "scout"
            rationale = (
                f"Agent USV-{event.agent_id} detected with severe {event.fault_type} "
                f"(health={event.health_index:.2f}). Demoting from {event.current_role} to {rec_role} "
                "to preserve primary encirclement ring integrity."
            )
            fallback = not event.encirclement_held
            return ArbiterDecision(
                timestamp=event.timestamp,
                agent_id=event.agent_id,
                approved=True,
                fallback_recommended=fallback,
                rationale=rationale,
                recommended_role=rec_role,
                execution_time_ms=0.0
            )
        else:
            return ArbiterDecision(
                timestamp=event.timestamp,
                agent_id=event.agent_id,
                approved=True,
                fallback_recommended=False,
                rationale=f"Health acceptable ({event.health_index:.2f}); retaining nominal assignment.",
                recommended_role=event.current_role,
                execution_time_ms=0.0
            )

    def _format_prompt(self, event: AnomalyEvent) -> str:
        return (
            f"Anomaly Incident Report:\n"
            f"- Timestamp: {event.timestamp:.1f}s\n"
            f"- Target USV: usv_{event.agent_id}\n"
            f"- Active Fault: {event.fault_type}\n"
            f"- Health Belief h_i(t): {event.health_index:.2f}\n"
            f"- Current Role: {event.current_role}\n"
            f"- Active Mesh Links: {event.active_mesh_links} / {event.total_fleet_size - 1}\n"
            f"- Swarm Encirclement Ring Currently Intact: {event.encirclement_held}\n"
            f"Analyze and decide role reassignment and fallback necessity."
        )
