"""Synthetic input generation.

Images produced here are *inputs*, never results. The pipeline that assesses
them is the same one used for real photographs, so a synthetic demo exercises
real code paths and its findings are genuinely computed.

Nothing in this package is presented as a real vehicle. The report page labels
synthetic captures, and ``SyntheticPair.describe()`` carries the ground truth so
a demo can show what was planted next to what was found.
"""

from __future__ import annotations

from ai.synthetic.vehicle_photos import (
    DEMO_SCENARIOS,
    SyntheticDefect,
    SyntheticPair,
    build_scenario,
    generate_pair,
    render_panel,
    scenario_names,
)

__all__ = [
    "DEMO_SCENARIOS",
    "SyntheticDefect",
    "SyntheticPair",
    "build_scenario",
    "generate_pair",
    "render_panel",
    "scenario_names",
]
