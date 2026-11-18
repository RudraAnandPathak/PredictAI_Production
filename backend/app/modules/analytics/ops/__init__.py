"""The closed vocabulary of analytical operations. Anything a plan can ask for is registered here; nothing else can run."""
from __future__ import annotations

from dataclasses import replace
from typing import Dict

from ..plan import OpSpec
from . import charts, descriptive, hypothesis, mixed, relationships, temporal

REGISTRY: Dict[str, OpSpec] = {}
for module in (descriptive, relationships, hypothesis, mixed, temporal, charts):
    for spec in module.SPECS:
        if spec.name in REGISTRY:
            raise RuntimeError(f"duplicate operation {spec.name}")
        REGISTRY[spec.name] = spec
