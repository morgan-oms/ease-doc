
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from analyzer.read_patterns_builder import resolve_entity_to_collection
from extractor.dupes_loader import (
    DEFAULT_POSSIBLES_DUPLICATION,
    DuplicationOp,
    parse_possibles_duplication,
)
from extractor.field_sizes_loader import (
    BSON_KEY_TYPE_OVERHEAD,
    _local_tag,
    _parse_entities,
    _parse_type_info,
    _primitive_value_size,
    _plist_estimated_value_size,
)

_COPY_LABEL_FULL = re.compile(
    r"Copy\s+(?P<source>[\w]+)\[(?P<fields>[^\]]*)\]\s*->\s*(?P<target>[\w.]+)",
    re.IGNORECASE,
)

_CAMEL_TO_SNAKE = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass(frozen=True)
class _AttributeMeta:
    value_bytes: int
    is_list: bool = False


def _normalize_token(value: str) -> str:
    return _CAMEL_TO_SNAKE.sub("_", value).replace(".", "_").lower()


def _build_attribute_index(nosql_schema_text: str) -> Dict[Tuple[str, str], _AttributeMeta]:
    root = ET.fromstring(nosql_schema_text)
    entities = _parse_entities(root)
    index: Dict[Tuple[str, str], _AttributeMeta] = {}

    for entity in entities:
        entity_key = entity.name.lower()
        for feature in entity.features:
            if _local_tag(feature) != "Attribute":
                continue
            name = feature.get("name")
            if not name:
                continue

            type_info = _parse_type_info(feature)
            if type_info is None:
                continue
            type_kind, primitive = type_info
            if type_kind == "primitive":
                value_size = _primitive_value_size(primitive, name)
                is_list = False
            elif type_kind == "plist":
                value_size = _plist_estimated_value_size(primitive, name)
                is_list = True
            else:
                value_size = 32
                is_list = False

            field_key = name.lower()
            meta = _AttributeMeta(value_bytes=value_size, is_list=is_list)
            index[(entity_key, field_key)] = meta
            index[(entity_key, _normalize_token(name))] = meta

    return index


def _parse_fields_from_label(label: str) -> tuple[str, ...]:
    match = _COPY_LABEL_FULL.search(label)
    if not match:
        return ()
    raw = match.group("fields")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _parse_op_from_label(label: str) -> Optional[DuplicationOp]:
    match = _COPY_LABEL_FULL.search(label)
    if not match:
        return None
    fields = _parse_fields_from_label(label)
    if not fields:
        return None
    note = None
    if "(" in label:
        note = label[label.index("(") + 1 : label.rindex(")")].strip()
    return DuplicationOp(
        source=match.group("source"),
        fields=fields,
        target=match.group("target").split(".")[0],
        note=note,
    )


def _load_duplication_ops(
    dupes: Dict[str, str],
    dupe_ids: List[str],
    duplication_path: Optional[Union[str, Path]] = None,
) -> List[Tuple[str, DuplicationOp]]:
    file_path = Path(duplication_path) if duplication_path is not None else DEFAULT_POSSIBLES_DUPLICATION
    parsed_ops = parse_possibles_duplication(file_path.read_text(encoding="utf-8"))

    paired: List[Tuple[str, DuplicationOp]] = []
    for index, dupe_id in enumerate(dupe_ids):
        if index < len(parsed_ops):
            paired.append((dupe_id, parsed_ops[index]))
            continue
        op = _parse_op_from_label(dupes.get(dupe_id, ""))
        if op is not None:
            paired.append((dupe_id, op))
    return paired


def _lookup_value_bytes(
    entity: str,
    field: str,
    attribute_index: Dict[Tuple[str, str], _AttributeMeta],
    field_sizes: Mapping[str, int],
) -> int:
    entity_key = entity.lower()
    field_variants = {
        field.lower(),
        _normalize_token(field),
    }

    for field_key in field_variants:
        meta = attribute_index.get((entity_key, field_key))
        if meta is not None:
            return meta.value_bytes

    bson_keys = field_sizes.keys() - {"bson_overhead"}
    for field_key in field_variants:
        for prefix in (f"{entity_key}_", ""):
            candidate = f"{prefix}{field_key}"
            if candidate in field_sizes:
                return int(field_sizes[candidate])

    for field_key in field_variants:
        if field_key in field_sizes:
            return int(field_sizes[field_key])

    for key in bson_keys:
        if key.endswith(f"_{field_key}") or key.endswith(field_key):
            return int(field_sizes[key])

    return int(field_sizes.get(_normalize_token(field), 32))


def _bson_overhead(field_sizes: Mapping[str, int]) -> int:
    return int(field_sizes.get("bson_overhead", BSON_KEY_TYPE_OVERHEAD))


