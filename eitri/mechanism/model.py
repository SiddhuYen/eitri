"""The mechanism model: links, joints, actuators, point masses and targets, with forward kinematics.

A mechanism model file is the same contract as a CAD or harness model:

    from eitri.mechanism import *

    PARAMS = dict(upper_mm=120, fore_mm=100, payload_g=50)

    def build(p):
        m = Mechanism("3-DOF arm")
        m.link("base", fixed=True)
        m.link("turret", length_mm=40, mass_g=70, direction=(0, 0, 1))      # a link's x axis points along its length
        m.link("upper", length_mm=p.upper_mm, mass_g=60)
        m.link("fore", length_mm=p.fore_mm, mass_g=30)
        m.joint("yaw", "base", "turret", axis=(0, 0, 1), limit_deg=(-90, 90), actuator=Actuator("MG996R", stall_kgcm=9.4, speed_deg_s=400))
        m.joint("shoulder", "turret", "upper", axis=(0, 1, 0), limit_deg=(-20, 100), actuator=...)
        m.joint("elbow", "upper", "fore", axis=(0, 1, 0), limit_deg=(-10, 120), actuator=...)
        m.payload(mass_g=p.payload_g)
        m.target("pick", (200, 0, 20))
        return m

Frames. Every link has a frame: its origin at the joint that moves it, its x axis along its length (so the tip of a link of
length L is at (L, 0, 0) in its frame, and its centre of mass at (L/2, 0, 0) unless given). Joints and directions are declared in
WORLD coordinates at the zero pose: a joint sits `at` a world point (default: the tip of its parent link) and moves about / along
`axis`, a world direction; the child link's `direction` is the world direction its x axis points in at the zero pose. The zero pose is where all joint values are 0: declare it as the mechanism's most extended pose, which is the worst case for
torque. Units: mm, grams, degrees; gravity is -Z.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Iterable

import numpy as np

G = 9.80665                    # m/s^2, standard gravity (ISO 80000-3)
KGCM_PER_NM = 1.0 / 0.0980665  # 1 kgf.cm = 0.0980665 N.m (the kilogram-force is defined with standard gravity)
# The share of stall torque (or force) taken as the continuous rating when neither the model nor the actuator catalog gives one.
# It is an ASSUMPTION, not a spec, and the checks say so: hobby-servo makers publish stall only. 1/3 is the ratio of the one
# hobby-class servo in the catalog that publishes a continuous rating (Feetech STS3215: 6.5 of 19.5 kg.cm at 6 V), and it keeps
# below the "half of stall" rule of thumb on purpose; ROBOTIS asks for 1/5 of stall for steady loads on its XL320.
ASSUMED_RATED_FRACTION = 1 / 3


@dataclass(frozen=True)
class ActuatorSpec:
    """A catalog actuator: what its datasheet gives (rated None: the maker publishes no continuous rating)."""
    key: str
    stall_kgcm: float
    rated_kgcm: float | None
    at_V: float
    source: str


# Datasheet figures, fetched 2026-10-26. Matched by name: Actuator("MG996R", ...) or Actuator("Feetech STS3215", ...).
ACTUATOR_CATALOG = {s.key: s for s in [
    ActuatorSpec("STS3215", 19.5, 6.5, 6.0, "Feetech STS3215 datasheet as listed by Core Electronics and RobotShop: rated continuous "
                 "torque 6.5 kg.cm, peak 19.5 kg.cm, both at 6 V"),
    ActuatorSpec("MG996R", 9.4, None, 4.8, "TowerPro MG996R datasheet: stall 9.4 kgf.cm at 4.8 V, 11 kgf.cm at 6 V; no continuous rating"),
    ActuatorSpec("SG90", 1.8, None, 4.8, "TowerPro SG90 datasheet: stall 1.8 kgf.cm at 4.8 V; no continuous rating"),
]}


def catalog_actuator(name: str) -> ActuatorSpec | None:
    """The catalog entry whose key appears in an actuator's name ("TowerPro MG996R" -> MG996R), else None."""
    squashed = "".join(ch for ch in str(name).upper() if ch.isalnum())
    return next((s for k, s in ACTUATOR_CATALOG.items() if k in squashed), None)

JOINT_TYPES = ("revolute", "prismatic", "fixed")
MOUNTS = ("table", "edge", "ceiling", "wall")   # "table": the base plane is a surface the arm must stay above


