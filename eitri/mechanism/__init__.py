"""Mechanisms: links, joints, actuators and targets, checked as a machine (the third domain of eitri).

A mechanism model file has the same contract as a CAD or harness model, but build(p) returns a Mechanism:

    from eitri.mechanism import *
"""
from __future__ import annotations

from ..model import Checker, Params                                              # noqa: F401  (verify(m, p, c) uses c)
from .checks import JointInfo, TargetInfo, joint_loads, mass_rollup, run_checks, sample_poses, solve_ik   # noqa: F401
from .model import (ACTUATOR_CATALOG, ASSUMED_RATED_FRACTION, G, KGCM_PER_NM, Actuator, catalog_actuator, Joint, Link, Mechanism, MechanismError, PointMass, Target,   # noqa: F401
                    is_mechanism)

__all__ = ["Mechanism", "Link", "Joint", "Actuator", "PointMass", "Target", "MechanismError", "joint_loads", "sample_poses",
           "solve_ik", "mass_rollup", "G", "KGCM_PER_NM"]
