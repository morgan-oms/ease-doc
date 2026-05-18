from __future__ import annotations

from typing import Dict, Iterable, List, Tuple


def flags_to_schema_name(
    flags: Dict[str, bool],
    dupe_ids: List[str],
    base_schema: str,
) -> str:
    enabled = [k for k in dupe_ids if flags.get(k, False)]
    return base_schema if not enabled else base_schema + "_" + "_".join(enabled)


def make_base_flags(dupe_ids: List[str]) -> Dict[str, bool]:
    return {k: False for k in dupe_ids}


def flags_to_key(flags: Dict[str, bool], dupe_ids: List[str]) -> Tuple[int, ...]:
    return tuple(int(flags[k]) for k in dupe_ids)


def copy_flags(flags: Dict[str, bool]) -> Dict[str, bool]:
    return {k: bool(v) for k, v in flags.items()}


def generate_neighbors(
    flags: Dict[str, bool],
    dupe_ids: List[str],
) -> Iterable[Tuple[str, Dict[str, bool]]]:
    for dupe_id in dupe_ids:
        neighbor = copy_flags(flags)
        neighbor[dupe_id] = not neighbor[dupe_id]
        yield dupe_id, neighbor
