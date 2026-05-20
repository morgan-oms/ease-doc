"""Módulo Seletor do EASE-Doc."""

from selector.flags import (
    copy_flags,
    flags_to_key,
    flags_to_schema_name,
    generate_neighbors,
    make_base_flags,
)
from selector.hill_climbing import SchemaEvaluator, greedy_hill_climb

__all__ = [
    "SchemaEvaluator",
    "copy_flags",
    "flags_to_key",
    "flags_to_schema_name",
    "generate_neighbors",
    "greedy_hill_climb",
    "make_base_flags",
]
