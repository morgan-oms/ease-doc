
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, Union

from analyzer.dbo_reference_graph import ReferenceGraph, load_reference_graph
from analyzer.read_patterns_builder import (
    parse_duplication_endpoints,
    resolve_entity_to_collection,
)

WriteStep = Dict[str, Union[str, int, float]]
WritePattern = Dict[str, List[WriteStep]]


def _float_volumes(volume_params: Mapping[str, Any]) -> Dict[str, float]:
    volumes: Dict[str, float] = {}
    for key, value in volume_params.items():
        if key.startswith("max_") or key.startswith("n_") or key.startswith("avg_"):
            continue
        try:
            volumes[key] = float(value)
        except (TypeError, ValueError):
            continue
    return volumes


def compute_collection_ratios(
    volume_params: Mapping[str, Any],
    reference_graph: ReferenceGraph,
) -> Dict[Tuple[str, str], float]:

    volumes = _float_volumes(volume_params)
    ratios: Dict[Tuple[str, str], float] = {}
    for child, parent in reference_graph.edges:
        if parent not in volumes or child not in volumes:
            continue
        parent_vol = max(1.0, volumes[parent])
        ratios[(parent, child)] = volumes[child] / parent_vol
    return ratios


def propagation_writes(
    source_col: str,
    target_col: str,
    volume_params: Mapping[str, Any],
    reference_graph: ReferenceGraph,
) -> float:
    volumes = _float_volumes(volume_params)
    return reference_graph.ratio_target_per_source(source_col, target_col, volumes)


def _primary_create_root(steps: List[WriteStep]) -> Optional[str]:
    for step in steps:
        if int(step.get("writes", 0)) > 0:
            return str(step["collection"])
    return None


def enrichment_reads(
    source_col: str,
    target_col: str,
    op_steps: List[WriteStep],
    volume_params: Mapping[str, Any],
    reference_graph: ReferenceGraph,
) -> Optional[Tuple[float, float]]:

    volumes = _float_volumes(volume_params)
    collections_in_op = {str(step["collection"]) for step in op_steps}
    if target_col not in collections_in_op:
        return None
    if source_col in collections_in_op:
        return None

    write_targets = {str(step["collection"]) for step in op_steps if int(step.get("writes", 0)) > 0}
    create_root = _primary_create_root(op_steps)
    if len(write_targets) > 1 and target_col == create_root:
        return (0.0, 0.0)

    if reference_graph.references(target_col, source_col):
        return (1.0, 0.0)

    for parent_col in write_targets:
        if reference_graph.references(target_col, parent_col):
            parent_vol = max(1.0, volumes.get(parent_col, 1.0))
            target_vol = float(volumes.get(target_col, 0.0))
            return (target_vol / parent_vol, 0.0)

    reads = reference_graph.ratio_target_per_source(source_col, target_col, volumes)
    return (reads, 0.0)


def _append_step(steps: List[WriteStep], collection: str, reads: float, writes: float) -> None:
    steps.append(
        {
            "collection": collection,
            "reads": reads,
            "writes": writes,
        }
    )


def _op_kind(op_id: str) -> str:
    if not op_id:
        return ""
    return op_id[0].upper()


def _primary_write_collections(steps: List[WriteStep]) -> Set[str]:
    return {str(step["collection"]) for step in steps if int(step.get("writes", 0)) > 0}


def build_write_patterns(
    base_patterns: WritePattern,
    flags: Dict[str, bool],
    dupes: Dict[str, str],
    dupe_ids: List[str],
    volume_params: Mapping[str, Any],
    *,
    known_collections: Optional[Set[str]] = None,
    reference_graph: Optional[ReferenceGraph] = None,
    dbo_schema_path: Optional[Union[str, Path]] = None,
) -> WritePattern:
    patterns: WritePattern = {
        op_id: [dict(step) for step in steps] for op_id, steps in base_patterns.items()
    }

    resolved_collections: Set[str] = set(known_collections or ())
    resolved_collections.update(
        str(step["collection"])
        for steps in base_patterns.values()
        for step in steps
    )
    resolved_collections.update(_float_volumes(volume_params).keys())

    graph = reference_graph
    if graph is None:
        graph = (
            load_reference_graph(dbo_schema_path)
            if dbo_schema_path is not None
            else load_reference_graph()
        )

    resolved_collections.update(graph.collections)

    for dupe_id in dupe_ids:
        if not flags.get(dupe_id):
            continue

        label = dupes.get(dupe_id, "")
        endpoints = parse_duplication_endpoints(label)
        if endpoints is None:
            continue

        source_col = resolve_entity_to_collection(endpoints[0], resolved_collections)
        target_col = resolve_entity_to_collection(endpoints[1], resolved_collections)
        if not source_col or not target_col:
            continue

        for op_id, steps in patterns.items():
            kind = _op_kind(op_id)
            write_targets = _primary_write_collections(steps)

            if kind == "U" and source_col in write_targets:
                writes = propagation_writes(
                    source_col, target_col, volume_params, graph
                )
                _append_step(steps, target_col, reads=0.0, writes=writes)
                continue

            if kind == "C":
                enrichment = enrichment_reads(
                    source_col, target_col, steps, volume_params, graph
                )
                if enrichment is not None:
                    reads, writes = enrichment
                    _append_step(steps, target_col, reads=reads, writes=writes)

    return patterns
