"""Простой поиск по базе знаний на BM25 без внешних зависимостей.

Нужен, чтобы Consultant Engine работал до подключения основного RAG.
Документы режутся по заголовкам Markdown, у каждого фрагмента сохраняется
путь к файлу и цепочка заголовков.
"""

from __future__ import annotations

import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

from consultant_contracts import RetrievedChunk

MAX_CHUNK_CHARS = 2000
TEXT_SUFFIXES = (".md", ".txt")

STOPWORDS = frozenset(
    """
    а без бы в во вам вас ваш ваша ваше ваши вы где да для до его ее если есть
    еще же за и из или им их к как ко когда ли либо мне мы на над не нет ни но
    нам нас наш наша наше наши о об обо однако он она они оно от по под при про
    с со так также то тоже только у уже чем что чтобы это этот эта эти я можно
    ли бы вообще какой какая какие какую сколько надо нужно будет было были
    """.split()
)

_TOKEN_RE = re.compile(r"[a-zа-я0-9]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _stem(token: str) -> str:
    # Грубая обрезка окончаний для русских слов, чтобы «станция» и «станцию»
    # совпадали без морфологического анализатора.
    if len(token) <= 4 or not ("а" <= token[0] <= "я"):
        return token
    return token[: min(6, max(4, len(token) - 2))]


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("ё", "е")
    return [_stem(t) for t in _TOKEN_RE.findall(text) if t not in STOPWORDS and len(t) > 1]


@dataclass(frozen=True)
class _Section:
    source: str
    headings: tuple[str, ...]
    body: str


def _split_sections(source: str, text: str) -> list[_Section]:
    sections: list[_Section] = []
    headings: list[tuple[int, str]] = []
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            sections.append(_Section(source, tuple(h for _, h in headings), body))
        lines.clear()

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            headings[:] = [h for h in headings if h[0] < level]
            headings.append((level, match.group(2)))
        else:
            lines.append(line)
    flush()
    return sections


def _split_long(body: str, limit: int) -> list[str]:
    if len(body) <= limit:
        return [body]
    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", body):
        if current and len(current) + len(paragraph) + 2 > limit:
            parts.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts


def chunk_documents(documents: Iterable[tuple[str, str]], limit: int = MAX_CHUNK_CHARS) -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    for source, text in documents:
        for section in _split_sections(source, text):
            title = " > ".join(section.headings)
            label = f"{source} > {title}" if title else source
            for part in _split_long(section.body, limit):
                chunk_text = f"{title}\n{part}" if title else part
                chunks.append(RetrievedChunk(text=chunk_text, source=label))
    return chunks


def read_documents(path: Union[str, Path]) -> list[tuple[str, str]]:
    """Читает .md и .txt из zip-архива или каталога."""
    path = Path(path)
    documents: list[tuple[str, str]] = []
    if path.is_dir():
        for file in sorted(path.rglob("*")):
            if file.is_file() and file.suffix.lower() in TEXT_SUFFIXES and not file.name.startswith("."):
                documents.append((file.relative_to(path).as_posix(), file.read_text("utf-8-sig")))
        return documents
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            parts = name.split("/")
            if name.endswith("/") or "__MACOSX" in parts or parts[-1].startswith("."):
                continue
            if not name.lower().endswith(TEXT_SUFFIXES):
                continue
            documents.append((name, archive.read(name).decode("utf-8-sig", errors="replace")))
    return documents


class KnowledgeBaseSearch:
    """BM25 по фрагментам базы знаний. Реализует протокол Retriever."""

    def __init__(self, chunks: list[RetrievedChunk], k1: float = 1.5, b: float = 0.75) -> None:
        self._chunks = chunks
        self._k1 = k1
        self._b = b
        self._term_counts = [Counter(tokenize(chunk.text)) for chunk in chunks]
        self._lengths = [sum(counts.values()) for counts in self._term_counts]
        self._avg_length = (sum(self._lengths) / len(self._lengths)) if chunks else 0.0
        doc_freq: Counter = Counter()
        for counts in self._term_counts:
            doc_freq.update(counts.keys())
        total = len(chunks)
        self._idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5)) for term, freq in doc_freq.items()
        }

    @classmethod
    def from_path(cls, path: Union[str, Path]) -> "KnowledgeBaseSearch":
        return cls(chunk_documents(read_documents(path)))

    def __len__(self) -> int:
        return len(self._chunks)

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        query_terms = Counter(tokenize(query))
        if not query_terms or not self._chunks or top_k <= 0:
            return []
        scored: list[tuple[float, int]] = []
        for index, counts in enumerate(self._term_counts):
            score = 0.0
            norm = self._k1 * (1 - self._b + self._b * self._lengths[index] / (self._avg_length or 1))
            for term, query_weight in query_terms.items():
                freq = counts.get(term)
                if freq:
                    score += query_weight * self._idf[term] * freq * (self._k1 + 1) / (freq + norm)
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        result: list[RetrievedChunk] = []
        for score, index in scored[:top_k]:
            chunk = self._chunks[index]
            result.append(RetrievedChunk(text=chunk.text, source=chunk.source, score=round(score, 4)))
        return result


def find_knowledge_base(data_dir: Path, extra_dirs: Iterable[Path] = ()) -> Optional[Path]:
    names = ("knowledge-base.zip", "knowledge_base.zip", "kb.zip", "knowledge-base")
    for base in (data_dir, *extra_dirs):
        for name in names:
            candidate = base / name
            if candidate.exists():
                return candidate
    return None
