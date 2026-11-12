"""Замер качества поиска на размеченных вопросах без вызова LLM.

Пример запуска из корня репозитория

    python3 tools/eval_retrieval.py \
        --questions local-data/demo_questions.csv \
        --labels local-data/retrieval_labels.csv \
        --kb local-data/knowledge-base.zip

Разметка это CSV с колонками id и expected. В expected через «|» перечислены
файлы базы, в которых есть ответ, например «23-solar.md|02-product-scope-and-limits.md».
Попаданием считается фрагмент из любого такого файла. Разметка строится по
материалам организаторов, поэтому хранится только локально в local-data/.

Другой retriever подключается так: --retriever модуль:функция, где функция
принимает путь к базе и возвращает объект с методом retrieve(query, top_k).
"""

from __future__ import annotations

import argparse
import csv
import importlib
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from consultant_contracts import RetrievedChunk, Retriever  # noqa: E402
from consultant_engine import build_search_query, filter_by_relative_score  # noqa: E402
from solution import parse_question, read_questions  # noqa: E402


def load_labels(path: Path) -> dict[str, list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {
            row["id"]: [name.strip() for name in row["expected"].split("|") if name.strip()]
            for row in csv.DictReader(stream)
        }


def load_factory(spec: str) -> Callable[[Path], Retriever]:
    module_name, _, attr_path = spec.partition(":")
    target = importlib.import_module(module_name)
    for attr in attr_path.split("."):
        target = getattr(target, attr)
    return target


def first_hit(chunks: list[RetrievedChunk], expected: list[str]) -> int:
    """Позиция первого попадания, начиная с 1, или 0, если попаданий нет."""
    for position, chunk in enumerate(chunks, start=1):
        file_part = chunk.source.split(" > ", 1)[0]
        if any(file_part.endswith(name) for name in expected):
            return position
    return 0


def evaluate(retriever: Retriever, cases: list[tuple[str, str, list[str]]], top_k: int, cutoffs: list[float]):
    rows = []
    misses: dict[float, list[str]] = {}
    for cutoff in cutoffs:
        hits_at = {1: 0, 3: 0, top_k: 0}
        reciprocal = 0.0
        kept_total = 0
        misses[cutoff] = []
        for item_id, raw_question, expected in cases:
            query = build_search_query(parse_question(raw_question))
            chunks = filter_by_relative_score(retriever.retrieve(query, top_k), cutoff)
            kept_total += len(chunks)
            position = first_hit(chunks, expected)
            if position:
                reciprocal += 1 / position
                for k in hits_at:
                    if position <= k:
                        hits_at[k] += 1
            else:
                misses[cutoff].append(item_id)
        total = len(cases) or 1
        rows.append(
            {
                "cutoff": cutoff,
                "hit@1": hits_at[1] / total,
                "hit@3": hits_at[3] / total,
                f"hit@{top_k}": hits_at[top_k] / total,
                "mrr": reciprocal / total,
                "avg_chunks": kept_total / total,
            }
        )
    return rows, misses


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--kb", type=Path, required=True)
    parser.add_argument("--retriever", default="kb_search:KnowledgeBaseSearch.from_path")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--cutoffs", default="0,0.2,0.3,0.4,0.5")
    args = parser.parse_args()

    labels = load_labels(args.labels)
    questions = read_questions(args.questions)
    cases = [(item_id, question, labels[item_id]) for item_id, question in questions if item_id in labels]
    if not cases:
        sys.exit("Нет вопросов с разметкой")
    retriever = load_factory(args.retriever)(args.kb)
    cutoffs = [float(value) for value in args.cutoffs.split(",")]
    rows, misses = evaluate(retriever, cases, args.top_k, cutoffs)

    print(f"Вопросов с разметкой {len(cases)}, retriever {args.retriever}, top_k {args.top_k}")
    header = list(rows[0])
    print("  ".join(f"{name:>10}" for name in header))
    for row in rows:
        print("  ".join(f"{row[name]:>10.2f}" for name in header))
    for cutoff, ids in misses.items():
        if ids:
            print(f"промахи при cutoff {cutoff}: {', '.join(ids)}")


if __name__ == "__main__":
    main()
