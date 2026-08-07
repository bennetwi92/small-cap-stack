"""Import a module from ``spikes/`` by name, for the handful of spike helpers worth testing.

``spikes/`` is deliberately outside the package (it is exempt from mypy, and nothing in
``src/small_cap_stack`` may import it), so a plain ``import`` does not reach it. This loads one by
path instead, and caches it in ``sys.modules`` under its bare name so a spike that imports a sibling
— ``massive_calibration`` imports ``scanner_reconstruct`` — resolves it the same way the CLI does.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SPIKES = Path(__file__).resolve().parent.parent / "spikes"


def load_spike(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    if str(SPIKES) not in sys.path:
        sys.path.insert(0, str(SPIKES))
    spec = importlib.util.spec_from_file_location(name, SPIKES / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - a missing spike is a typo
        raise ImportError(f"no spike named {name} in {SPIKES}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
