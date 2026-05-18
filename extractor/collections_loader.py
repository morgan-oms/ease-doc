
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Union

DEFAULT_NOSQL_SCHEMA_XMI = (
    Path(__file__).resolve().parent.parent
    / "uschema-code-analysis"
    / "es.um.uschema.code.transfs.launcher"
    / "outputs"
    / "nosqlschema"
    / "noSQLSchemaModel.xmi"
)


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_base_collections(text: str, *, root_only: bool = False) -> List[str]:
   
    root = ET.fromstring(text)
    collections: List[str] = []

    for element in root.iter():
        if _local_tag(element) != "entities":
            continue
        name = element.get("name")
        if not name:
            continue
        if root_only and element.get("root") != "true":
            continue
        collections.append(name)

    return collections


def load_base_collections(
    path: Optional[Union[str, Path]] = None,
    *,
    root_only: bool = False,
    encoding: str = "utf-8",
) -> List[str]:
    file_path = Path(path) if path is not None else DEFAULT_NOSQL_SCHEMA_XMI
    text = file_path.read_text(encoding=encoding)
    return parse_base_collections(text, root_only=root_only)
