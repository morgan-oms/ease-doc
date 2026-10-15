from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from selector.flags import (
    flags_to_key,
    generate_neighbors,
    make_base_flags,
)

SchemaEvaluator = Callable[[Dict[str, bool]], Dict[str, Any]]

TABU_TENURE = 7
MAX_ITERATIONS = 80
MAX_NO_IMPROVEMENT = 20

Candidate = Tuple[
    Dict[str, bool],
    Dict[str, Any],
    str,
    bool,
    bool,
]


def _evaluate_cached(
    flags: Dict[str, bool],
    dupe_ids: List[str],
    evaluate: SchemaEvaluator,
    eval_cache: Dict[Tuple[int, ...], Dict[str, Any]],
) -> Dict[str, Any]:
    key = flags_to_key(flags, dupe_ids)
    cached = eval_cache.get(key)
    if cached is not None:
        return cached
    result = evaluate(flags)
    eval_cache[key] = result
    return result


def _select_best_admissible_neighbor(
    current_flags: Dict[str, bool],
    dupe_ids: List[str],
    evaluate: SchemaEvaluator,
    eval_cache: Dict[Tuple[int, ...], Dict[str, Any]],
    tabu_moves: Deque[str],
    best_global_cost: float,
) -> Optional[Candidate]:
    best: Optional[Candidate] = None

    for dupe_changed, neighbor_flags in generate_neighbors(current_flags, dupe_ids):
        neighbor_eval = _evaluate_cached(
            neighbor_flags, dupe_ids, evaluate, eval_cache
        )
        is_tabu = dupe_changed in tabu_moves
        aspiration = is_tabu and (neighbor_eval["total_cost"] < best_global_cost)

        if is_tabu and not aspiration:
            continue

        if best is None or neighbor_eval["total_cost"] < best[1]["total_cost"]:
            best = (
                neighbor_flags,
                neighbor_eval,
                dupe_changed,
                is_tabu,
                aspiration,
            )

    return best


def tabu_search(
    evaluate: SchemaEvaluator,
    dupe_ids: List[str],
    *,
    initial_flags: Optional[Dict[str, bool]] = None,
    context: Optional[Dict[str, Any]] = None,
    tabu_tenure: int = TABU_TENURE,
    max_iterations: int = MAX_ITERATIONS,
    max_no_improvement: int = MAX_NO_IMPROVEMENT,
    strategy_label: str = "tabu_search",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:

    base_context = dict(context) if context else {}
    start_time = time.perf_counter()

    eval_cache: Dict[Tuple[int, ...], Dict[str, Any]] = {}
    visited_solution_keys: Set[Tuple[int, ...]] = set()
    tabu_moves: Deque[str] = deque(maxlen=tabu_tenure)

    current_flags = (
        dict(initial_flags) if initial_flags is not None else make_base_flags(dupe_ids)
    )
    current_eval = _evaluate_cached(current_flags, dupe_ids, evaluate, eval_cache)
    best_eval = dict(current_eval)

    visited_solution_keys.add(flags_to_key(current_flags, dupe_ids))

    path_rows: List[Dict[str, Any]] = [
        {
            **base_context,
            "step": 0,
            "move": "START",
            "move_is_tabu": False,
            "aspiration_used": False,
            "from_schema": None,
            "to_schema": current_eval["schema"],
            "accepted_cost": current_eval["total_cost"],
            "best_cost_so_far": best_eval["total_cost"],
            "delta_vs_current": None,
            "delta_vs_global_best": None,
            "improvement": None,
            **current_eval,
        }
    ]

    iterations_without_improvement = 0

    for step in range(1, max_iterations + 1):
        candidate = _select_best_admissible_neighbor(
            current_flags,
            dupe_ids,
            evaluate,
            eval_cache,
            tabu_moves,
            float(best_eval["total_cost"]),
        )
        if candidate is None:
            break

        (
            best_candidate_flags,
            best_candidate_eval,
            best_candidate_move,
            best_candidate_is_tabu,
            best_candidate_uses_aspiration,
        ) = candidate

        previous_cost = current_eval["total_cost"]
        previous_schema = current_eval["schema"]
        current_flags = best_candidate_flags
        current_eval = best_candidate_eval
        visited_solution_keys.add(flags_to_key(current_flags, dupe_ids))
        tabu_moves.append(best_candidate_move)

        if current_eval["total_cost"] < best_eval["total_cost"]:
            best_eval = dict(current_eval)
            iterations_without_improvement = 0
        else:
            iterations_without_improvement += 1

        path_rows.append({
            **base_context,
            "step": step,
            "move": best_candidate_move,
            "move_is_tabu": best_candidate_is_tabu,
            "aspiration_used": best_candidate_uses_aspiration,
            "from_schema": previous_schema,
            "to_schema": current_eval["schema"],
            "accepted_cost": current_eval["total_cost"],
            "best_cost_so_far": best_eval["total_cost"],
            "delta_vs_current": current_eval["total_cost"] - previous_cost,
            "delta_vs_global_best": current_eval["total_cost"] - best_eval["total_cost"],
            "improvement": previous_cost - current_eval["total_cost"],
            **current_eval,
        })

        if iterations_without_improvement >= max_no_improvement:
            break

    execution_time_seconds = time.perf_counter() - start_time

    final_row = {
        **base_context,
        "steps_taken": len(path_rows) - 1,
        "evaluated_candidates": len(eval_cache),
        "visited_solutions": len(visited_solution_keys),
        "search_strategy": strategy_label,
        "tabu_tenure": tabu_tenure,
        "max_iterations": max_iterations,
        "max_no_improvement": max_no_improvement,
        "execution_time_seconds": execution_time_seconds,
        **best_eval,
    }

    return path_rows, final_row
