#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from analyzer import (
    CostModelParams,
    QueryStructure,
    ReferenceGraph,
    build_read_patterns,
    build_write_patterns,
    derive_avg_doc_sizes,
    evaluate_schema,
    load_query_structures,
    load_reference_graph,
)
from extractor import (
    load_avg_doc_sizes,
    load_base_collections,
    load_base_read_patterns,
    load_base_schema,
    load_base_write_patterns,
    load_dupes,
    load_fields,
    load_volumes,
    load_workload,
)
from extractor.config_loader import DEFAULT_CONFIG_PATH, load_config
from extractor.dupes_loader import DEFAULT_POSSIBLES_DUPLICATION, load_dupe_ids
from selector import flags_to_schema_name, greedy_hill_climb


@dataclass(frozen=True)
class ExtractionArtifacts:
    """Snapshot consolidado de tudo o que o Extrator produz para o pipeline."""

    base_schema: str
    base_collections: List[str]
    dupes: Dict[str, str]
    dupe_ids: List[str]
    fields: Dict[str, int]
    avg_doc_size_base: Dict[str, int]
    base_read_patterns: Dict[str, List[Tuple[str, int]]]
    base_write_patterns: Dict[str, List[Dict[str, Any]]]
    volumes: Dict[str, Dict[str, int]]
    workload: Dict[str, Any]
    scenario_id: str
    query_structures: Dict[str, QueryStructure]
    reference_graph: ReferenceGraph


def extract_all(
    config_path: Optional[Path] = None,
    duplication_path: Optional[Path] = None,
) -> ExtractionArtifacts:
    """Camada 1 — Extrator.

    Lê todos os artefatos a partir do U-Schema, do DBO e do arquivo de
    configuração, e devolve um snapshot imutável usado pelas camadas
    seguintes. Os modelos auxiliares (`query_structures`, `reference_graph`)
    são pré-carregados aqui para evitar parsing repetido do XMI dentro do
    loop do hill climbing.
    """

    cfg_path = config_path or DEFAULT_CONFIG_PATH
    raw_cfg = load_config(cfg_path)

    dupes = load_dupes(duplication_path)

    return ExtractionArtifacts(
        base_schema=load_base_schema(cfg_path),
        base_collections=load_base_collections(),
        dupes=dupes,
        dupe_ids=load_dupe_ids(dupes),
        fields=load_fields(),
        avg_doc_size_base=load_avg_doc_sizes(),
        base_read_patterns=load_base_read_patterns(),
        base_write_patterns=load_base_write_patterns(),
        volumes=load_volumes(cfg_path),
        workload=load_workload(cfg_path),
        scenario_id=str(raw_cfg.get("scenario_id", "default")),
        query_structures=load_query_structures(),
        reference_graph=load_reference_graph(),
    )


def make_evaluator(
    artifacts: ExtractionArtifacts,
    doc_counts: Dict[str, int],
    *,
    cost_params: Optional[CostModelParams] = None,
) -> Callable[[Dict[str, bool]], Dict[str, Any]]:
    """Camadas 2/3 — Avaliador.

    Constrói o callable `evaluate(flags) -> dict` consumido pelo Seletor.
    Cada chamada reaplica os builders sobre o esquema base, instancia o
    esquema candidato e o avalia com o modelo de custo.
    """

    def _eval(flags: Dict[str, bool]) -> Dict[str, Any]:
        schema_name = flags_to_schema_name(
            flags, artifacts.dupe_ids, artifacts.base_schema
        )
        avg_sizes = derive_avg_doc_sizes(
            artifacts.avg_doc_size_base,
            flags,
            artifacts.dupes,
            artifacts.dupe_ids,
            artifacts.fields,
            volume_params=doc_counts,
        )
        read_patterns = build_read_patterns(
            artifacts.base_read_patterns,
            flags,
            artifacts.dupes,
            artifacts.dupe_ids,
            query_structures=artifacts.query_structures,
        )
        write_patterns = build_write_patterns(
            artifacts.base_write_patterns,
            flags,
            artifacts.dupes,
            artifacts.dupe_ids,
            doc_counts,
            reference_graph=artifacts.reference_graph,
        )
        return evaluate_schema(
            schema_name,
            avg_sizes,
            doc_counts,
            read_patterns,
            write_patterns,
            artifacts.workload,
            cost_params=cost_params,
        )

    return _eval


