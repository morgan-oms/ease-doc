"""
Extrai BASE_WRITE_PATTERNS do dboSchemaModel.xmi (operações Update, Insert, Delete).

Cada CallableBlock de escrita no controller vira U1, C1, D1, … na ordem do código-fonte.
Ajustes por duplicação ficam no analyzer (futuro write_patterns_builder).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

DEFAULT_DBO_SCHEMA_XMI = (
    Path(__file__).resolve().parent.parent
    / "uschema-code-analysis"
    / "es.um.uschema.code.transfs.launcher"
    / "outputs"
    / "dboSchema"
    / "dboSchemaModel.xmi"
)

CONTAINER_REF_RE = re.compile(r"/2/@containers\.(\d+)")
CODE_BLOCK_PREFIX_RE = re.compile(
    r"(@subContainer\.\d+/@codeContainer\.0/@codeBlock\.\d+)"
)

_WRITE_TYPE_PREFIX = {
    "Update": "U",
    "Insert": "C",
    "Delete": "D",
}


@dataclass
class _WriteOperationGroup:
    block_prefix: str
    write_kind: str
    min_line: int
    accesses: OrderedDict[str, Dict[str, int]] = field(default_factory=OrderedDict)


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _xsi_type_name(element: ET.Element) -> str:
    xsi_type = element.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
    if ":" in xsi_type:
        return xsi_type.split(":", 1)[-1]
    return xsi_type


def _parse_container_names(dbo_root: ET.Element) -> List[str]:
    names: List[str] = []
    for element in dbo_root.iter():
        if _local_tag(element) != "containers":
            continue
        name = element.get("name")
        names.append(name if name else f"__container_{len(names)}__")
    return names


def _resolve_container_name(container_ref: str, container_names: List[str]) -> Optional[str]:
    match = CONTAINER_REF_RE.search(container_ref or "")
    if not match:
        return None
    index = int(match.group(1))
    if 0 <= index < len(container_names):
        return container_names[index]
    return None


def _extract_block_prefix(statement: str) -> Optional[str]:
    match = CODE_BLOCK_PREFIX_RE.search(statement or "")
    return match.group(1) if match else None


def _min_source_line(element: ET.Element) -> int:
    lines: List[int] = []
    for node in element.iter():
        line_attr = node.get("line")
        if line_attr is None:
            continue
        try:
            lines.append(int(line_attr))
        except ValueError:
            continue
    return min(lines) if lines else 10**9


def _is_write_controller_file(filename: str) -> bool:
    lower = filename.lower()
    if "query" in lower or "validator" in lower:
        return False
    return lower.endswith(".json")


def _write_kind_from_function(function_name: str) -> Optional[str]:
    lowered = function_name.lower()
    if lowered.startswith(("create", "insert", "add")):
        return "Insert"
    if lowered.startswith("update"):
        return "Update"
    if lowered.startswith(("delete", "remove")):
        return "Delete"
    return None


def _touch_access(
    accesses: OrderedDict[str, Dict[str, int]],
    collection: str,
    *,
    reads: int = 0,
    writes: int = 0,
) -> None:
    current = accesses.get(collection, {"reads": 0, "writes": 0})
    if reads:
        current["reads"] = max(current["reads"], reads)
    if writes:
        current["writes"] = max(current["writes"], writes)
    accesses[collection] = current


def _find_dbo_schema_root(root: ET.Element) -> Optional[ET.Element]:
    for element in root.iter():
        if _local_tag(element) == "DatabaseOperationsSchema":
            return element
    return None


def _collect_write_block_metadata(
    root: ET.Element,
) -> Dict[str, Tuple[int, str]]:
    metadata: Dict[str, Tuple[int, str]] = {}
    current_file = ""

    for element in root.iter():
        tag = _local_tag(element)
        if tag == "subContainer":
            name = element.get("name", "")
            current_file = name if name else current_file
        elif tag == "CallableBlock" and _is_write_controller_file(current_file):
            function_name = element.get("functionName") or element.get("name") or ""
            write_kind = _write_kind_from_function(function_name)
            if write_kind is None:
                continue
            prefix = _extract_block_prefix(element.get("parameters", ""))
            if prefix is None:
                continue
            line = _min_source_line(element)
            if prefix not in metadata:
                metadata[prefix] = (line, write_kind)
            else:
                prev_line, prev_kind = metadata[prefix]
                metadata[prefix] = (min(line, prev_line), prev_kind)

    return metadata


def _ingest_database_operations(
    dbo_root: ET.Element,
    container_names: List[str],
) -> Dict[str, _WriteOperationGroup]:
    groups: Dict[str, _WriteOperationGroup] = {}

    for element in dbo_root.iter():
        if _local_tag(element) != "databaseOperations":
            continue

        kind = _xsi_type_name(element)
        if kind not in _WRITE_TYPE_PREFIX and kind != "Read":
            continue

        statement = element.get("statement", "")
        block_prefix = _extract_block_prefix(statement)
        container_ref = element.get("container", "")
        collection = _resolve_container_name(container_ref, container_names)
        if not block_prefix or not collection:
            continue

        if block_prefix not in groups:
            groups[block_prefix] = _WriteOperationGroup(
                block_prefix=block_prefix,
                write_kind=kind if kind in _WRITE_TYPE_PREFIX else "Update",
                min_line=10**9,
            )

        group = groups[block_prefix]

        if kind == "Read":
            _touch_access(group.accesses, collection, reads=1, writes=0)
        elif kind == "Insert":
            _touch_access(group.accesses, collection, reads=0, writes=1)
        elif kind == "Delete":
            _touch_access(group.accesses, collection, reads=1, writes=1)
        elif kind == "Update":
            _touch_access(group.accesses, collection, reads=1, writes=1)

    return groups


def _groups_with_writes(
    groups: Dict[str, _WriteOperationGroup],
    block_metadata: Dict[str, Tuple[int, str]],
) -> List[_WriteOperationGroup]:
    result: List[_WriteOperationGroup] = []
    for prefix, group in groups.items():
        has_write = any(entry["writes"] > 0 for entry in group.accesses.values())
        if not has_write:
            continue
        meta = block_metadata.get(prefix)
        if meta is not None:
            group.min_line, group.write_kind = meta
        else:
            group.min_line = 10**9
        result.append(group)
    result.sort(key=lambda item: item.min_line)
    return result


def _assign_operation_ids(
    groups: List[_WriteOperationGroup],
) -> Dict[str, List[Dict[str, int]]]:
    counters = dict.fromkeys(_WRITE_TYPE_PREFIX.values(), 0)
    patterns: Dict[str, List[Dict[str, int]]] = OrderedDict()

    for group in groups:
        type_prefix = _WRITE_TYPE_PREFIX.get(group.write_kind)
        if not type_prefix:
            continue
        counters[type_prefix] += 1
        op_id = f"{type_prefix}{counters[type_prefix]}"
        patterns[op_id] = [
            {
                "collection": collection,
                "reads": access["reads"],
                "writes": access["writes"],
            }
            for collection, access in group.accesses.items()
        ]

    return patterns


def parse_base_write_patterns(text: str) -> Dict[str, List[Dict[str, Any]]]:
    root = ET.fromstring(text)
    dbo_root = _find_dbo_schema_root(root)
    if dbo_root is None:
        raise ValueError("dboSchemaModel.xmi inválido: DatabaseOperationsSchema não encontrado.")

    container_names = _parse_container_names(dbo_root)
    block_metadata = _collect_write_block_metadata(root)
    groups = _ingest_database_operations(dbo_root, container_names)
    ordered_groups = _groups_with_writes(groups, block_metadata)
    return _assign_operation_ids(ordered_groups)


def load_base_write_patterns(
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    file_path = Path(path) if path is not None else DEFAULT_DBO_SCHEMA_XMI
    return parse_base_write_patterns(file_path.read_text(encoding="utf-8"))
