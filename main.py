#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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
from extractor.dupes_loader import DEFAULT_POSSIBLES_DUPLICATION, load_dupe_ids

EASE_DOC_CONFIG = Path(__file__).resolve().parent / "ease_doc.config.json"

DUPES: Dict[str, str] = load_dupes()
DUPE_IDS: List[str] = load_dupe_ids(DUPES)
BASE_SCHEMA: str = load_base_schema(EASE_DOC_CONFIG)

BASE_COLLECTIONS: List[str] = load_base_collections()

BASE_READ_PATTERNS: Dict[str, List[Tuple[str, int]]] = load_base_read_patterns()

BASE_WRITE_PATTERNS: Dict[str, List[Dict[str, Union[str, int]]]] = load_base_write_patterns()

# Tamanhos BSON por campo (https://bsonspec.org/spec.html) e .
FIELD: Dict[str, int] = load_fields()

# Tamanho médio por coleção
AVG_DOC_SIZE_BASE: Dict[str, int] = load_avg_doc_sizes()

VOLUMES: Dict[str, Dict[str, int]] = load_volumes(EASE_DOC_CONFIG)

WORKLOAD: Dict[str, Any] = load_workload(EASE_DOC_CONFIG)

# Parâmetros físicos do Avaliador (fixos; ver Seção 3.3 do artigo / Hewasinghage et al. 2021).

# Tamanho de bloco para indexação e leitura em disco (4 KB), alinhado ao modelo
# genérico de custo e a análises de acesso aleatório em armazenamento em blocos.
BLOCK_SIZE_BYTES = 4096

# Memória disponível para cache de dados e de índices; usada para calcular Pi(C)
# e Pd(C), isto é, a fração de blocos residentes em memória por coleção.
MEM_DATA_MB = 256
MEM_INDEX_MB = 256
MEM_DATA_BYTES = MEM_DATA_MB * 1024 * 1024
MEM_INDEX_BYTES = MEM_INDEX_MB * 1024 * 1024

# Custos relativos de acesso: Td (disco) e Tm (memória). Tm << Td captura, de forma
# simplificada, a diferença de ordem de grandeza entre RAM e disco (Ousterhout et al.).
Td = 1.0
Tm = 0.01

# Fração do tamanho dos dados usada para estimar o índice da coleção (Size_i ≈ f · Size_d).
# 0.1 (10%) é um valor fixo e uniforme, simplificando o modelo de Hewasinghage et al.
# quando não há estatísticas reais de índice; uma entrada por coleção em BASE_COLLECTIONS.
INDEX_FACTOR = {c: 0.1 for c in BASE_COLLECTIONS}



def extract_dupes(
    duplication_file: Optional[Path] = None,
    *,
    id_prefix: str = "D",
) -> Dict[str, str]:
    path = duplication_file
    return load_dupes(path, id_prefix=id_prefix)


def run(args: argparse.Namespace) -> int:
    duplication_path = Path(args.duplication_file) if args.duplication_file else None

    if duplication_path is not None and not duplication_path.is_file():
        print(f"Erro: arquivo não encontrado: {duplication_path}", file=sys.stderr)
        return 1

    if duplication_path is None:
        active_dupes = DUPES
    else:
        try:
            active_dupes = extract_dupes(duplication_path, id_prefix=args.prefix)
        except OSError as exc:
            print(f"Erro ao ler duplicações: {exc}", file=sys.stderr)
            return 1

    if not active_dupes:
        resolved = duplication_path or DEFAULT_POSSIBLES_DUPLICATION
        print(f"Aviso: nenhuma duplicação encontrada em {resolved}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(active_dupes, indent=2, ensure_ascii=False))
        return 0

    print(f"EASE-Doc — duplicações extraídas ({len(active_dupes)})")
    print()
    for dupe_id in load_dupe_ids(active_dupes):
        print(f"  {dupe_id}: {active_dupes[dupe_id]}")

    print()
    print(f"EASE-Doc — coleções base ({len(BASE_COLLECTIONS)})")
    print()
    for collection in BASE_COLLECTIONS:
        print(f"  - {collection}")

    print()
    print(f"EASE-Doc — volumetria ({len(VOLUMES)} perfil(is))")
    print(f"Fonte: {EASE_DOC_CONFIG.resolve()}")
    print()
    for profile, collections in VOLUMES.items():
        print(f"  [{profile}]")
        for collection, count in collections.items():
            print(f"    {collection}: {count}")

    print()
    print(f"EASE-Doc — tamanhos de documento ({len(AVG_DOC_SIZE_BASE)} coleções)")
    print()
    for collection, size in AVG_DOC_SIZE_BASE.items():
        print(f"  {collection}: {size} bytes (estimado)")

    print()
    print(f"EASE-Doc — campos BSON ({len(FIELD) - 1} campos + bson_overhead)")
    print()
    for field_name, size in sorted(FIELD.items()):
        if field_name == "bson_overhead":
            continue
        print(f"  {field_name}: {size}")

    print()
    print(f"EASE-Doc — padrões de leitura ({len(BASE_READ_PATTERNS)} consultas)")
    print()
    for query_id, accesses in BASE_READ_PATTERNS.items():
        path = ", ".join(f"{collection}×{count}" for collection, count in accesses)
        print(f"  {query_id}: [{path}]")

    print()
    print(f"EASE-Doc — padrões de escrita ({len(BASE_WRITE_PATTERNS)} operações)")
    print()
    for op_id, accesses in BASE_WRITE_PATTERNS.items():
        parts = [
            f"{entry['collection']}(r{entry['reads']},w{entry['writes']})"
            for entry in accesses
        ]
        print(f"  {op_id}: [{', '.join(parts)}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ease-doc",
        description="EASE-Doc: extração, avaliação e seleção de esquemas NoSQL.",
    )
    parser.add_argument(
        "-f",
        "--duplication-file",
        metavar="PATH",
        help=(
            "Caminho para possibles-duplication.js "
            f"(padrão: {DEFAULT_POSSIBLES_DUPLICATION})"
        ),
    )
    parser.add_argument(
        "--prefix",
        default="D",
        help="Prefixo dos identificadores de duplicação (padrão: D)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Saída em JSON",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
