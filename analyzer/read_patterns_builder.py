"""
Ajusta BASE_READ_PATTERNS conforme duplicações ativas (flags + dupes).

O carregamento do padrão base a partir do XMI fica em extractor.read_patterns_loader.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from analyzer.dbo_query_structure import QueryStructure, load_query_structures

_COPY_LABEL_RE = re.compile(
    r"Copy\s+(?P<source>[\w]+)\[[^\]]*\]\s*->\s*(?P<target>[\w.]+)",
    re.IGNORECASE,
)


def remove_once(path: List[Tuple[str, int]], collection: str) -> None:
    for idx, (name, _) in enumerate(path):
        if name == collection:
            del path[idx]
            return


def parse_duplication_endpoints(label: str) -> Optional[Tuple[str, str]]:
    match = _COPY_LABEL_RE.search(label)
    if not match:
        return None
    return match.group("source"), match.group("target").split(".")[0]


def resolve_entity_to_collection(
    entity: str,
    known_collections: set[str],
) -> Optional[str]:
    if not entity:
        return None

    entity_lower = entity.lower()
    if entity in known_collections:
        return entity
    if entity_lower in known_collections:
        return entity_lower

    for collection in known_collections:
        col_lower = collection.lower()
        if col_lower == entity_lower or col_lower == entity_lower + "s":
            return collection
        if entity_lower.endswith("s") and col_lower == entity_lower[:-1]:
            return collection
        if col_lower.endswith("s") and col_lower[:-1] == entity_lower:
            return collection

    return None


def _should_remove_source_access(
    *,
    source_col: str,
    target_col: str,
    path_columns: List[str],
    query_structure: Optional[QueryStructure],
) -> bool:
    if source_col in path_columns and target_col in path_columns:
        return True

    if query_structure is None:
        return False

    main = query_structure.main_collection
    lookups = set(query_structure.lookup_collections)

    if main == target_col and source_col in lookups:
        return True
    if target_col in path_columns and source_col in lookups:
        return True
    if main == target_col and source_col in path_columns:
        return True

    return False


def build_read_patterns(
    base_patterns: Dict[str, List[Tuple[str, int]]],
    flags: Dict[str, bool],
    dupes: Dict[str, str],
    dupe_ids: List[str],
    *,
    dbo_schema_path: Optional[Union[str, Path]] = None,
    query_structures: Optional[Dict[str, QueryStructure]] = None,
) -> Dict[str, List[Tuple[str, int]]]:

    patterns = {query_id: list(accesses) for query_id, accesses in base_patterns.items()}
    known_collections = {name for path in patterns.values() for name, _ in path}

    structures = query_structures
    if structures is None and dbo_schema_path is not None:
        structures = load_query_structures(dbo_schema_path)
    elif structures is None:
        try:
            structures = load_query_structures()
        except OSError:
            structures = {}

    for dupe_id in dupe_ids:
        if not flags.get(dupe_id):
            continue

        label = dupes.get(dupe_id, "")
        endpoints = parse_duplication_endpoints(label)
        if endpoints is None:
            continue

        source_col = resolve_entity_to_collection(endpoints[0], known_collections)
        target_col = resolve_entity_to_collection(endpoints[1], known_collections)
        if not source_col or not target_col:
            continue

        for query_id, path in patterns.items():
            path_columns = [name for name, _ in path]
            query_structure = structures.get(query_id) if structures else None
            if _should_remove_source_access(
                source_col=source_col,
                target_col=target_col,
                path_columns=path_columns,
                query_structure=query_structure,
            ):
                remove_once(path, source_col)

    return patterns


def build_read_patterns_from_extractor(
    base_patterns: Dict[str, List[Tuple[str, int]]],
    flags: Dict[str, bool],
    dupes: Dict[str, str],
    dupe_ids: List[str],
    *,
    dbo_schema_path: Optional[Union[str, Path]] = None,
) -> Dict[str, List[Tuple[str, int]]]:

    return build_read_patterns(
        base_patterns,
        flags,
        dupes,
        dupe_ids,
        dbo_schema_path=dbo_schema_path,
    )
