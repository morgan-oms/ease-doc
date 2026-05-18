
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

QUERY_BLOCK_RE = re.compile(
    r"@subContainer\.(\d+)/@codeContainer\.0/@codeBlock\.(\d+)"
)


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


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


def _is_query_file(filename: str) -> bool:
    return "query" in filename.lower()


def _is_read_query_function(function_name: str) -> bool:
    lowered = function_name.lower()
    return not any(
        lowered.startswith(prefix)
        for prefix in ("create", "update", "delete", "validate")
    )


def _extract_query_block_key(statement: str) -> Optional[tuple[int, int]]:
    match = QUERY_BLOCK_RE.search(statement or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))

DEFAULT_DBO_SCHEMA_XMI = (
    Path(__file__).resolve().parent.parent
    / "uschema-code-analysis"
    / "es.um.uschema.code.transfs.launcher"
    / "outputs"
    / "dboSchema"
    / "dboSchemaModel.xmi"
)

_LITERAL_VALUE_RE = re.compile(r'literal="&quot;([^&]+)&quot;"')
_COLLECTION_CALL_RE = re.compile(r'collection\(&quot;([^&]+)&quot;\)')


@dataclass(frozen=True)
class QueryStructure:
    query_id: str
    main_collection: Optional[str]
    lookup_collections: tuple[str, ...]


def _first_literal_matching(element: ET.Element, name: str) -> Optional[str]:
    for node in element.iter():
        if _local_tag(node) != "properties" or node.get("name") != name:
            continue
        for child in node.iter():
            if _local_tag(child) != "Initialization":
                continue
            literal = child.get("literal", "")
            match = _LITERAL_VALUE_RE.search(literal)
            if match:
                return match.group(1)
    return None


def _extract_main_collection(block: ET.Element) -> Optional[str]:
    for node in block.iter():
        if _local_tag(node) != "Initialization":
            continue
        literal = node.get("literal", "")
        match = _COLLECTION_CALL_RE.search(literal)
        if match:
            return match.group(1)
    return None


def _extract_lookup_collections(block: ET.Element) -> List[str]:
    lookups: List[str] = []
    for node in block.iter():
        if _local_tag(node) != "dataContainer" or node.get("name") != "$lookup":
            continue
        from_collection = _first_literal_matching(node, "from")
        if from_collection:
            lookups.append(from_collection)
    return lookups


def parse_query_structures(
    text: str,
    *,
    query_id_prefix: str = "Q",
) -> Dict[str, QueryStructure]:
    root = ET.fromstring(text)
    structures: Dict[str, QueryStructure] = {}
    current_file = ""
    blocks: List[tuple[ET.Element, int]] = []

    for element in root.iter():
        tag = _local_tag(element)
        if tag == "subContainer":
            name = element.get("name", "")
            current_file = name if name else current_file
        elif tag == "CallableBlock" and _is_query_file(current_file):
            function_name = element.get("functionName") or element.get("name") or ""
            if not function_name or not _is_read_query_function(function_name):
                continue
            parameters = element.get("parameters", "")
            if _extract_query_block_key(parameters) is None:
                continue
            blocks.append((element, _min_source_line(element)))

    blocks.sort(key=lambda item: item[1])
    for query_num, (element, _) in enumerate(blocks, start=1):
        query_id = f"{query_id_prefix}{query_num}"
        structures[query_id] = QueryStructure(
            query_id=query_id,
            main_collection=_extract_main_collection(element),
            lookup_collections=tuple(_extract_lookup_collections(element)),
        )

    return structures


def load_query_structures(
    path: Optional[Union[str, Path]] = None,
    *,
    query_id_prefix: str = "Q",
) -> Dict[str, QueryStructure]:
    file_path = Path(path) if path is not None else DEFAULT_DBO_SCHEMA_XMI
    return parse_query_structures(file_path.read_text(encoding="utf-8"), query_id_prefix=query_id_prefix)
