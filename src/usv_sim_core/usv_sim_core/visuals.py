"""
USV Swarm Simulation - Multi-Target Battlefield Visualizer
Author: Principal Enterprise Engineer
Stage: 2 (RViz2 Battle Immersion)

Builds the complete RViz2 MarkerArray for the battle: hostile hulls with per-target liveries,
containment and exclusion perimeters, ally vessels with role chevrons and telemetry billboards,
wing tethers showing which vessel is committed to which hostile, pulsed EMP defusal beams,
arrest cages, the gossip mesh, and a fleet-level battle HUD.

Extracted from sim_node so the physics loop and the presentation layer stay separable, and so
marker lifecycle (stale-id deletion) is handled in exactly one place: RViz retains any marker
id it has ever been sent, so ids that fall out of use must be explicitly deleted or they
linger as ghost geometry.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from geometry_msgs.msg import Point
from visualization_msgs.msg import Marker, MarkerArray

# Per-hostile livery: flagship first, then escorts. Colours are deliberately far apart in hue
# so wing affiliation is readable at a glance from any camera angle.
HOSTILE_LIVERY: Tuple[Tuple[float, float, float, str], ...] = (
    (0.95, 0.12, 0.12, "FLAGSHIP"),
    (0.72, 0.20, 0.95, "ESCORT"),
    (1.00, 0.62, 0.05, "PICKET"),
    (0.95, 0.15, 0.55, "RAIDER"),
    (0.20, 0.85, 0.85, "TENDER"),
)

ROLE_COLOR: Dict[str, Tuple[float, float, float]] = {
    "interceptor": (0.15, 0.35, 0.95),   # royal azure: strike element
    "tracker": (0.05, 0.75, 1.00),       # electric cyan: containment element
    "relay": (1.00, 0.75, 0.05),         # amber gold: mesh backbone
    "scout": (0.10, 0.90, 0.35),         # emerald: early warning
}
COLOR_DEGRADED = (1.00, 0.25, 0.05)
COLOR_CASUALTY = (0.42, 0.44, 0.48)

Z_WATERLINE = 0.02


@dataclass
class HostileVisual:
    """Presentation state for one hostile drone."""
    target_id: int
    x: float
    y: float
    psi: float
    speed: float
    health: float = 1.0
    defusal_progress: float = 0.0
    neutralized: bool = False
    combat_state: str = "COMBAT_PATROL"
    tactic: str = ""
    containment_radius: float = 20.0
    exclusion_radius: float = 14.0
    history: Sequence[Tuple[float, float]] = field(default_factory=tuple)

    @property
    def livery(self) -> Tuple[float, float, float, str]:
        return HOSTILE_LIVERY[self.target_id % len(HOSTILE_LIVERY)]


@dataclass
class AgentVisual:
    """Presentation state for one ally drone."""
    agent_id: int
    x: float
    y: float
    psi: float
    speed: float
    role: str = "tracker"
    health: float = 1.0
    thrust_efficiency: float = 1.0
    assigned_target_id: int = 0
    allocation_cost: float = 0.0
    tactical_state: str = ""
    history: Sequence[Tuple[float, float]] = field(default_factory=tuple)

    @property
    def is_casualty(self) -> bool:
        return self.health < 0.25

    @property
    def is_degraded(self) -> bool:
        return (self.health < 0.60) or (self.thrust_efficiency < 0.80)

    @property
    def color(self) -> Tuple[float, float, float]:
        if self.is_casualty:
            return COLOR_CASUALTY
        if self.is_degraded:
            return COLOR_DEGRADED
        return ROLE_COLOR.get(self.role.lower(), ROLE_COLOR["scout"])


class BattlefieldVisualizer:
    """Assembles the battle MarkerArray and manages marker lifecycle across frames."""

    def __init__(self, frame_id: str = "world"):
        self.frame_id = frame_id
        self.frame_count = 0
        # Highest marker id published per namespace on the previous frame, so ids that fall
        # out of use this frame can be explicitly deleted instead of lingering in RViz.
        self._prev_ns_ids: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Marker plumbing
    # ------------------------------------------------------------------
    def _marker(self, stamp, ns: str, mid: int, mtype: int) -> Marker:
        m = Marker()
        m.header.frame_id = self.frame_id
        m.header.stamp = stamp
        m.ns = ns
        m.id = mid
        m.type = mtype
        m.action = Marker.ADD
        m.pose.orientation.w = 1.0
        return m

    @staticmethod
    def _set_pose(m: Marker, x: float, y: float, z: float, psi: Optional[float] = None) -> None:
        m.pose.position.x = float(x)
        m.pose.position.y = float(y)
        m.pose.position.z = float(z)
        if psi is not None:
            m.pose.orientation.z = math.sin(psi / 2.0)
            m.pose.orientation.w = math.cos(psi / 2.0)

    @staticmethod
    def _set_color(m: Marker, r: float, g: float, b: float, a: float = 1.0) -> None:
        m.color.r = float(r)
        m.color.g = float(g)
        m.color.b = float(b)
        m.color.a = float(a)

    @staticmethod
    def _point(x: float, y: float, z: float) -> Point:
        p = Point()
        p.x = float(x)
        p.y = float(y)
        p.z = float(z)
        return p

    def _ring(self, stamp, ns: str, mid: int, cx: float, cy: float, radius: float,
              color: Tuple[float, float, float], alpha: float, width: float,
              z: float = 0.1, step_deg: int = 6) -> Marker:
        m = self._marker(stamp, ns, mid, Marker.LINE_STRIP)
        m.scale.x = width
        self._set_color(m, *color, alpha)
        for deg in range(0, 360 + step_deg, step_deg):
            rad = math.radians(deg)
            m.points.append(self._point(cx + radius * math.cos(rad),
                                        cy + radius * math.sin(rad), z))
        return m

    # ------------------------------------------------------------------
    # Hostile drones
    # ------------------------------------------------------------------
    def _hostile_markers(self, stamp, hostiles: Sequence[HostileVisual],
                         counts: Dict[str, int]) -> List[Marker]:
        out: List[Marker] = []
        for h in hostiles:
            r, g, b, livery = h.livery
            idx = h.target_id

            # Neutralized hulls go dark: the livery drains to a dead grey-green.
            if h.neutralized:
                r, g, b = 0.25, 0.65, 0.35

            # Hull. The flagship is visibly the largest hull in the hostile formation.
            scale_x = 5.4 if idx == 0 else 4.4
            hull = self._marker(stamp, "hostile_hull", idx, Marker.CUBE)
            self._set_pose(hull, h.x, h.y, 0.5, h.psi)
            hull.scale.x, hull.scale.y, hull.scale.z = scale_x, 2.2, 1.0
            self._set_color(hull, r, g, b, 0.95)
            out.append(hull)

            # Deckhouse, set slightly aft.
            cx = h.x - 0.5 * math.cos(h.psi)
            cy = h.y - 0.5 * math.sin(h.psi)
            cabin = self._marker(stamp, "hostile_cabin", idx, Marker.CUBE)
            self._set_pose(cabin, cx, cy, 1.25, h.psi)
            cabin.scale.x, cabin.scale.y, cabin.scale.z = 1.9, 1.5, 0.7
            self._set_color(cabin, 0.20, 0.22, 0.28, 0.98)
            out.append(cabin)

            # Radar dome: pulses while the hostile is actively evading.
            mast = self._marker(stamp, "hostile_mast", idx, Marker.CYLINDER)
            self._set_pose(mast, cx, cy, 1.9)
            mast.scale.x = mast.scale.y = 0.5
            mast.scale.z = 0.5
            pulse = 0.55 + 0.45 * math.sin(self.frame_count * 0.35 + idx)
            if h.neutralized:
                self._set_color(mast, 0.35, 0.38, 0.40, 1.0)
            else:
                self._set_color(mast, 1.0, pulse, pulse, 1.0)
            out.append(mast)

            # Bow vector.
            arrow = self._marker(stamp, "hostile_heading", idx, Marker.ARROW)
            self._set_pose(arrow, h.x, h.y, 0.5, h.psi)
            arrow.scale.x, arrow.scale.y, arrow.scale.z = 3.4, 0.5, 0.5
            self._set_color(arrow, 1.0, 0.85, 0.1, 0.95 if not h.neutralized else 0.3)
            out.append(arrow)

            # Floating billboard.
            bill = self._marker(stamp, "hostile_billboard", idx, Marker.TEXT_VIEW_FACING)
            self._set_pose(bill, h.x, h.y, 3.6)
            bill.scale.z = 1.15
            if h.neutralized:
                self._set_color(bill, 0.1, 1.0, 0.2, 1.0)
                bill.text = (f"* HOSTILE-{idx} {livery} DEFUSED & ARRESTED *\n"
                             f"Speed: {h.speed:.2f} m/s | Under Guard")
            elif h.defusal_progress > 0.0:
                self._set_color(bill, 1.0, 0.9, 0.1, 1.0)
                bill.text = (f"HOSTILE-{idx} {livery} | EMP DEFUSAL {int(h.defusal_progress)}%\n"
                             f"Speed: {h.speed:.2f} m/s | Hull {h.health*100:.0f}% | {h.combat_state}")
            else:
                self._set_color(bill, r, max(g, 0.2), max(b, 0.2), 1.0)
                bill.text = (f"HOSTILE-{idx} {livery} [{h.combat_state}]\n"
                             f"Speed: {h.speed:.2f} m/s | Hull {h.health*100:.0f}%"
                             + (f" | {h.tactic}" if h.tactic else ""))
            out.append(bill)

            # Wake trail.
            if len(h.history) >= 2:
                wake = self._marker(stamp, "hostile_wake", idx, Marker.LINE_STRIP)
                wake.scale.x = 0.26
                self._set_color(wake, r, g, b, 0.40)
                for wx, wy in h.history:
                    wake.points.append(self._point(wx, wy, Z_WATERLINE))
                out.append(wake)

            # Containment perimeter and the hard exclusion envelope the shield enforces.
            out.append(self._ring(stamp, "hostile_containment_ring", idx, h.x, h.y,
                                  h.containment_radius, (0.10, 0.95, 0.55),
                                  0.30 if h.neutralized else 0.80, 0.30, z=0.10))
            out.append(self._ring(stamp, "hostile_exclusion_ring", idx, h.x, h.y,
                                  h.exclusion_radius, (0.95, 0.35, 0.10),
                                  0.25 if h.neutralized else 0.60, 0.22, z=0.14))

            # Arrest cage: visual proof the hull is contained.
            if h.neutralized:
                cage = self._marker(stamp, "hostile_arrest_cage", idx, Marker.CYLINDER)
                self._set_pose(cage, h.x, h.y, 1.6)
                cage.scale.x = cage.scale.y = 13.0
                cage.scale.z = 3.2
                self._set_color(cage, 0.2, 1.0, 0.3, 0.30)
                out.append(cage)

        counts["hostile_hull"] = len(hostiles) - 1
        counts["hostile_cabin"] = len(hostiles) - 1
        counts["hostile_mast"] = len(hostiles) - 1
        counts["hostile_heading"] = len(hostiles) - 1
        counts["hostile_billboard"] = len(hostiles) - 1
        counts["hostile_containment_ring"] = len(hostiles) - 1
        counts["hostile_exclusion_ring"] = len(hostiles) - 1
        counts["hostile_wake"] = max(
            (h.target_id for h in hostiles if len(h.history) >= 2), default=-1)
        counts["hostile_arrest_cage"] = max(
            (h.target_id for h in hostiles if h.neutralized), default=-1)
        return out

    # ------------------------------------------------------------------
    # Ally drones
    # ------------------------------------------------------------------
    def _agent_markers(self, stamp, agents: Sequence[AgentVisual],
                       counts: Dict[str, int]) -> List[Marker]:
        out: List[Marker] = []
        for a in agents:
            r, g, b = a.color
            i = a.agent_id

            hull = self._marker(stamp, "usv_hull", i, Marker.CUBE)
            self._set_pose(hull, a.x, a.y, 0.35, a.psi)
            hull.scale.x, hull.scale.y, hull.scale.z = 3.6, 1.5, 0.6
            self._set_color(hull, r, g, b, 0.95)
            out.append(hull)

            cab_x = a.x - 0.4 * math.cos(a.psi)
            cab_y = a.y - 0.4 * math.sin(a.psi)
            cabin = self._marker(stamp, "usv_cabin", i, Marker.CUBE)
            self._set_pose(cabin, cab_x, cab_y, 0.85, a.psi)
            cabin.scale.x, cabin.scale.y, cabin.scale.z = 1.5, 1.0, 0.55
            self._set_color(cabin, 0.92, 0.94, 0.98, 0.98)
            out.append(cabin)

            # Comms mast: green when meshed, pulsing red when degraded, dark when lost.
            mast = self._marker(stamp, "usv_mast", i, Marker.CYLINDER)
            self._set_pose(mast, cab_x, cab_y, 1.45)
            mast.scale.x = mast.scale.y = 0.3
            mast.scale.z = 0.7
            if a.is_casualty:
                self._set_color(mast, 0.3, 0.3, 0.32, 1.0)
            elif a.is_degraded:
                blink = 0.5 + 0.5 * math.sin(self.frame_count * 0.5 + i)
                self._set_color(mast, 1.0, 0.1 + 0.3 * blink, 0.1, 1.0)
            else:
                self._set_color(mast, 0.1, 0.8, 1.0, 1.0)
            out.append(mast)

            # Role chevron above the mast: role is readable without reading any text.
            chev = self._marker(stamp, "usv_role_chevron", i, Marker.CUBE)
            self._set_pose(chev, cab_x, cab_y, 2.05, a.psi)
            chev.scale.x, chev.scale.y, chev.scale.z = 1.1, 0.26, 0.26
            self._set_color(chev, r, g, b, 1.0)
            out.append(chev)

            arrow = self._marker(stamp, "usv_arrow", i, Marker.ARROW)
            self._set_pose(arrow, a.x, a.y, 0.4, a.psi)
            arrow.scale.x, arrow.scale.y, arrow.scale.z = 2.8, 0.35, 0.35
            self._set_color(arrow, r, g, b, 0.3 if a.is_casualty else 0.9)
            out.append(arrow)

            bill = self._marker(stamp, "usv_billboard", i, Marker.TEXT_VIEW_FACING)
            self._set_pose(bill, a.x, a.y, 2.8)
            bill.scale.z = 0.82
            wing_label = HOSTILE_LIVERY[a.assigned_target_id % len(HOSTILE_LIVERY)][3]
            if a.is_casualty:
                self._set_color(bill, 1.0, 0.25, 0.2, 1.0)
                bill.text = f"USV-{i} [X COMBAT LOSS]\nDEAD IN WATER | h={a.health:.2f}"
            elif a.is_degraded:
                self._set_color(bill, 1.0, 0.45, 0.1, 1.0)
                bill.text = (f"USV-{i} [{a.role.upper()}] -> T{a.assigned_target_id}\n"
                             f"DEGRADED h={a.health:.2f} | thrust {a.thrust_efficiency*100:.0f}%")
            else:
                self._set_color(bill, 0.9, 0.95, 1.0, 1.0)
                bill.text = (f"USV-{i} [{a.role.upper()}] -> T{a.assigned_target_id} {wing_label}\n"
                             f"{a.tactical_state} | {a.speed:.1f} m/s | h={a.health:.2f}")
            out.append(bill)

            if len(a.history) >= 2 and not a.is_casualty:
                wake = self._marker(stamp, "usv_wake", i, Marker.LINE_STRIP)
                wake.scale.x = 0.20
                self._set_color(wake, 0.65, 0.85, 1.0, 0.45)
                for wx, wy in a.history:
                    wake.points.append(self._point(wx, wy, Z_WATERLINE))
                out.append(wake)

        last = len(agents) - 1
        for ns in ("usv_hull", "usv_cabin", "usv_mast", "usv_role_chevron",
                   "usv_arrow", "usv_billboard"):
            counts[ns] = last
        counts["usv_wake"] = max(
            (a.agent_id for a in agents if len(a.history) >= 2 and not a.is_casualty),
            default=-1)
        return out

    # ------------------------------------------------------------------
    # Tactical overlays
    # ------------------------------------------------------------------
    def _wing_tethers(self, stamp, agents: Sequence[AgentVisual],
                      hostiles: Sequence[HostileVisual],
                      counts: Dict[str, int]) -> List[Marker]:
        """One LINE_LIST per hostile livery, linking every committed vessel to its hostile."""
        by_target: Dict[int, List[AgentVisual]] = {}
        for a in agents:
            if not a.is_casualty:
                by_target.setdefault(a.assigned_target_id, []).append(a)

        out: List[Marker] = []
        hostile_by_id = {h.target_id: h for h in hostiles}
        for tid, members in sorted(by_target.items()):
            host = hostile_by_id.get(tid)
            if host is None:
                continue
            r, g, b, _ = host.livery
            m = self._marker(stamp, "wing_tether", tid, Marker.LINE_LIST)
            m.scale.x = 0.09
            self._set_color(m, r, g, b, 0.22)
            for a in members:
                m.points.append(self._point(a.x, a.y, 0.55))
                m.points.append(self._point(host.x, host.y, 0.9))
            out.append(m)
        counts["wing_tether"] = max(by_target.keys(), default=-1)
        return out

    def _emp_beams(self, stamp, agents: Sequence[AgentVisual],
                   hostiles: Sequence[HostileVisual],
                   counts: Dict[str, int]) -> List[Marker]:
        """Pulsed EMP defusal beams from each locked interceptor to its hostile."""
        hostile_by_id = {h.target_id: h for h in hostiles}
        out: List[Marker] = []
        drawn: List[int] = []

        for a in agents:
            if "interceptor" not in a.role.lower() or a.is_casualty:
                continue
            host = hostile_by_id.get(a.assigned_target_id)
            if host is None or host.defusal_progress <= 0.0 or host.neutralized:
                continue

            # Dashed, travelling segments read as a pulsed energy beam rather than a static line.
            beam = self._marker(stamp, "emp_beam", a.agent_id, Marker.LINE_LIST)
            beam.scale.x = 0.34
            intensity = 0.55 + 0.45 * math.sin(self.frame_count * 0.8)
            self._set_color(beam, 0.2, 0.8 * intensity + 0.2, 1.0, 0.95)

            segments = 7
            offset = (self.frame_count % 8) / 8.0
            for s in range(segments):
                t0 = (s + offset * 0.5) / segments
                t1 = min(1.0, t0 + 0.42 / segments)
                beam.points.append(self._point(a.x + (host.x - a.x) * t0,
                                               a.y + (host.y - a.y) * t0,
                                               1.2 + 0.3 * t0))
                beam.points.append(self._point(a.x + (host.x - a.x) * t1,
                                               a.y + (host.y - a.y) * t1,
                                               1.2 + 0.3 * t1))
            out.append(beam)
            drawn.append(a.agent_id)

            # Impact flare on the hostile hull.
            flare = self._marker(stamp, "emp_impact", host.target_id, Marker.SPHERE)
            self._set_pose(flare, host.x, host.y, 1.5)
            flare.scale.x = flare.scale.y = flare.scale.z = 1.4 + 0.6 * intensity
            self._set_color(flare, 0.4, 0.9, 1.0, 0.55)
            out.append(flare)

        counts["emp_beam"] = max(drawn, default=-1)
        counts["emp_impact"] = max(
            (h.target_id for h in hostiles
             if h.defusal_progress > 0.0 and not h.neutralized), default=-1)
        return out

    def _comms_mesh(self, stamp, agents: Sequence[AgentVisual], comm_range: float,
                    counts: Dict[str, int]) -> List[Marker]:
        live = [a for a in agents if not a.is_casualty]
        mesh = self._marker(stamp, "comms_mesh", 0, Marker.LINE_LIST)
        mesh.scale.x = 0.11
        self._set_color(mesh, 0.15, 0.85, 0.70, 0.30)
        for i in range(len(live)):
            for j in range(i + 1, len(live)):
                a, b = live[i], live[j]
                if math.hypot(a.x - b.x, a.y - b.y) <= comm_range:
                    mesh.points.append(self._point(a.x, a.y, 0.7))
                    mesh.points.append(self._point(b.x, b.y, 0.7))
        counts["comms_mesh"] = 0
        return [mesh]

    def _hud(self, stamp, hud_lines: Sequence[str], anchor: Tuple[float, float],
             counts: Dict[str, int]) -> List[Marker]:
        if not hud_lines:
            counts["battle_hud"] = -1
            return []
        hud = self._marker(stamp, "battle_hud", 0, Marker.TEXT_VIEW_FACING)
        self._set_pose(hud, anchor[0], anchor[1], 26.0)
        hud.scale.z = 2.1
        self._set_color(hud, 0.85, 0.95, 1.0, 0.95)
        hud.text = "\n".join(hud_lines)
        counts["battle_hud"] = 0
        return [hud]

    # ------------------------------------------------------------------
    # Frame assembly
    # ------------------------------------------------------------------
    def build(
        self,
        stamp,
        hostiles: Sequence[HostileVisual],
        agents: Sequence[AgentVisual],
        hud_lines: Sequence[str] = (),
        hud_anchor: Tuple[float, float] = (0.0, 0.0),
        comm_range: float = 55.0,
    ) -> MarkerArray:
        """Builds one complete battle frame, including deletion of now-unused marker ids."""
        self.frame_count += 1
        counts: Dict[str, int] = {}
        markers = MarkerArray()

        markers.markers.extend(self._hostile_markers(stamp, hostiles, counts))
        markers.markers.extend(self._agent_markers(stamp, agents, counts))
        markers.markers.extend(self._wing_tethers(stamp, agents, hostiles, counts))
        markers.markers.extend(self._emp_beams(stamp, agents, hostiles, counts))
        markers.markers.extend(self._comms_mesh(stamp, agents, comm_range, counts))
        markers.markers.extend(self._hud(stamp, hud_lines, hud_anchor, counts))

        markers.markers.extend(self._reap_stale(stamp, counts))
        self._prev_ns_ids = {ns: hi for ns, hi in counts.items()}
        return markers

    def _reap_stale(self, stamp, counts: Dict[str, int]) -> List[Marker]:
        """
        Emits DELETE markers for ids published last frame but not this one. Without this an
        EMP beam, arrest cage or casualty marker would persist in RViz after its cause ended.
        """
        stale: List[Marker] = []
        for ns, prev_high in self._prev_ns_ids.items():
            now_high = counts.get(ns, -1)
            for mid in range(now_high + 1, prev_high + 1):
                m = self._marker(stamp, ns, mid, Marker.CUBE)
                m.action = Marker.DELETE
                stale.append(m)
        return stale
