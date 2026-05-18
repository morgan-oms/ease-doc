
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "ease_doc.config.json"


def load_config(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    file_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with file_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _parse_collection_counts(raw: Mapping[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Nome de coleção inválido: {name!r}")
        try:
            counts[name] = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Volume da coleção '{name}' deve ser um número inteiro; recebido: {value!r}"
            ) from exc
    return counts


def load_volumes(
    path: Optional[Union[str, Path]] = None,
) -> Dict[str, Dict[str, int]]:

    config = load_config(path)
    volume = config.get("volume")
    if not isinstance(volume, dict):
        raise ValueError("Configuração inválida: chave 'volume' ausente ou não é um objeto.")

    collections = volume.get("collections")
    if not isinstance(collections, dict) or not collections:
        raise ValueError(
            "Configuração inválida: 'volume.collections' deve ser um objeto não vazio."
        )

    profile = volume.get("profile", "default")
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("Configuração inválida: 'volume.profile' deve ser uma string não vazia.")

    return {profile: _parse_collection_counts(collections)}


def _parse_mix(raw: Mapping[str, Any], field_name: str) -> Dict[str, float]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError(
            f"Configuração inválida: 'workload.{field_name}' deve ser um objeto não vazio."
        )
    mix: Dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Chave inválida em workload.{field_name}: {key!r}")
        try:
            mix[key] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Peso '{key}' em workload.{field_name} deve ser numérico; recebido: {value!r}"
            ) from exc
    return mix


def load_base_schema(path: Optional[Union[str, Path]] = None) -> str:
    config = load_config(path)
    base_schema = config.get("base_schema")
    if not isinstance(base_schema, str) or not base_schema.strip():
        raise ValueError(
            "Configuração inválida: 'base_schema' deve ser uma string não vazia."
        )
    return base_schema.strip()


def load_workload(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:

    config = load_config(path)
    workload = config.get("workload")
    if not isinstance(workload, dict):
        raise ValueError("Configuração inválida: chave 'workload' ausente ou não é um objeto.")

    if "alpha_read" not in workload:
        raise ValueError("Configuração inválida: 'workload.alpha_read' é obrigatório.")
    try:
        alpha_read = float(workload["alpha_read"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Configuração inválida: 'workload.alpha_read' deve ser numérico.") from exc
    if not 0.0 <= alpha_read <= 1.0:
        raise ValueError("Configuração inválida: 'workload.alpha_read' deve estar em [0, 1].")

    return {
        "alpha_read": alpha_read,
        "read_mix": _parse_mix(workload.get("read_mix", {}), "read_mix"),
        "write_mix": _parse_mix(workload.get("write_mix", {}), "write_mix"),
    }