class MechanismError(ValueError):
    """The mechanism is described wrongly (not a mechanical problem: those are reported by the checks)."""


def _vec(v, what: str) -> np.ndarray:
    try:
        a = np.array([float(x) for x in v], dtype=float)
    except (TypeError, ValueError):
        raise MechanismError(f"{what} must be three numbers (x, y, z), got {v!r}") from None
    if a.shape != (3,):
        raise MechanismError(f"{what} must be three numbers (x, y, z), got {v!r}")
    return a


def _unit(v, what: str) -> np.ndarray:
    a = _vec(v, what)
    n = float(np.linalg.norm(a))
    if n < 1e-12:
        raise MechanismError(f"{what} must not be the zero vector")
    return a / n


@dataclass(eq=False)
class Actuator:
    """What drives a joint. Torque and speed are at the motor's own shaft; `gear_ratio` > 1 multiplies torque (x efficiency) and
    divides speed at the joint. rated_* is the recommended continuous value: as given, else the catalog's datasheet figure (a
    revolute actuator named after a catalog entry), else ASSUMED_RATED_FRACTION x stall, an assumption the checks name."""
    name: str = "actuator"
    stall_kgcm: float | None = None      # revolute: stall torque, kgf.cm
    rated_kgcm: float | None = None
    stall_N: float | None = None         # prismatic: stall force, N
    rated_N: float | None = None
    speed_deg_s: float | None = None     # no-load speed (revolute), deg/s
    speed_mm_s: float | None = None      # no-load speed (prismatic), mm/s
    gear_ratio: float = 1.0
    efficiency: float = 1.0              # of the transmission (1.0 = direct drive)
    travel_deg: float | None = None      # the actuator's own travel (a 180 deg servo); None = unlimited
    travel_mm: float | None = None
    mass_g: float = 0.0                  # the motor body, carried by the parent link at the joint
    notes: str = ""

    def __post_init__(self) -> None:
        if self.gear_ratio <= 0 or not 0 < self.efficiency <= 1:
            raise MechanismError(f"actuator {self.name!r}: gear_ratio must be positive and efficiency in (0, 1]")
        for k in ("stall_kgcm", "rated_kgcm", "stall_N", "rated_N", "speed_deg_s", "speed_mm_s", "travel_deg", "travel_mm"):
            v = getattr(self, k)
            if v is not None and float(v) <= 0:
                raise MechanismError(f"actuator {self.name!r}: {k} must be positive")
        if self.mass_g < 0:
            raise MechanismError(f"actuator {self.name!r}: mass_g must not be negative")

    def stall_out(self, kind: str) -> float | None:
        """Stall torque (kgf.cm) or force (N) at the joint, after the transmission."""
        v = self.stall_kgcm if kind == "revolute" else self.stall_N
        return None if v is None else float(v) * self.gear_ratio * self.efficiency

    def rated_out(self, kind: str) -> tuple[float | None, bool]:
        """(recommended continuous torque/force at the joint, whether it is assumed from the stall value)."""
        v = self.rated_kgcm if kind == "revolute" else self.rated_N
        if v is None and kind == "revolute":
            spec = catalog_actuator(self.name)
            v = spec.rated_kgcm if spec else None
        if v is not None:
            return float(v) * self.gear_ratio * self.efficiency, False
        s = self.stall_out(kind)
        return (None if s is None else s * ASSUMED_RATED_FRACTION), True

    def rated_basis(self, kind: str) -> str:
        """Where the continuous rating comes from, for the check message."""
        if (self.rated_kgcm if kind == "revolute" else self.rated_N) is not None:
            return "as given"
        spec = catalog_actuator(self.name) if kind == "revolute" else None
        if spec is not None and spec.rated_kgcm is not None:
            return f"datasheet: {spec.source}"
        why = f"{spec.key} publishes no continuous rating" if spec is not None else "no rated value given"
        return f"assumed, not a spec: {Fraction(ASSUMED_RATED_FRACTION).limit_denominator(20)} of stall ({why})"

    def speed_out(self, kind: str) -> float | None:
        v = self.speed_deg_s if kind == "revolute" else self.speed_mm_s
        return None if v is None else float(v) / self.gear_ratio

    def travel_out(self, kind: str) -> float | None:
        v = self.travel_deg if kind == "revolute" else self.travel_mm
        return None if v is None else float(v) / self.gear_ratio


