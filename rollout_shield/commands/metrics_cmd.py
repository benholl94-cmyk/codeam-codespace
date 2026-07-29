"""Quick check for the metrics command."""

import argparse

from ..state import State


def cmd_metrics(state: State, args: argparse.Namespace) -> int:
    """Print the Prometheus-format metrics snapshot."""
    from .. import metrics as _metrics
    print(_metrics.render(), end="")
    return 0
