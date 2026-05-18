
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from extractor.collections_loader import DEFAULT_NOSQL_SCHEMA_XMI

# Tamanho médio estimado do conteúdo UTF-8 de strings (valor BSON, sem type byte / chave).
DEFAULT_STRING_BYTES = 32
# Sobrecarga fixa por elemento BSON (type byte + nome da chave + terminador), alinhada ao greedy.
BSON_KEY_TYPE_OVERHEAD = 12

# Tamanho do valor BSON (bytes) por tipo primitivo U-Schema / BSON.
_PRIMITIVE_VALUE_BYTES: Dict[str, int] = {
    "String": DEFAULT_STRING_BYTES,
    "Integer": 4,
    "Int": 4,
    "Int32": 4,
    "Long": 8,
    "Int64": 8,
    "Double": 8,
    "Float": 8,
    "Boolean": 1,
    "Date": 8,
    "DateTime": 8,
    "Decimal128": 16,
    "Decimal": 16,
    "ObjectId": 12,
}

_OBJECT_ID_VALUE_BYTES = 12


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_child(element: ET.Element, local_name: str) -> Optional[ET.Element]:
    for child in element:
        if _local_tag(child) == local_name:
            return child
    return None


def _find_descendant(element: ET.Element, local_name: str) -> Optional[ET.Element]:
    for node in element.iter():
        if _local_tag(node) == local_name:
            return node
    return None


def _parse_type_info(feature_elem: ET.Element) -> Optional[Tuple[str, str]]:
    type_elem = _find_child(feature_elem, "type")
    if type_elem is None:
        return None

    kind = _local_tag(type_elem)
    if kind == "PrimitiveType":
        return ("primitive", type_elem.get("name", "String"))
    if kind == "PList":
        element_type = _find_child(type_elem, "elementType")
        primitive = element_type.get("name", "String") if element_type is not None else "String"
        return ("plist", primitive)
    return (kind, type_elem.get("name", ""))


def _string_value_size(field_name: str) -> int:
    lowered = field_name.lower()
    if lowered == "_id" or lowered.endswith("id"):
        return 24
    return DEFAULT_STRING_BYTES


def _primitive_value_size(primitive_name: str, field_name: str) -> int:
    if primitive_name == "String":
        return _string_value_size(field_name)
    return _PRIMITIVE_VALUE_BYTES.get(primitive_name, DEFAULT_STRING_BYTES)


def _element_overhead(field_name: str) -> int:
    return 1 + len(field_name.encode("utf-8")) + 1


def _plist_estimated_value_size(element_primitive: str, field_name: str) -> int:
    
    element_value = _primitive_value_size(element_primitive, field_name)
    return 4 + _element_overhead("0") + element_value


@dataclass
class EntityModel:
    name: str
    index: int
    features: List[ET.Element] = field(default_factory=list)


def _parse_entities(root: ET.Element) -> List[EntityModel]:
    entities: List[EntityModel] = []
    index = 0
    for element in root:
        if _local_tag(element) != "entities":
            continue
        name = element.get("name")
        if not name:
            continue
        variation = _find_child(element, "variations")
        features: List[ET.Element] = []
        if variation is not None:
            for feat in variation:
                if _local_tag(feat) in {"Attribute", "Reference", "Aggregate"}:
                    features.append(feat)
        entities.append(EntityModel(name=name, index=index, features=features))
        index += 1
    return entities


def _resolve_refs_to_entity_index(refs_to: str) -> Optional[int]:
    match = re.search(r"/@entities\.(\d+)", refs_to or "")
    if not match:
        return None
    return int(match.group(1))


class SchemaSizeCalculator:
    def __init__(self, entities: List[EntityModel]) -> None:
        self._entities = entities
        self._by_name: Dict[str, EntityModel] = {e.name: e for e in entities}
        self._doc_size_cache: Dict[str, int] = {}

    def entity_doc_size(self, entity_name: str, stack: Optional[Tuple[str, ...]] = None) -> int:
        if entity_name in self._doc_size_cache:
            return self._doc_size_cache[entity_name]

        if stack is None:
            stack = ()
        if entity_name in stack:
            return 0

        entity = self._by_name.get(entity_name)
        if entity is None:
            return 0

        total = 0
        seen_names: set[str] = set()
        next_stack = stack + (entity_name,)

        for feature in entity.features:
            kind = _local_tag(feature)
            name = feature.get("name")

            if kind == "Attribute":
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                type_info = _parse_type_info(feature)
                if type_info is None:
                    continue
                type_kind, primitive = type_info
                if type_kind == "primitive":
                    value_size = _primitive_value_size(primitive, name)
                elif type_kind == "plist":
                    value_size = _plist_estimated_value_size(primitive, name)
                else:
                    value_size = DEFAULT_STRING_BYTES
                total += _element_overhead(name) + value_size

            elif kind == "Reference":
                ref_key = name or f"__ref_{_resolve_refs_to_entity_index(feature.get('refsTo', ''))}__"
                if ref_key in seen_names:
                    continue
                seen_names.add(ref_key)
                total += _element_overhead(ref_key) + _OBJECT_ID_VALUE_BYTES

            elif kind == "Aggregate":
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                nested_entity = self._by_name.get(name)
                if nested_entity is None:
                    continue
                nested_size = self.entity_doc_size(name, next_stack)
                total += _element_overhead(name) + nested_size

        self._doc_size_cache[entity_name] = total
        return total

    def build_field_sizes(self) -> Dict[str, int]:
        fields: Dict[str, int] = {"bson_overhead": BSON_KEY_TYPE_OVERHEAD}

        for entity in self._entities:
            for feature in entity.features:
                kind = _local_tag(feature)
                name = feature.get("name")

                if kind == "Attribute" and name:
                    type_info = _parse_type_info(feature)
                    if type_info is None:
                        continue
                    type_kind, primitive = type_info
                    if type_kind == "primitive":
                        fields[name] = _primitive_value_size(primitive, name)
                    elif type_kind == "plist":
                        fields[name] = _plist_estimated_value_size(primitive, name)

                elif kind == "Reference":
                    ref_key = name or f"__ref_{entity.index}_{feature.get('refsTo', '')}__"
                    fields[ref_key] = _OBJECT_ID_VALUE_BYTES

        return fields

    def build_avg_doc_sizes(self) -> Dict[str, int]:
        return {entity.name: self.entity_doc_size(entity.name) for entity in self._entities}


def parse_field_and_avg_sizes(text: str) -> Tuple[Dict[str, int], Dict[str, int]]:
    root = ET.fromstring(text)
    calculator = SchemaSizeCalculator(_parse_entities(root))
    return calculator.build_field_sizes(), calculator.build_avg_doc_sizes()


def load_fields(path: Optional[Union[str, Path]] = None) -> Dict[str, int]:
    file_path = Path(path) if path is not None else DEFAULT_NOSQL_SCHEMA_XMI
    fields, _ = parse_field_and_avg_sizes(file_path.read_text(encoding="utf-8"))
    return fields


def load_avg_doc_sizes(path: Optional[Union[str, Path]] = None) -> Dict[str, int]:
    file_path = Path(path) if path is not None else DEFAULT_NOSQL_SCHEMA_XMI
    _, avg_sizes = parse_field_and_avg_sizes(file_path.read_text(encoding="utf-8"))
    return avg_sizes
