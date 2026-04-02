"""
GNSS package: native serial control for F9P (and future LG290P).

Uses direct serial I/O instead of an external relay/UI stack.
"""

from .manager import gnss_manager
from .state import GNSSState

# Backward-compatible access to gnss_state (used by routes, WS, etc.)
gnss_state = gnss_manager.state

__all__ = ["gnss_manager", "gnss_state", "GNSSState"]
