
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

CollectionStats = Dict[str, Any]
MemoryStats = Dict[str, int]
ReadPatterns = Mapping[str, List[Tuple[str, int]]]
WritePatterns = Mapping[str, List[Dict[str, Any]]]
IndexFactor = Union[float, Mapping[str, float]]


@dataclass(frozen=True)
class CostModelParams:

    block_size_bytes: int = 4096
    mem_data_bytes: int = 256 * 1024 * 1024
    mem_index_bytes: int = 256 * 1024 * 1024
    Tm: float = 0.01
    Td: float = 1.0
    lambda_write: float = 1.0
    default_index_factor: float = 0.1


def _resolve_index_factor(
    collection: str,
    index_factor: IndexFactor,
    default: float,
) -> float:
    if isinstance(index_factor, Mapping):
        return float(index_factor.get(collection, default))
    return float(index_factor)


def estimate_coll_stats(
    avg_sizes: Mapping[str, int],
    doc_counts: Mapping[str, int],
    *,
    index_factor: IndexFactor = 0.1,
    block_size_bytes: int = 4096,
) -> Dict[str, CollectionStats]:

    stats: Dict[str, CollectionStats] = {}
    for collection, n_docs in doc_counts.items():
        if collection not in avg_sizes:
            continue
        n_docs = int(n_docs)
        storage = n_docs * int(avg_sizes[collection])
        idx_factor = _resolve_index_factor(
            collection, index_factor, default=0.1
        )
        index = storage * idx_factor
        b_d = max(1, math.ceil(storage / block_size_bytes))
        b_i = max(1, math.ceil(index / block_size_bytes))
        stats[collection] = {
            "n_docs": n_docs,
            "storage_size": storage,
            "index_size": index,
            "B_d": b_d,
            "B_i": b_i,
        }
    return stats


def allocate_memory_blocks_per_schema(
    coll_stats: Mapping[str, CollectionStats],
    *,
    mem_data_bytes: int,
    mem_index_bytes: int,
    block_size_bytes: int,
) -> Dict[str, MemoryStats]:


    total_data_blocks = mem_data_bytes // block_size_bytes
    total_index_blocks = mem_index_bytes // block_size_bytes

    total_storage = sum(cs["storage_size"] for cs in coll_stats.values())
    total_index = sum(cs["index_size"] for cs in coll_stats.values())

    mem: Dict[str, MemoryStats] = {}
    for collection, cs in coll_stats.items():
        share_data = (
            cs["storage_size"] / total_storage if total_storage > 0 else 0.0
        )
        share_index = (
            cs["index_size"] / total_index if total_index > 0 else 0.0
        )
        m_d = min(cs["B_d"], int(total_data_blocks * share_data))
        m_i = min(cs["B_i"], int(total_index_blocks * share_index))
        mem[collection] = {"M_d": m_d, "M_i": m_i}
    return mem


def cost_rand(
    collection: str,
    coll_stats: Mapping[str, CollectionStats],
    mem_stats: Mapping[str, MemoryStats],
    *,
    Tm: float,
    Td: float,
) -> float:

    cs = coll_stats[collection]
    ms = mem_stats[collection]

    b_d, b_i = cs["B_d"], cs["B_i"]
    m_d, m_i = ms["M_d"], ms["M_i"]

    p_d = min(1.0, m_d / b_d) if b_d else 0.0
    p_i = min(1.0, m_i / b_i) if b_i else 0.0

    cost_idx = (Tm * p_i + Td * (1 - p_i)) / 2.0
    cost_data = (Tm * p_d + Td * (1 - p_d)) / 2.0
    return cost_idx + cost_data


def compute_read_cost(
    read_patterns: ReadPatterns,
    read_mix: Mapping[str, float],
    coll_stats: Mapping[str, CollectionStats],
    mem_stats: Mapping[str, MemoryStats],
    *,
    Tm: float,
    Td: float,
) -> float:

    total = 0.0
    for query_id, weight in read_mix.items():
        if weight == 0:
            continue
        accesses = read_patterns.get(query_id, [])
        cost_q = 0.0
        for collection, n_acc in accesses:
            if collection not in coll_stats:
                continue
            cost_q += n_acc * cost_rand(
                collection, coll_stats, mem_stats, Tm=Tm, Td=Td
            )
        total += weight * cost_q
    return total


def compute_write_cost(
    write_patterns: WritePatterns,
    write_mix: Mapping[str, float],
    coll_stats: Mapping[str, CollectionStats],
    mem_stats: Mapping[str, MemoryStats],
    *,
    Tm: float,
    Td: float,
) -> float:

    total = 0.0
    for op_id, weight in write_mix.items():
        if weight == 0:
            continue
        steps = write_patterns.get(op_id, [])
        cost_op = 0.0
        for step in steps:
            collection = step["collection"]
            if collection not in coll_stats:
                continue
            n_reads = float(step.get("reads", 0))
            n_writes = float(step.get("writes", 0))
            c = cost_rand(collection, coll_stats, mem_stats, Tm=Tm, Td=Td)
            cost_op += n_reads * c + n_writes * c
        total += weight * cost_op
    return total


def evaluate_schema(
    schema_name: str,
    avg_sizes: Mapping[str, int],
    doc_counts: Mapping[str, int],
    read_patterns: ReadPatterns,
    write_patterns: WritePatterns,
    workload: Mapping[str, Any],
    *,
    cost_params: Optional[CostModelParams] = None,
    index_factor: Optional[IndexFactor] = None,
) -> Dict[str, Any]:

    params = cost_params or CostModelParams()
    effective_index_factor = (
        index_factor if index_factor is not None else params.default_index_factor
    )

    coll_stats = estimate_coll_stats(
        avg_sizes,
        doc_counts,
        index_factor=effective_index_factor,
        block_size_bytes=params.block_size_bytes,
    )
    mem_stats = allocate_memory_blocks_per_schema(
        coll_stats,
        mem_data_bytes=params.mem_data_bytes,
        mem_index_bytes=params.mem_index_bytes,
        block_size_bytes=params.block_size_bytes,
    )

    alpha = float(workload["alpha_read"])
    read_cost = compute_read_cost(
        read_patterns,
        workload["read_mix"],
        coll_stats,
        mem_stats,
        Tm=params.Tm,
        Td=params.Td,
    )
    write_cost = compute_write_cost(
        write_patterns,
        workload["write_mix"],
        coll_stats,
        mem_stats,
        Tm=params.Tm,
        Td=params.Td,
    )
    total_cost = alpha * read_cost + (1 - alpha) * write_cost

    return {
        "schema": schema_name,
        "alpha_read": alpha,
        "read_cost": read_cost,
        "write_cost": write_cost,
        "total_cost": total_cost,
    }
