# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Registry of known target types. Add a new target type by importing its
class and adding it to ``_TARGETS`` — nothing else in the codebase changes."""

from __future__ import annotations

from typing import Dict, List

from .a2a import A2aTarget
from .base import TargetType
from .http_agent import HttpAgentTarget
from .inference import InferenceTarget
from .mcp import HttpMcpTarget, McpTarget

_TARGETS: Dict[str, TargetType] = {
    t.type_key: t
    for t in (
        McpTarget(),
        HttpMcpTarget(),
        A2aTarget(),
        InferenceTarget(),
        HttpAgentTarget(),
    )
}


def target_types() -> List[str]:
    """All registered target-type keys."""
    return list(_TARGETS)


def get_target(type_key: str) -> TargetType:
    """Look up a target type by key, raising a clear error if unknown."""
    try:
        return _TARGETS[type_key]
    except KeyError:
        raise ValueError(
            f"unknown endpoint type {type_key!r}; "
            f"valid types: {', '.join(target_types())}"
        )