@dataclass(eq=False)
class Link:
    name: str
    length_mm: float = 0.0
    mass_g: float = 0.0
    cg: Any = None                       # centre of mass in the link's frame (default: (length/2, 0, 0))
    fixed: bool = False                  # the ground link
    direction: Any = (1.0, 0.0, 0.0)     # the world direction of the link's x axis at the zero pose
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name or any(c in str(self.name) for c in ". \t\n"):
            raise MechanismError(f"link name {self.name!r} must be non-empty and contain no dots or spaces")
        self.name = str(self.name)
        if float(self.length_mm) < 0 or float(self.mass_g) < 0:
            raise MechanismError(f"link {self.name}: length_mm and mass_g must not be negative")
        self.length_mm, self.mass_g = float(self.length_mm), float(self.mass_g)
        self.cg = _vec(self.cg, f"link {self.name} cg") if self.cg is not None else np.array([self.length_mm / 2, 0.0, 0.0])
        self.direction = _unit(self.direction, f"link {self.name} direction")

    @property
    def tip(self) -> np.ndarray:
        return np.array([self.length_mm, 0.0, 0.0])


@dataclass(eq=False)
class Joint:
    name: str
    parent: str
    child: str
    type: str = "revolute"
    axis: Any = (0.0, 0.0, 1.0)          # world direction at the zero pose
    at: Any = None                       # world point at the zero pose (default: the parent link's tip)
    limit: Any = None                    # (lo, hi) in degrees (revolute) or mm (prismatic); None for a fixed joint
    actuator: Actuator | None = None
    home: float = 0.0                    # the joint value of the home pose (default 0, which must be within the limits)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.name or any(c in str(self.name) for c in ". \t\n"):
            raise MechanismError(f"joint name {self.name!r} must be non-empty and contain no dots or spaces")
        self.name, self.parent, self.child = str(self.name), str(self.parent), str(self.child)
        if self.type not in JOINT_TYPES:
            raise MechanismError(f"joint {self.name}: type must be one of {JOINT_TYPES}, got {self.type!r}")
        if self.parent == self.child:
            raise MechanismError(f"joint {self.name}: parent and child must differ")
        self.axis = _unit(self.axis, f"joint {self.name} axis")
        if self.at is not None:
            self.at = _vec(self.at, f"joint {self.name} at")
        if self.type == "fixed":
            self.limit = None
        else:
            if self.limit is None or len(self.limit) != 2:
                raise MechanismError(f"joint {self.name}: give limit_deg=(lo, hi) (revolute) or limit_mm=(lo, hi) (prismatic)")
            lo, hi = float(self.limit[0]), float(self.limit[1])
            if not lo < hi:
                raise MechanismError(f"joint {self.name}: the limit's low end must be below its high end, got ({lo:g}, {hi:g})")
            self.limit = (lo, hi)
            if not lo <= float(self.home) <= hi:
                raise MechanismError(f"joint {self.name}: home {self.home:g} is outside the limits ({lo:g}, {hi:g})")
        if self.actuator is not None and not isinstance(self.actuator, Actuator):
            raise MechanismError(f"joint {self.name}: actuator must be an Actuator(...)")

    @property
    def unit(self) -> str:
        return "mm" if self.type == "prismatic" else "deg"

    @property
    def moving(self) -> bool:
        return self.type != "fixed"


@dataclass(eq=False)
class PointMass:
    name: str
    link: str
    mass_g: float
    at: Any = None                       # in the link's frame (default: the link's tip)
    kind: str = "payload"                # payload | mass

    def __post_init__(self) -> None:
        try:
            mass = float(self.mass_g)
        except (TypeError, ValueError):
            call = "payload(mass_g, link=None, at=None, name='payload')" if self.kind == "payload" else "mass(name, link, mass_g, at=None)"
            raise MechanismError(f"{self.name}: mass_g must be a number of grams, not {self.mass_g!r} (the call is {call}, "
                                 f"e.g. m.payload(50))") from None
        if mass < 0:
            raise MechanismError(f"{self.name}: mass_g must not be negative")
        self.mass_g = mass
        if self.at is not None:
            self.at = _vec(self.at, f"{self.name} at")


@dataclass(eq=False)
class Target:
    name: str
    at: Any
    tol_mm: float = 2.0

    def __post_init__(self) -> None:
        self.at = _vec(self.at, f"target {self.name} at")
        if float(self.tol_mm) <= 0:
            raise MechanismError(f"target {self.name}: tol_mm must be positive")
        self.tol_mm = float(self.tol_mm)


