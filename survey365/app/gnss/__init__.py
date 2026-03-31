"""
GNSS package: native serial control for F9P (and future LG290P).

Replaces the old TCP:5015 → str2str → RTKBase chain with direct serial I/O.
"""

from .manager import gnss_manager
from .state import GNSSState

# Backward-compatible access to gnss_state (used by routes, WS, etc.)
gnss_state = gnss_manager.state

__all__ = ["gnss_manager", "gnss_state", "GNSSState"]