def _scalar_copy_bytes(
    source_entity: str,
    fields: Iterable[str],
    attribute_index: Dict[Tuple[str, str], _AttributeMeta],
    field_sizes: Mapping[str, int],
) -> int:
    overhead = _bson_overhead(field_sizes)
    total = 0
    for field_name in fields:
        value_bytes = _lookup_value_bytes(
            source_entity, field_name, attribute_index, field_sizes
        )
        total += overhead + value_bytes
    return total


def _is_array_duplication(
    op: DuplicationOp,
    attribute_index: Dict[Tuple[str, str], _AttributeMeta],
) -> bool:
    if op.note and "array" in op.note.lower():
        return True
    entity_key = op.source.lower()
    for field_name in op.fields:
        meta = attribute_index.get((entity_key, field_name.lower()))
        if meta is None:
            meta = attribute_index.get((entity_key, _normalize_token(field_name)))
        if meta is not None and meta.is_list:
            return True
    return False


def _duplication_scale(
    volume_params: Optional[Mapping[str, Any]],
    dupe_id: str,
) -> float:
    if not volume_params:
        return 1.0

    nested = volume_params.get("duplication_scale")
    if isinstance(nested, Mapping) and dupe_id in nested:
        return float(nested[dupe_id])

    for key in (f"_scale_{dupe_id}", f"scale_{dupe_id}"):
        if key in volume_params:
            return float(volume_params[key])

    return 1.0


def _array_element_multiplier(
    volume_params: Optional[Mapping[str, Any]],
    dupe_id: str,
    source_col: str,
    target_col: str,
) -> float:
    if not volume_params:
        return 1.0

    nested = volume_params.get("duplication_array_size")
    if isinstance(nested, Mapping) and dupe_id in nested:
        return float(nested[dupe_id])

    for key in (
        f"_array_{dupe_id}",
        f"avg_{source_col}_per_{target_col}",
        f"avg_items_per_{target_col}",
    ):
        if key in volume_params:
            return float(volume_params[key])

    source_vol = float(volume_params.get(source_col, 0.0))
    target_vol = max(1.0, float(volume_params.get(target_col, 1.0)))
    if source_vol > 0:
        return source_vol / target_vol

    return 1.0


def _list_embedding_bytes(
    element_count: float,
    fields_value_sum: int,
    field_sizes: Mapping[str, int],
) -> int:
    overhead = _bson_overhead(field_sizes)
    per_item = overhead + fields_value_sum
    return int(element_count * per_item)


def _fields_value_sum_only(
    source_entity: str,
    fields: Iterable[str],
    attribute_index: Dict[Tuple[str, str], _AttributeMeta],
    field_sizes: Mapping[str, int],
) -> int:
    return sum(
        _lookup_value_bytes(source_entity, field_name, attribute_index, field_sizes)
        for field_name in fields
    )


def derive_avg_doc_sizes(
    base_sizes: Dict[str, int],
    flags: Dict[str, bool],
    dupes: Dict[str, str],
    dupe_ids: List[str],
    field_sizes: Mapping[str, int],
    *,
    volume_params: Optional[Mapping[str, Any]] = None,
    nosql_schema_path: Optional[Union[str, Path]] = None,
    duplication_path: Optional[Union[str, Path]] = None,
    attribute_index: Optional[Dict[Tuple[str, str], _AttributeMeta]] = None,
) -> Dict[str, int]:

    sizes = dict(base_sizes)
    known_collections: Set[str] = set(base_sizes.keys())
    if volume_params:
        for key, value in volume_params.items():
            if isinstance(key, str) and not key.startswith(("_", "max_", "n_", "avg_")):
                try:
                    float(value)
                    known_collections.add(key)
                except (TypeError, ValueError):
                    pass

    if attribute_index is None:
        from extractor.field_sizes_loader import DEFAULT_NOSQL_SCHEMA_XMI

        schema_path = Path(nosql_schema_path) if nosql_schema_path else DEFAULT_NOSQL_SCHEMA_XMI
        attribute_index = _build_attribute_index(schema_path.read_text(encoding="utf-8"))

    for dupe_id, op in _load_duplication_ops(dupes, dupe_ids, duplication_path):
        if not flags.get(dupe_id):
            continue

        source_col = resolve_entity_to_collection(op.source, known_collections)
        target_col = resolve_entity_to_collection(op.target, known_collections)
        if not source_col or not target_col:
            continue

        scale = _duplication_scale(volume_params, dupe_id)

        if _is_array_duplication(op, attribute_index):
            element_count = _array_element_multiplier(
                volume_params, dupe_id, source_col, target_col
            )
            values_sum = _fields_value_sum_only(
                op.source, op.fields, attribute_index, field_sizes
            )
            increment = _list_embedding_bytes(element_count, values_sum, field_sizes)
        else:
            increment = _scalar_copy_bytes(
                op.source, op.fields, attribute_index, field_sizes
            )

        sizes[target_col] = sizes.get(target_col, 0) + int(increment * scale)

    return sizes