# --------------------------------------------------------------------------- the mechanism


class Mechanism:
    def __init__(self, name: str = "mechanism", description: str = ""):
        self.name = name
        self.description = description
        self.links: dict[str, Link] = {}
        self.joints: list[Joint] = []
        self.masses: list[PointMass] = []
        self.targets: list[Target] = []
        self.speeds: dict[str, float] = {}           # joint -> required speed (deg/s or mm/s)
        self.tip_link: str | None = None
        self.tip_at: np.ndarray | None = None
        self.reach_mm: float | None = None
        self.poses: int = 24                         # random poses sampled by the checks, besides the structured ones
        self.mounted_on: str = "table"               # m.mount(): where the base is fixed
        self.layout: dict[str, list[float]] = {}

    # -- building ----------------------------------------------------------
    def mount(self, on: str = "table") -> None:
        """Where the base is fixed. "table" (the default): the base plane (z = 0 of the base) is a surface, and the checks warn
        when any part of the mechanism reaches below it. "edge", "ceiling" or "wall": it is meant to reach below its base."""
        on = str(on).strip().lower()
        if on not in MOUNTS:
            raise MechanismError(f"mount(on=...) must be one of {MOUNTS}, got {on!r}")
        self.mounted_on = on

    def link(self, name: str, length_mm: float = 0.0, mass_g: float = 0.0, cg=None, fixed: bool = False, direction=(1.0, 0.0, 0.0), **kw) -> Link:
        l = Link(name, length_mm, mass_g, cg, fixed, direction, **kw)
        if l.name in self.links:
            raise MechanismError(f"link {l.name!r} is already defined")
        self.links[l.name] = l
        return l

    def joint(self, name: str, parent, child, type: str = "revolute", axis=(0.0, 0.0, 1.0), at=None, limit=None, limit_deg=None,
              limit_mm=None, actuator: Actuator | None = None, home: float = 0.0, **kw) -> Joint:
        parent = parent.name if isinstance(parent, Link) else str(parent)
        child = child.name if isinstance(child, Link) else str(child)
        for n in (parent, child):
            if n not in self.links:
                raise MechanismError(f"joint {name!r}: no link {n!r}; define it first with m.link({n!r}, ...); links: {list(self.links)}")
        if any(j.name == name for j in self.joints):
            raise MechanismError(f"joint {name!r} is already defined")
        if any(j.child == child for j in self.joints):
            raise MechanismError(f"link {child!r} is already moved by joint {next(j.name for j in self.joints if j.child == child)!r}")
        if self.links[child].fixed:
            raise MechanismError(f"joint {name!r}: {child!r} is the fixed link and cannot be moved")
        lim = limit if limit is not None else limit_mm if type == "prismatic" else limit_deg
        if type == "revolute" and limit_mm is not None:
            raise MechanismError(f"joint {name!r}: a revolute joint takes limit_deg, not limit_mm")
        if type == "prismatic" and limit_deg is not None:
            raise MechanismError(f"joint {name!r}: a prismatic joint takes limit_mm, not limit_deg")
        j = Joint(name, parent, child, type, axis, at, lim, actuator, home, **kw)
        self.joints.append(j)
        return j

    def payload(self, mass_g: float, link: str | None = None, at=None, name: str = "payload") -> PointMass:
        pm = PointMass(name, str(link) if link else "", mass_g, at, "payload")
        self.masses.append(pm)
        return pm

    def mass(self, name: str, link: str, mass_g: float, at=None) -> PointMass:
        """An extra point mass carried by a link (a gripper, a camera), at a point in the link's frame."""
        if str(link) not in self.links:
            raise MechanismError(f"mass {name!r}: no link {link!r}")
        pm = PointMass(name, str(link), mass_g, at, "mass")
        self.masses.append(pm)
        return pm

    def tip(self, link: str, at=None) -> None:
        if str(link) not in self.links:
            raise MechanismError(f"tip: no link {link!r}")
        self.tip_link, self.tip_at = str(link), (None if at is None else _vec(at, "tip at"))

    def target(self, name: str, at, tol_mm: float = 2.0) -> Target:
        if any(t.name == name for t in self.targets):
            raise MechanismError(f"target {name!r} is already defined")
        t = Target(str(name), at, tol_mm)
        self.targets.append(t)
        return t

    def speed(self, joint: str, deg_s: float | None = None, mm_s: float | None = None) -> None:
        j = self.get_joint(joint)
        v = deg_s if j.type == "revolute" else mm_s
        if v is None or float(v) <= 0:
            raise MechanismError(f"speed for {j.name}: give {'deg_s' if j.type == 'revolute' else 'mm_s'}= > 0")
        self.speeds[j.name] = float(v)

    def require_reach(self, mm: float) -> None:
        self.reach_mm = float(mm)

    def place(self, name: str, x: float, y: float) -> None:
        self.layout[name] = [float(x), float(y)]

    def apply_layout(self, layout) -> None:
        for k, v in (layout or {}).items():
            try:
                self.layout[str(k)] = [float(v[0]), float(v[1])]
            except (TypeError, ValueError, IndexError):
                continue

    # -- queries -------------------------------------------------------------
    def get_joint(self, name) -> Joint:
        name = name.name if isinstance(name, Joint) else str(name)
        for j in self.joints:
            if j.name == name:
                return j
        raise MechanismError(f"no joint {name!r}; joints: {[j.name for j in self.joints]}")

    def get_link(self, name) -> Link:
        if str(name) not in self.links:
            raise MechanismError(f"no link {name!r}; links: {list(self.links)}")
        return self.links[str(name)]

    @property
    def base(self) -> str:
        """The ground link: the one marked fixed (exactly one), else the one no joint moves (exactly one)."""
        fixed = [l.name for l in self.links.values() if l.fixed]
        if len(fixed) > 1:
            raise MechanismError(f"more than one fixed link: {fixed}")
        if fixed:
            return fixed[0]
        children = {j.child for j in self.joints}
        roots = [n for n in self.links if n not in children]
        if len(roots) != 1:
            raise MechanismError(f"mark the ground link fixed=True (candidates: {roots})" if roots else "no ground link: every link is moved by a joint")
        return roots[0]

    def order(self) -> list[Joint]:
        """The joints from the base outwards (parents before children); validates that they form a tree."""
        base = self.base
        placed = {base}
        out: list[Joint] = []
        pending = list(self.joints)
        while pending:
            ready = [j for j in pending if j.parent in placed]
            if not ready:
                raise MechanismError("joints do not form a tree from the base link " + repr(base) + ": " + ", ".join(f"{j.name} ({j.parent} -> {j.child})" for j in pending))
            for j in ready:
                out.append(j)
                placed.add(j.child)
                pending.remove(j)
        loose = [n for n in self.links if n not in placed]
        if loose:
            raise MechanismError(f"link(s) {loose} are not connected to the base by any joint")
        return out

    @property
    def moving_joints(self) -> list[Joint]:
        return [j for j in self.order() if j.moving]

    def children_of(self, link: str) -> list[Joint]:
        return [j for j in self.joints if j.parent == link]

    def subtree(self, link: str) -> set[str]:
        out = {link}
        stack = [link]
        while stack:
            for j in self.children_of(stack.pop()):
                if j.child not in out:
                    out.add(j.child)
                    stack.append(j.child)
        return out

    def chain(self, link: str) -> list[Joint]:
        """The joints from the base to `link`."""
        by_child = {j.child: j for j in self.joints}
        out = []
        n = link
        while n in by_child:
            out.append(by_child[n])
            n = by_child[n].parent
        return list(reversed(out))

    def resolved_tip(self) -> tuple[str, np.ndarray]:
        """(link, point in its frame) of the tool point: declared, else the end of the last link of the longest chain."""
        if self.tip_link:
            return self.tip_link, (self.tip_at if self.tip_at is not None else self.links[self.tip_link].tip)
        order = self.order()
        if not order:
            return self.base, np.zeros(3)
        leaf = max((j.child for j in order), key=lambda n: len(self.chain(n)))
        return leaf, self.links[leaf].tip

    def joint_at(self, j: Joint) -> np.ndarray:
        """The joint's world position at the zero pose."""
        return self.zero_frames()[1][j.name]

    # -- kinematics ---------------------------------------------------------------
    def clamp(self, q: dict[str, float]) -> dict[str, float]:
        out = {}
        for j in self.moving_joints:
            v = float(q.get(j.name, j.home))
            out[j.name] = min(max(v, j.limit[0]), j.limit[1])
        return out

    def home_pose(self) -> dict[str, float]:
        return {j.name: j.home for j in self.moving_joints}

    def zero_frames(self) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        """At the zero pose: (world frame of every link, world position of every joint). A link's frame is its origin (its joint)
        and the rotation taking +x to its `direction`; the base link sits at the world origin."""
        W = {self.base: _rot_x_to(self.links[self.base].direction)}
        P: dict[str, np.ndarray] = {}
        for j in self.order():
            P[j.name] = j.at if j.at is not None else _apply(W[j.parent], self.links[j.parent].tip)
            W[j.child] = _trans(P[j.name]) @ _rot_x_to(self.links[j.child].direction)
        return W, P

    def transforms(self, q: dict[str, float] | None = None) -> dict[str, np.ndarray]:
        """The rigid motion (4x4, world -> world) every link has undergone at joint values q (degrees / mm; missing joints at
        home): the product of the motions of the joints from the base to it, each about its zero-pose world axis."""
        q = q or {}
        _W, P = self.zero_frames()
        out = {self.base: np.eye(4)}
        for j in self.order():
            v = float(q.get(j.name, j.home)) if j.moving else 0.0
            if j.type == "revolute":
                M = _rot_about(j.axis, v, P[j.name])
            elif j.type == "prismatic":
                M = _trans(j.axis * v)
            else:
                M = np.eye(4)
            out[j.child] = out[j.parent] @ M
        return out

    def points(self, q: dict[str, float] | None = None) -> dict[str, Any]:
        """World positions at a pose: joints, link ends and centres of mass, point masses, the tip."""
        W, P = self.zero_frames()
        T = self.transforms(q)
        pts: dict[str, Any] = {"joints": {}, "ends": {}, "cg": {}, "masses": {}, "axes": {}}
        for j in self.order():
            pts["joints"][j.name] = _apply(T[j.parent], P[j.name])
            pts["axes"][j.name] = T[j.parent][:3, :3] @ j.axis
        for name, l in self.links.items():
            pts["ends"][name] = _apply(T[name] @ W[name], l.tip)
            pts["cg"][name] = _apply(T[name] @ W[name], l.cg)
        tl, ta = self.resolved_tip()
        for pm in self.masses:
            link = pm.link or tl
            at = pm.at if pm.at is not None else (ta if (pm.kind == "payload" and not pm.link) else self.links[link].tip)
            pts["masses"][pm.name] = _apply(T[link] @ W[link], at)
        pts["tip"] = _apply(T[tl] @ W[tl], ta)
        return pts

    def tip_position(self, q: dict[str, float] | None = None) -> np.ndarray:
        tl, ta = self.resolved_tip()
        W, _P = self.zero_frames()
        return _apply(self.transforms(q)[tl] @ W[tl], ta)

    def __repr__(self) -> str:
        return f"Mechanism({self.name!r}: {len(self.links)} links, {len(self.joints)} joints, {len(self.targets)} targets)"


