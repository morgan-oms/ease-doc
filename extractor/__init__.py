"""Módulo Extrator do EASE-Doc."""

from extractor.collections_loader import load_base_collections, parse_base_collections
from extractor.config_loader import load_base_schema, load_config, load_volumes, load_workload
from extractor.field_sizes_loader import load_avg_doc_sizes, load_fields
from extractor.read_patterns_loader import load_base_read_patterns, parse_base_read_patterns
from extractor.write_patterns_loader import load_base_write_patterns, parse_base_write_patterns
from extractor.dupes_loader import (
    DEFAULT_POSSIBLES_DUPLICATION,
    DuplicationOp,
    format_dupe_label,
    load_dupes,
    parse_possibles_duplication,
)

__all__ = [
    "DEFAULT_POSSIBLES_DUPLICATION",
    "DuplicationOp",
    "format_dupe_label",
    "load_avg_doc_sizes",
    "load_base_collections",
    "load_base_read_patterns",
    "load_base_write_patterns",
    "load_base_schema",
    "parse_base_read_patterns",
    "parse_base_write_patterns",
    "load_config",
    "load_dupes",
    "load_fields",
    "load_volumes",
    "load_workload",
    "parse_base_collections",
    "parse_possibles_duplication",
]
