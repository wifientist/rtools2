"""
WiredWiz check engine — the rules a senior ICX engineer would run by hand.

Importing this package registers every check. See framework.REGISTRY.
"""

from . import (config_checks, coverage_checks, flooding_checks,  # noqa: F401
               hardware_checks, metric_checks, poe_checks, scale_checks)
from .framework import REGISTRY, Check, CheckContext, Finding, IcxConfig, run_checks
from .run import run_health_check

__all__ = ["REGISTRY", "Check", "CheckContext", "Finding", "IcxConfig",
           "run_checks", "run_health_check"]