# --------------------------------------------------------------------------- transforms

def _trans(v: np.ndarray) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = v
    return m


def _rot(axis: np.ndarray, deg: float) -> np.ndarray:
    """Rotation by `deg` about the unit `axis` through the origin (Rodrigues)."""
    t = math.radians(deg)
    x, y, z = axis
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    m = np.eye(4)
    m[:3, :3] = np.eye(3) + math.sin(t) * k + (1 - math.cos(t)) * (k @ k)
    return m


def _rot_about(axis: np.ndarray, deg: float, origin: np.ndarray) -> np.ndarray:
    """Rotation by `deg` about the line through `origin` along unit `axis`."""
    m = _rot(axis, deg)
    m[:3, 3] = origin - m[:3, :3] @ origin
    return m


def _rot_x_to(direction: np.ndarray) -> np.ndarray:
    """The rotation that takes +x to `direction` (the shortest one; a reversal turns about z)."""
    d = direction / np.linalg.norm(direction)
    x = np.array([1.0, 0.0, 0.0])
    c = float(np.clip(d @ x, -1.0, 1.0))
    if c > 1 - 1e-12:
        return np.eye(4)
    if c < -1 + 1e-12:
        return _rot(np.array([0.0, 0.0, 1.0]), 180.0)
    axis = np.cross(x, d)
    axis /= np.linalg.norm(axis)
    return _rot(axis, math.degrees(math.acos(c)))


def _apply(m: np.ndarray, p) -> np.ndarray:
    return (m @ np.append(np.asarray(p, dtype=float), 1.0))[:3]


def is_mechanism(obj: Any) -> bool:
    return isinstance(obj, Mechanism)
