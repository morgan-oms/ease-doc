
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

DEFAULT_POSSIBLES_DUPLICATION = (
    Path(__file__).resolve().parent.parent
    / "uschema-code-analysis"
    / "es.um.uschema.code.transfs.launcher"
    / "outputs"
    / "duplication"
    / "updates"
    / "possibles-duplication.js"
)

_COPY_LINE = re.compile(
    r"^\s*Copy\s*:?\s*"
    r"(?P<source>[\w]+)"
    r"\[(?P<fields>[^\]]*)\]"
    r"\s*(?:to:\s*|->\s*)"
    r"(?P<target>[\w.]+)"
    r"\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DuplicationOp:

    source: str
    fields: tuple[str, ...]
    target: str
    note: Optional[str] = None

    @property
    def label(self) -> str:
        return format_dupe_label(self)


def format_dupe_label(op: DuplicationOp) -> str:

    fields = ", ".join(op.fields)
    text = f"Copy {op.source}[{fields}] -> {op.target}"
    if op.note:
        text = f"{text} ({op.note})"
    return text


def _strip_inline_comment(line: str) -> tuple[str, Optional[str]]:
    if "//" not in line:
        return line, None
    body, _, comment = line.partition("//")
    note = comment.strip() or None
    return body.rstrip(), note


def _parse_fields(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def parse_possibles_duplication(text: str) -> List[DuplicationOp]:

    ops: List[DuplicationOp] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("duplication"):
            continue

        body, note = _strip_inline_comment(line)
        match = _COPY_LINE.match(body.strip())
        if not match:
            continue

        fields = _parse_fields(match.group("fields"))
        if not fields:
            continue

        ops.append(
            DuplicationOp(
                source=match.group("source"),
                fields=fields,
                target=match.group("target"),
                note=note,
            )
        )

    return ops


def build_dupes_dict(
    ops: Iterable[DuplicationOp],
    *,
    id_prefix: str = "D",
    start_index: int = 1,
) -> Dict[str, str]:
    dupes: Dict[str, str] = {}
    for index, op in enumerate(ops, start=start_index):
        dupes[f"{id_prefix}{index}"] = op.label
    return dupes


def load_dupes(
    path: Optional[Union[str, Path]] = None,
    *,
    id_prefix: str = "D",
    start_index: int = 1,
    encoding: str = "utf-8",
) -> Dict[str, str]:

    file_path = Path(path) if path is not None else DEFAULT_POSSIBLES_DUPLICATION
    text = file_path.read_text(encoding=encoding)
    ops = parse_possibles_duplication(text)
    return build_dupes_dict(ops, id_prefix=id_prefix, start_index=start_index)


def load_dupe_ids(dupes: Dict[str, str]) -> List[str]:

    return list(dupes.keys())


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Gera DUPES a partir de possibles-duplication.js"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Caminho do arquivo (padrão: saída do launcher U-Schema)",
    )
    parser.add_argument("--prefix", default="D", help="Prefixo dos IDs")
    parser.add_argument("--json", action="store_true", help="Imprime JSON")
    args = parser.parse_args()

    dupes = load_dupes(args.path, id_prefix=args.prefix)
    if args.json:
        print(json.dumps(dupes, indent=2, ensure_ascii=False))
    else:
        for key, value in dupes.items():
            print(f'    "{key}": "{value}",')
