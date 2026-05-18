"""
Grafo de referências entre coleções extraído do dboSchemaModel.xmi.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

DEFAULT_DBO_SCHEMA_XMI = (
    Path(__file__).resolve().parent.parent
    / "uschema-code-analysis"
    / "es.um.uschema.code.transfs.launcher"
    / "outputs"
    / "dboSchema"
    / "dboSchemaModel.xmi"
)

CONTAINER_REF_RE = re.compile(r"/2/@containers\.(\d+)")


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _xsi_type_name(element: ET.Element) -> str:
    xsi_type = element.get("{http://www.w3.org/2001/XMLSchema-instance}type", "")
    if ":" in xsi_type:
        return xsi_type.split(":", 1)[-1]
    return xsi_type


def _container_index(ref: str) -> Optional[int]:
    match = CONTAINER_REF_RE.search(ref or "")
    return int(match.group(1)) if match else None


@dataclass
class ReferenceGraph:
    """Arestas child -> parent (a coleção filha referencia a pai)."""

    collections: Tuple[str, ...]
    edges: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    _edge_set: Set[Tuple[str, str]] = field(init=False, repr=False)
    _adjacency: Dict[str, List[str]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._edge_set = set(self.edges)
        adjacency: Dict[str, List[str]] = {}
        for child, parent in self.edges:
            adjacency.setdefault(child, []).append(parent)
        self._adjacency = adjacency

    def references(self, child: str, parent: str) -> bool:
        return (child, parent) in self._edge_set

    def has_path_to_source(self, target: str, source: str) -> bool:
        return self._shortest_path(target, source) is not None

    def ratio_target_per_source(
        self,
        source: str,
        target: str,
        volumes: Dict[str, float],
    ) -> float:
        """
        Estima quantos documentos em ``target`` são afetados por uma alteração
        em ``source`` (propagação de escrita ou leituras de enriquecimento).
        """
        path = self._shortest_path(target, source)
        if path is None or len(path) < 2:
            source_vol = max(1.0, float(volumes.get(source, 1.0)))
            target_vol = float(volumes.get(target, 0.0))
            return target_vol / source_vol

        ratio = 1.0
        for index in range(len(path) - 1):
            child = path[index]
            parent = path[index + 1]
            parent_vol = max(1.0, float(volumes.get(parent, 1.0)))
            child_vol = float(volumes.get(child, 0.0))
            ratio *= child_vol / parent_vol
        return ratio

    def _shortest_path(self, start: str, goal: str) -> Optional[List[str]]:
        if start == goal:
            return [start]

        queue: deque[List[str]] = deque([[start]])
        visited = {start}

        while queue:
            path = queue.popleft()
            node = path[-1]
            for parent in self._adjacency.get(node, []):
                if parent in visited:
                    continue
                next_path = path + [parent]
                if parent == goal:
                    return next_path
                visited.add(parent)
                queue.append(next_path)
        return None


def _parse_container_names(dbo_root: ET.Element) -> List[str]:
    names: List[str] = []
    for element in dbo_root.iter():
        if _local_tag(element) != "containers":
            continue
        name = element.get("name")
        names.append(name if name else f"__container_{len(names)}__")
    return names


def _find_dbo_schema_root(root: ET.Element) -> Optional[ET.Element]:
    for element in root.iter():
        if _local_tag(element) == "DatabaseOperationsSchema":
            return element
    return None


def parse_reference_graph(text: str) -> ReferenceGraph:
    root = ET.fromstring(text)
    dbo_root = _find_dbo_schema_root(root)
    if dbo_root is None:
        raise ValueError("dboSchemaModel.xmi inválido: DatabaseOperationsSchema não encontrado.")

    container_names = _parse_container_names(dbo_root)
    edge_set: Set[Tuple[str, str]] = set()
    current_container: Optional[str] = None

    for element in dbo_root.iter():
        tag = _local_tag(element)
        if tag == "containers":
            name = element.get("name")
            current_container = name if name else current_container
        elif _xsi_type_name(element) == "Reference" and current_container:
            target_ref = element.get("targetContainer", "")
            target_index = _container_index(target_ref)
            if target_index is None or not (0 <= target_index < len(container_names)):
                continue
            parent = container_names[target_index]
            child = current_container
            if child != parent:
                edge_set.add((child, parent))

    return ReferenceGraph(
        collections=tuple(container_names),
        edges=tuple(sorted(edge_set)),
    )


def load_reference_graph(
    path: Optional[Union[str, Path]] = None,
) -> ReferenceGraph:
    file_path = Path(path) if path is not None else DEFAULT_DBO_SCHEMA_XMI
    return parse_reference_graph(file_path.read_text(encoding="utf-8"))
