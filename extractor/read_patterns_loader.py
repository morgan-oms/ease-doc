
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

DEFAULT_DBO_SCHEMA_XMI = (
    Path(__file__).resolve().parent.parent
    / "uschema-code-analysis"
    / "es.um.uschema.code.transfs.launcher"
    / "outputs"
    / "dboSchema"
    / "dboSchemaModel.xmi"
)

CONTAINER_REF_RE = re.compile(r"/2/@containers\.(\d+)")
QUERY_BLOCK_RE = re.compile(
    r"@subContainer\.(\d+)/@codeContainer\.0/@codeBlock\.(\d+)"
)


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _resolve_container_name(container_ref: str, container_names: List[str]) -> Optional[str]:
    match = CONTAINER_REF_RE.search(container_ref or "")
    if not match:
        return None
    index = int(match.group(1))
    if 0 <= index < len(container_names):
        return container_names[index]
    return None


def _extract_query_block_key(statement: str) -> Optional[Tuple[int, int]]:
    match = QUERY_BLOCK_RE.search(statement or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _is_query_file(filename: str) -> bool:
    return "query" in filename.lower()


def _is_read_query_function(function_name: str) -> bool:
    lowered = function_name.lower()
    return not any(
        lowered.startswith(prefix)
        for prefix in ("create", "update", "delete", "validate")
    )


def _parse_container_names(dbo_root: ET.Element) -> List[str]:
    names: List[str] = []
    for element in dbo_root.iter():
        if _local_tag(element) != "containers":
            continue
        name = element.get("name")
        names.append(name if name else f"__container_{len(names)}__")
    return names


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


def _parse_query_blocks(code_root: ET.Element) -> List[Tuple[int, int, str]]:
   
    blocks: List[Tuple[Tuple[int, int, str], int]] = []
    current_file = ""

    for element in code_root.iter():
        tag = _local_tag(element)
        if tag == "subContainer":
            name = element.get("name", "")
            current_file = name if name else current_file
        elif tag == "CallableBlock" and _is_query_file(current_file):
            function_name = element.get("functionName") or element.get("name") or ""
            if not function_name or not _is_read_query_function(function_name):
                continue
            parameters = element.get("parameters", "")
            block_key = _extract_query_block_key(parameters)
            if block_key is not None:
                blocks.append(
                    ((block_key[0], block_key[1], function_name), _min_source_line(element))
                )

    blocks.sort(key=lambda item: item[1])
    return [item[0] for item in blocks]


def _parse_read_operations(dbo_root: ET.Element) -> List[Tuple[Tuple[int, int], str]]:
    reads: List[Tuple[Tuple[int, int], str]] = []
    for element in dbo_root.iter():
        if _local_tag(element) != "databaseOperations":
            continue
        xsi_type = element.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
        if not xsi_type.endswith(":Read"):
            continue
        statement = element.get("statement", "")
        block_key = _extract_query_block_key(statement)
        container_ref = element.get("container", "")
        if block_key is not None and container_ref:
            reads.append((block_key, container_ref))
    return reads


def _find_dbo_schema_root(root: ET.Element) -> Optional[ET.Element]:
    for element in root.iter():
        if _local_tag(element) == "DatabaseOperationsSchema":
            return element
    return None


def parse_base_read_patterns(
    text: str,
    *,
    query_id_prefix: str = "Q",
) -> Dict[str, List[Tuple[str, int]]]:

    root = ET.fromstring(text)
    dbo_root = _find_dbo_schema_root(root)
    if dbo_root is None:
        raise ValueError("dboSchemaModel.xmi inválido: DatabaseOperationsSchema não encontrado.")

    container_names = _parse_container_names(dbo_root)
    query_blocks = _parse_query_blocks(root)
    read_ops = _parse_read_operations(dbo_root)

    reads_by_block: Dict[Tuple[int, int], List[str]] = {}
    for block_key, container_ref in read_ops:
        collection = _resolve_container_name(container_ref, container_names)
        if collection is None:
            continue
        reads_by_block.setdefault(block_key, []).append(collection)

    patterns: Dict[str, List[Tuple[str, int]]] = OrderedDict()
    query_num = 1
    for block_key, _function_name in query_blocks:
        collections = reads_by_block.get(block_key, [])
        if not collections:
            continue
        ordered_counts: OrderedDict[str, int] = OrderedDict()
        for collection in collections:
            ordered_counts[collection] = ordered_counts.get(collection, 0) + 1
        patterns[f"{query_id_prefix}{query_num}"] = [
            (name, count) for name, count in ordered_counts.items()
        ]
        query_num += 1

    return patterns


def load_base_read_patterns(
    path: Optional[Union[str, Path]] = None,
    *,
    query_id_prefix: str = "Q",
) -> Dict[str, List[Tuple[str, int]]]:
    file_path = Path(path) if path is not None else DEFAULT_DBO_SCHEMA_XMI
    text = file_path.read_text(encoding="utf-8")
    return parse_base_read_patterns(text, query_id_prefix=query_id_prefix)
