
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from selector.flags import (
    flags_to_key,
    generate_neighbors,
    make_base_flags,
)

SchemaEvaluator = Callable[[Dict[str, bool]], Dict[str, Any]]


def _improvement(current: Dict[str, Any], candidate: Dict[str, Any]) -> float:
    return float(current["total_cost"]) - float(candidate["total_cost"])


def _best_neighbor(
    current_flags: Dict[str, bool],
    dupe_ids: List[str],
    evaluate: SchemaEvaluator,
    visited: Set[Tuple[int, ...]],
) -> Tuple[Optional[Dict[str, bool]], Optional[Dict[str, Any]], Optional[str], int]:
    best_flags: Optional[Dict[str, bool]] = None
    best_eval: Optional[Dict[str, Any]] = None
    best_move: Optional[str] = None
    evaluated = 0

    for dupe_changed, neighbor_flags in generate_neighbors(current_flags, dupe_ids):
        neighbor_key = flags_to_key(neighbor_flags, dupe_ids)
        if neighbor_key in visited:
            continue

        neighbor_eval = evaluate(neighbor_flags)
        evaluated += 1

        if best_eval is None or neighbor_eval["total_cost"] < best_eval["total_cost"]:
            best_flags = neighbor_flags
            best_eval = neighbor_eval
            best_move = dupe_changed

    return best_flags, best_eval, best_move, evaluated


def greedy_hill_climb(
    evaluate: SchemaEvaluator,
    dupe_ids: List[str],
    *,
    initial_flags: Optional[Dict[str, bool]] = None,
    context: Optional[Dict[str, Any]] = None,
    strategy_label: str = "greedy_hill_climbing_best_improvement",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
   
    base_context = dict(context) if context else {}
    start_time = time.perf_counter()

    current_flags = (
        dict(initial_flags) if initial_flags is not None else make_base_flags(dupe_ids)
    )
    current_eval = evaluate(current_flags)

    visited: Set[Tuple[int, ...]] = {flags_to_key(current_flags, dupe_ids)}
    path_rows: List[Dict[str, Any]] = []

    evaluated_candidates = 1
    step = 0

    path_rows.append({
        **base_context,
        "step": step,
        "move": "START",
        "from_schema": None,
        "to_schema": current_eval["schema"],
        "improvement": None,
        **current_eval,
    })

    while True:
        best_flags, best_eval, best_move, evaluated_this_round = _best_neighbor(
            current_flags, dupe_ids, evaluate, visited
        )
        evaluated_candidates += evaluated_this_round

        if best_eval is None:
            break

        improvement = _improvement(current_eval, best_eval)
        if improvement <= 0:
            break

        previous_schema = current_eval["schema"]
        current_flags = best_flags
        current_eval = best_eval
        visited.add(flags_to_key(current_flags, dupe_ids))

        step += 1
        path_rows.append({
            **base_context,
            "step": step,
            "move": best_move,
            "from_schema": previous_schema,
            "to_schema": current_eval["schema"],
            "improvement": improvement,
            **current_eval,
        })

    execution_time_seconds = time.perf_counter() - start_time

    final_row = {
        **base_context,
        "steps_taken": step,
        "evaluated_candidates": evaluated_candidates,
        "search_strategy": strategy_label,
        "execution_time_seconds": execution_time_seconds,
        **current_eval,
    }

    return path_rows, final_row