def run_pipeline(
    artifacts: ExtractionArtifacts,
    *,
    cost_params: Optional[CostModelParams] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Camada 4 — Seletor.

    Para cada perfil de volumetria, monta o avaliador correspondente e
    executa o hill climbing best-improvement.
    """

    path_rows_all: List[Dict[str, Any]] = []
    final_rows: List[Dict[str, Any]] = []

    for vol_label, doc_counts in artifacts.volumes.items():
        evaluator = make_evaluator(artifacts, doc_counts, cost_params=cost_params)
        path_rows, final_row = greedy_hill_climb(
            evaluator,
            artifacts.dupe_ids,
            context={
                "volume": vol_label,
                "workload": artifacts.scenario_id,
            },
        )
        path_rows_all.extend(path_rows)
        final_rows.append(final_row)

    return path_rows_all, final_rows


def persist_results(
    path_rows: List[Dict[str, Any]],
    final_rows: List[Dict[str, Any]],
    *,
    artifacts: ExtractionArtifacts,
    output_dir: Path,
) -> Tuple[Path, Path]:
    """Persiste o trajeto e a linha final do hill climbing em CSV."""

    output_dir.mkdir(parents=True, exist_ok=True)

    alpha_pct = int(round(float(artifacts.workload["alpha_read"]) * 100))
    suffix = f"R{alpha_pct}_W{100 - alpha_pct}"

    out_path = output_dir / f"easedoc_{artifacts.base_schema}_path_{suffix}.csv"
    out_final = output_dir / f"easedoc_{artifacts.base_schema}_final_{suffix}.csv"

    df_path = pd.DataFrame(path_rows)
    df_final = pd.DataFrame(final_rows)

    df_path.sort_values(["volume", "workload", "step"]).to_csv(out_path, index=False)
    df_final.sort_values(
        ["volume", "workload", "total_cost", "schema"]
    ).to_csv(out_final, index=False)

    return out_path, out_final


def report_best(final_rows: List[Dict[str, Any]]) -> None:
    """Imprime a resposta final do EASE-Doc no terminal."""

    print("EASE-Doc — resultado final")
    print()
    for row in final_rows:
        print(f"  Volume               : {row.get('volume')}")
        print(f"  Workload             : {row.get('workload')}")
        print(f"  Esquema selecionado  : {row['schema']}")
        print(f"  Custo total estimado : {row['total_cost']:.6f}")
        print(f"  ReadCost             : {row['read_cost']:.6f}")
        print(f"  WriteCost            : {row['write_cost']:.6f}")
        print(f"  Passos               : {row['steps_taken']}")
        print(f"  Candidatos avaliados : {row['evaluated_candidates']}")
        print(f"  Tempo                : {row['execution_time_seconds'] * 1000:.2f} ms")
        print()


def diagnose(artifacts: ExtractionArtifacts) -> None:
    """Modo diagnóstico — imprime os artefatos extraídos, sem rodar a busca."""

    print(f"EASE-Doc — duplicações extraídas ({len(artifacts.dupes)})")
    print()
    for dupe_id in artifacts.dupe_ids:
        print(f"  {dupe_id}: {artifacts.dupes[dupe_id]}")

    print()
    print(f"EASE-Doc — coleções base ({len(artifacts.base_collections)})")
    print()
    for collection in artifacts.base_collections:
        print(f"  - {collection}")

    print()
    print(f"EASE-Doc — volumetria ({len(artifacts.volumes)} perfil(is))")
    print()
    for profile, collections in artifacts.volumes.items():
        print(f"  [{profile}]")
        for collection, count in collections.items():
            print(f"    {collection}: {count}")

    print()
    print(
        f"EASE-Doc — workload (alpha_read={artifacts.workload['alpha_read']}, "
        f"scenario={artifacts.scenario_id})"
    )
    print(f"  read_mix : {artifacts.workload['read_mix']}")
    print(f"  write_mix: {artifacts.workload['write_mix']}")

    print()
    print(
        f"EASE-Doc — tamanhos médios de documento ({len(artifacts.avg_doc_size_base)} coleções)"
    )
    print()
    for collection, size in artifacts.avg_doc_size_base.items():
        print(f"  {collection}: {size} bytes (estimado)")

    print()
    print(
        f"EASE-Doc — padrões de leitura ({len(artifacts.base_read_patterns)} consultas)"
    )
    print()
    for query_id, accesses in artifacts.base_read_patterns.items():
        path = ", ".join(f"{collection}×{count}" for collection, count in accesses)
        print(f"  {query_id}: [{path}]")

    print()
    print(
        f"EASE-Doc — padrões de escrita ({len(artifacts.base_write_patterns)} operações)"
    )
    print()
    for op_id, accesses in artifacts.base_write_patterns.items():
        parts = [
            f"{entry['collection']}(r{entry['reads']},w{entry['writes']})"
            for entry in accesses
        ]
        print(f"  {op_id}: [{', '.join(parts)}]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ease-doc",
        description="EASE-Doc: extração, avaliação e seleção de esquemas NoSQL.",
    )
    parser.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        help=f"Arquivo de configuração (padrão: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "-f",
        "--duplication-file",
        metavar="PATH",
        help=(
            "Arquivo possibles-duplication.js "
            f"(padrão: {DEFAULT_POSSIBLES_DUPLICATION})."
        ),
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        metavar="PATH",
        default=".",
        help="Diretório de saída para os CSVs (padrão: ./).",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Apenas imprime os artefatos extraídos, sem rodar a busca.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Imprime a resposta final em JSON (em vez do relatório textual).",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Não persiste CSV; apenas imprime o resultado no terminal.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    config_path = Path(args.config) if args.config else None
    duplication_path = Path(args.duplication_file) if args.duplication_file else None

    if duplication_path is not None and not duplication_path.is_file():
        print(f"Erro: arquivo não encontrado: {duplication_path}", file=sys.stderr)
        return 1
    if config_path is not None and not config_path.is_file():
        print(f"Erro: arquivo não encontrado: {config_path}", file=sys.stderr)
        return 1

    try:
        artifacts = extract_all(
            config_path=config_path, duplication_path=duplication_path
        )
    except (OSError, ValueError) as exc:
        print(f"Erro na extração: {exc}", file=sys.stderr)
        return 1

    if not artifacts.dupes:
        resolved = duplication_path or DEFAULT_POSSIBLES_DUPLICATION
        print(
            f"Aviso: nenhuma duplicação encontrada em {resolved}",
            file=sys.stderr,
        )
        return 1

    if args.diagnose:
        diagnose(artifacts)
        return 0

    path_rows, final_rows = run_pipeline(artifacts)

    if not args.no_write:
        out_path, out_final = persist_results(
            path_rows,
            final_rows,
            artifacts=artifacts,
            output_dir=Path(args.output_dir),
        )
        print(f"Trajeto: {out_path}")
        print(f"Final  : {out_final}")
        print()

    if args.json:
        print(json.dumps(final_rows, indent=2, ensure_ascii=False, default=str))
    else:
        report_best(final_rows)

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
