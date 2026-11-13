"""Поиск по базе знаний EnergyTool: чанкинг Markdown, BM25, эмбеддинги, реранкер.

Модуль отдаёт объект с методом ``retrieve(query, top_k)`` и подставляется в
``build_default_engine`` из ``consultant_engine.py`` вместо временного
``KnowledgeBaseSearch``.

Зависимости: только стандартная библиотека. ``numpy`` используется, если он
установлен, но не требуется. ``faiss``, ``rank-bm25``, ``langchain`` и прочие
пакеты не используются: разрешённый платформой набор зависимостей не
подтверждён, а в ``requirements.txt`` проекта сторонних пакетов нет.

Эмбеддинги и реранкер необязательны. Если прокси недоступен, поиск продолжает
работать на BM25 и не падает.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from consultant_contracts import RetrievedChunk

try:  # numpy не обязателен, без него считается чистым Python
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover - зависит от окружения
    _np = None


DEFAULT_TOP_K = 5
# Сколько кандидатов берём из каждого канала до объединения.
CANDIDATES_PER_CHANNEL = 20
# Сколько кандидатов отдаём реранкеру.
RERANK_CANDIDATES = 15
# Веса каналов при объединении нормированных скоров. Consultant Engine может
# отсекать фрагменты относительно лучшего (RELATIVE_SCORE_CUTOFF, по умолчанию
# 0.0 — отсечение выключено), поэтому скоры должны быть положительными и
# сопоставимыми по шкале: RRF даёт почти одинаковые значения и для отсева
# бесполезен.
LEXICAL_WEIGHT = 0.6
DENSE_WEIGHT = 0.4
# Минимальное косинусное сходство, при котором фрагмент векторного канала
# допускается в выдачу. Нужно, чтобы на посторонний вопрос вроде «сколько стоит
# билет» в контекст не попадали случайные разделы базы. Значение калибруется
# замером на размеченных вопросах: слишком высокий порог режет полезные
# фрагменты перефраз, слишком низкий пропускает мусор.
DENSE_MIN_SIMILARITY = 0.25

# Границы чанка в символах: 800 токенов ~ 3000-3200 символов для русского текста.
MAX_CHUNK_CHARS = 3200
MIN_CHUNK_CHARS = 400
OVERLAP_CHARS = 480

EMBED_BATCH_SIZE = 32

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+")
_SPLIT_PARAGRAPHS_RE = re.compile(r"\n\s*\n")

_RU_STOPWORDS = frozenset(
    """
    а без более бы был была были было быть в вам вас весь во вот все всего всех вы
    где да даже для до его ее если есть еще же за здесь и из или им их к как какая
    какие какой когда кто ли либо меня мне может мы на над надо наш не него нее нет
    ни них но ну о об однако он она они оно от очень по под при про раз с со так
    также такой там те тем то того тоже той только том ты у уже хотя чего чей чем
    что чтобы чье чья эта эти это я
    the a an and or but if then than that this these those is are was were be been
    to of in on at for with by from as not no
    """.split()
)


def _log(message: str) -> None:
    """Диагностика в stderr: stdout занят отчётом конкурсного скрипта."""
    print(f"[rag] {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Чанкинг
# --------------------------------------------------------------------------


def _normalize_token(token: str) -> str:
    """Грубое усечение окончаний: «симки» и «сим-картами» должны сойтись.

    Полноценная лемматизация тянет pymorphy и лишние зависимости, поэтому
    обрезаем частые русские окончания у достаточно длинных слов.
    """
    if len(token) <= 4:
        return token
    for suffix in ("ками", "ами", "ого", "ему", "ыми", "ими", "ий", "ый", "ая", "ые", "ой", "ей", "ам", "ах", "ов", "ев", "ка", "ки", "ке", "ку", "ы", "и", "а", "я", "е", "у", "ю"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _tokenize(text: str) -> List[str]:
    """Токены для BM25: нижний регистр, стоп-слова, усечение окончаний."""
    tokens: List[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if len(raw) < 2 or raw in _RU_STOPWORDS:
            continue
        tokens.append(_normalize_token(raw))
    return tokens


def _hard_split(text: str, max_chars: int) -> List[str]:
    """Режет строку, которая не влезает в лимит даже как один абзац.

    Строка без переносов и пробелов (длинная таблица, base64-подобный блок)
    иначе осталась бы одним чанком сверх лимита. Режем по границе лимита,
    стараясь попасть на пробел, но не теряя ни одного символа.
    """
    pieces: List[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars + 1)
        if cut < max_chars // 2:
            cut = max_chars
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


def _split_oversized(text: str, max_chars: int, overlap_chars: int) -> List[str]:
    """Дробит длинный блок по абзацам, не разрывая таблицы и списки."""
    paragraphs = [part for part in _SPLIT_PARAGRAPHS_RE.split(text) if part.strip()]
    if not paragraphs:
        return [text] if text.strip() else []

    pieces: List[str] = []
    current: List[str] = []

    def flush() -> None:
        if current:
            pieces.append("\n\n".join(current))

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush()
            current = []
            # Абзац сам длиннее лимита: режем по строкам, таблицу режем только
            # между строками, чтобы не порвать разметку.
            line_buffer: List[str] = []
            for line in paragraph.splitlines():
                if sum(len(item) for item in line_buffer) + len(line) > max_chars and line_buffer:
                    pieces.append("\n".join(line_buffer))
                    line_buffer = []
                line_buffer.append(line)
            if line_buffer:
                pieces.append("\n".join(line_buffer))
            continue
        if sum(len(item) for item in current) + len(paragraph) > max_chars and current:
            flush()
            tail = current[-1] if current and len(current[-1]) <= overlap_chars else ""
            current = [tail] if tail else []
        current.append(paragraph)
    flush()

    # Последняя страховка: любая строка, которая всё ещё длиннее лимита,
    # режется жёстко. Без неё чанк мог превысить MAX_CHUNK_CHARS.
    result: List[str] = []
    for piece in pieces:
        if piece.strip():
            result.extend(_hard_split(piece, max_chars))
    return result


def split_markdown(text: str, source_prefix: str) -> List[Tuple[str, str]]:
    """Возвращает пары (source, text) с иерархией заголовков в source.

    Формат source: ``путь/файл.md > Раздел > Подраздел``. Движок показывает
    этот же текст в режиме отладки веба.
    """
    lines = text.splitlines()
    stack: List[Tuple[int, str]] = []
    blocks: List[Tuple[Tuple[str, ...], List[str]]] = []
    current: List[str] = []

    def flush() -> None:
        if any(line.strip() for line in current):
            blocks.append((tuple(title for _, title in stack), list(current)))

    for line in lines:
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current = [line]
            continue
        current.append(line)
    flush()

    chunks: List[Tuple[str, str]] = []
    for headings, block_lines in blocks:
        block_text = "\n".join(block_lines).strip()
        if not block_text:
            continue
        source = source_prefix
        if headings:
            source = f"{source_prefix} > " + " > ".join(headings)
        for piece in _split_oversized(block_text, MAX_CHUNK_CHARS, OVERLAP_CHARS):
            chunks.append((source, piece))

    # Короткие хвосты приклеиваем к предыдущему чанку, если вместе влезают.
    # Склеивать можно только фрагменты одного раздела: иначе текст второго
    # раздела получит source первого, и движок покажет неверный источник.
    merged: List[Tuple[str, str]] = []
    for source, piece in chunks:
        previous_source, previous_text = merged[-1] if merged else ("", "")
        if (
            merged
            and source == previous_source
            and len(piece) < MIN_CHUNK_CHARS
            and len(previous_text) + len(piece) <= MAX_CHUNK_CHARS
        ):
            merged[-1] = (previous_source, previous_text + "\n\n" + piece)
            continue
        merged.append((source, piece))
    return merged


def chunk_knowledge_base(root: Path) -> List[Tuple[str, str]]:
    """Читает все .md и .txt из распакованной базы и режет их на чанки."""
    chunks: List[Tuple[str, str]] = []
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in (".md", ".txt")
    )
    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            _log(f"не удалось прочитать {path}: {type(exc).__name__}")
            continue
        relative = path.relative_to(root).as_posix()
        chunks.extend(split_markdown(text, relative))
    return chunks


# --------------------------------------------------------------------------
# BM25
# --------------------------------------------------------------------------


class _BM25:
    """Обычный BM25 без внешних зависимостей."""

    def __init__(self, tokenised_docs: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._docs = [list(tokens) for tokens in tokenised_docs]
        self._lengths = [len(tokens) for tokens in self._docs]
        self._avg_length = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        frequencies: List[Dict[str, int]] = []
        document_frequency: Dict[str, int] = {}
        for tokens in self._docs:
            counts: Dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            frequencies.append(counts)
            for token in counts:
                document_frequency[token] = document_frequency.get(token, 0) + 1
        self._frequencies = frequencies
        self._document_frequency = document_frequency
        self._total_docs = len(self._docs)

    def _idf(self, token: str) -> float:
        df = self._document_frequency.get(token, 0)
        if df == 0:
            return 0.0
        return math.log(1.0 + (self._total_docs - df + 0.5) / (df + 0.5))

    def search(self, tokens: Sequence[str], limit: int) -> List[Tuple[int, float]]:
        if not self._docs or not tokens:
            return []
        scores: List[Tuple[int, float]] = []
        average = self._avg_length or 1.0
        for index, counts in enumerate(self._frequencies):
            score = 0.0
            length = self._lengths[index]
            for token in tokens:
                frequency = counts.get(token)
                if not frequency:
                    continue
                idf = self._idf(token)
                denominator = frequency + self._k1 * (1 - self._b + self._b * length / average)
                score += idf * frequency * (self._k1 + 1) / denominator
            if score > 0:
                scores.append((index, score))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:limit]


# --------------------------------------------------------------------------
# Векторный канал
# --------------------------------------------------------------------------


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    """Косинус двух векторов. None, если сравнение бессмысленно.

    Векторы разной длины молча дают неверный результат при ``zip``, поэтому
    такая пара не сравнивается вовсе.
    """
    if not left or len(left) != len(right):
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


class _DenseIndex:
    """Косинусный поиск по нормализованным векторам."""

    def __init__(self, vectors: Sequence[Sequence[float]]) -> None:
        self._vectors = [list(vector) for vector in vectors]
        self._matrix = None
        if _np is not None and self._vectors:
            try:
                matrix = _np.asarray(self._vectors, dtype="float32")
                norms = _np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                self._matrix = matrix / norms
            except Exception:  # pragma: no cover
                self._matrix = None

    def __len__(self) -> int:
        return len(self._vectors)

    def search(self, vector: Sequence[float], limit: int) -> List[Tuple[int, float]]:
        if not self._vectors:
            return []
        if self._matrix is not None:
            if len(vector) != self._matrix.shape[1]:
                _log("векторный канал: размерность запроса не совпадает с индексом")
                return []
            scores = self._matrix.dot(_np.asarray(vector, dtype="float32"))
            order = _np.argsort(-scores)[:limit]
            return [(int(index), float(scores[index])) for index in order]
        scored: List[Tuple[int, float]] = []
        for index, candidate in enumerate(self._vectors):
            similarity = _cosine_similarity(vector, candidate)
            if similarity is None:
                continue
            scored.append((index, similarity))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


# --------------------------------------------------------------------------
# Вызовы прокси организаторов
# --------------------------------------------------------------------------


def _base_url() -> Optional[str]:
    return os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or None


def _api_key() -> str:
    return os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def _post_json(path: str, payload: dict, timeout: float, attempts: int = 3) -> dict:
    """POST на прокси с повторами и экспоненциальной паузой. Ключ не логируется."""
    base = _base_url()
    if not base:
        raise RuntimeError("LLM_BASE_URL не задан")
    url = base.rstrip("/") + path
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"запрос {path} не удался: {type(last_error).__name__}")


def _validate_vectors(vectors: Sequence[Sequence[float]]) -> int:
    """Проверяет размерность и конечность значений. Возвращает dim или 0."""
    if not vectors:
        _log("эмбеддинги: пустой ответ")
        return 0
    dimension = len(vectors[0])
    if dimension <= 0:
        _log("эмбеддинги: нулевая размерность")
        return 0
    for vector in vectors:
        if len(vector) != dimension:
            _log("эмбеддинги: векторы разной длины, плотный канал отключён")
            return 0
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                _log("эмбеддинги: нечисловое значение, плотный канал отключён")
                return 0
            if not math.isfinite(float(value)):
                _log("эмбеддинги: NaN или бесконечность, плотный канал отключён")
                return 0
    return dimension


def _embed_texts(texts: Sequence[str], timeout: float = 30.0) -> List[List[float]]:
    model = os.environ.get("EMB_MODEL")
    if not model:
        raise RuntimeError("EMB_MODEL не задан")
    vectors: List[List[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = list(texts[start : start + EMBED_BATCH_SIZE])
        payload = _post_json("/embeddings", {"model": model, "input": batch}, timeout)
        data = payload.get("data") or []
        if len(data) != len(batch):
            raise RuntimeError("прокси вернул неверное число векторов")
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        vectors.extend(list(item["embedding"]) for item in ordered)
    if _validate_vectors(vectors) == 0:
        raise RuntimeError("эмбеддинги не прошли проверку размерности или значений")
    return vectors


def _rerank(query: str, documents: Sequence[str], timeout: float = 20.0) -> Optional[List[float]]:
    """Скоры реранкера в порядке documents. None, если ответ невалиден.

    Неполный или битый ответ отбрасывается целиком: подставлять пропущенным
    документам нейтральный скор нельзя, иначе они обгонят реальные результаты.
    """
    model = os.environ.get("RERANKER_MODEL")
    if not model:
        return None
    try:
        payload = _post_json(
            "/rerank",
            {"model": model, "query": query, "documents": list(documents)},
            timeout,
            attempts=2,
        )
    except Exception as exc:
        _log(f"реранкер недоступен: {type(exc).__name__}")
        return None
    if not isinstance(payload, dict):
        # Прокси может вернуть JSON null, список или строку. Обращение к .get()
        # на таком значении уронило бы весь поиск и потеряло результаты BM25.
        _log("реранкер: ответ не объект, оставляем исходный порядок")
        return None
    items = payload.get("results") or payload.get("data") or []
    if not isinstance(items, list) or len(items) != len(documents):
        _log("реранкер: число результатов не совпадает с числом документов")
        return None
    scores: List[float] = [0.0] * len(documents)
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            _log("реранкер: элемент ответа не объект")
            return None
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            _log("реранкер: индекс не целое число")
            return None
        if not 0 <= index < len(documents) or index in seen:
            _log("реранкер: индекс вне диапазона или повторяется")
            return None
        seen.add(index)
        raw = item.get("relevance_score", item.get("score"))
        if raw is None or isinstance(raw, bool):
            _log("реранкер: отсутствует оценка")
            return None
        try:
            score = float(raw)
        except (TypeError, ValueError):
            _log("реранкер: оценка не число")
            return None
        if not math.isfinite(score):
            _log("реранкер: оценка не конечное число")
            return None
        scores[index] = score
    return scores


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-min(value, 60.0)))
    exponent = math.exp(max(value, -60.0))
    return exponent / (1.0 + exponent)


# --------------------------------------------------------------------------
# База знаний
# --------------------------------------------------------------------------


_KB_CACHE: Dict[str, Path] = {}
_KB_LOCK = threading.Lock()


def _extract_zip(archive: Path) -> Path:
    key = f"{archive}|{archive.stat().st_mtime_ns}|{archive.stat().st_size}"
    with _KB_LOCK:
        cached = _KB_CACHE.get(key)
        if cached and cached.is_dir():
            return cached
        target = Path(tempfile.mkdtemp(prefix="kb-"))
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                # Защита от выхода за пределы каталога распаковки.
                name = member.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                bundle.extract(member, target)
        _KB_CACHE[key] = target
        return target


def find_knowledge_base(data_dir: Optional[Path] = None) -> Optional[Path]:
    """Ищет knowledge-base.zip (или распакованный каталог) рядом с данными."""
    root = Path(data_dir) if data_dir else Path(os.environ.get("DATA_DIR", "data"))
    archive_names = ("knowledge-base.zip", "knowledge_base.zip")
    directory_names = ("knowledge-base", "knowledge_base", "kb")
    project_root = Path(__file__).resolve().parent
    bases = [root, project_root / root, Path.cwd()]

    for base in bases:
        for name in directory_names:
            candidate = base / name
            if candidate.is_dir():
                return candidate
        for name in archive_names:
            candidate = base / name
            if candidate.is_file():
                try:
                    return _extract_zip(candidate)
                except (OSError, zipfile.BadZipFile) as exc:
                    _log(f"не удалось распаковать {candidate.name}: {type(exc).__name__}")
                    return None
    return None


# --------------------------------------------------------------------------
# Поиск
# --------------------------------------------------------------------------


class RagRetriever:
    """Гибридный поиск: BM25 + эмбеддинги, объединение RRF, реранкер по желанию."""

    def __init__(
        self,
        kb_path: Optional[Path] = None,
        top_k: int = DEFAULT_TOP_K,
        use_dense: Optional[bool] = None,
    ) -> None:
        # Путь может прийти строкой из окружения или от движка.
        self._kb_path = Path(kb_path) if kb_path is not None else None
        self._top_k = top_k
        self._use_dense = _dense_enabled() if use_dense is None else use_dense
        self._lock = threading.Lock()
        self._ready = False
        self._chunks: List[Tuple[str, str]] = []
        self._bm25: Optional[_BM25] = None
        self._dense: Optional[_DenseIndex] = None

    # -- построение индекса -------------------------------------------------

    def _ensure_built(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            try:
                self._build()
            except Exception as exc:
                # Без базы поиск бесполезен, но вызывающая сторона не должна падать.
                _log(f"индекс не построен: {type(exc).__name__}")
                self._chunks = []
                self._bm25 = None
                self._dense = None
            finally:
                self._ready = True

    def _build(self) -> None:
        started = time.monotonic()
        root = self._kb_path or find_knowledge_base()
        # Замер и платформа могут передать путь к zip-архиву, а не к каталогу.
        if root is not None and root.is_file():
            try:
                root = _extract_zip(root)
            except (OSError, zipfile.BadZipFile) as exc:
                _log(f"не удалось распаковать базу: {type(exc).__name__}")
                return
        if root is None or not root.is_dir():
            _log("база знаний не найдена")
            return
        chunks = chunk_knowledge_base(root)
        if not chunks:
            _log("в базе знаний нет текстовых файлов")
            return
        self._chunks = chunks
        self._bm25 = _BM25([_tokenize(text) for _, text in chunks])
        dense: Optional[_DenseIndex] = None
        if self._use_dense:
            try:
                vectors = _embed_texts([text for _, text in chunks])
                dense = _DenseIndex(vectors)
                _log(f"эмбеддинги получены: {len(vectors)} векторов")
            except Exception as exc:
                _log(f"эмбеддинги недоступны, работаем на BM25: {type(exc).__name__}")
        self._dense = dense
        _log(
            "индекс готов: {0} чанков, {1} с, плотный канал: {2}".format(
                len(chunks), round(time.monotonic() - started, 2), "да" if dense else "нет"
            )
        )

    # -- поиск --------------------------------------------------------------

    def retrieve(self, query: str, top_k: int = DEFAULT_TOP_K) -> List[RetrievedChunk]:
        self._ensure_built()
        if not self._chunks or self._bm25 is None:
            return []
        query = (query or "").strip()
        if not query:
            return []
        started = time.monotonic()
        try:
            ranked = self._search(query)
        except Exception as exc:
            _log(f"поиск не удался: {type(exc).__name__}")
            return []
        limit = top_k if top_k and top_k > 0 else self._top_k
        result = [
            RetrievedChunk(text=self._chunks[index][1], source=self._chunks[index][0], score=score)
            for index, score in ranked[:limit]
        ]
        _log(
            "поиск: {0} симв., {1} результатов, {2} с".format(
                len(query), len(result), round(time.monotonic() - started, 2)
            )
        )
        return result

    def _search(self, query: str) -> List[Tuple[int, float]]:
        assert self._bm25 is not None
        tokens = _tokenize(query)
        lexical = self._bm25.search(tokens, CANDIDATES_PER_CHANNEL)
        vectors: List[Tuple[int, float]] = []
        if self._dense is not None:
            try:
                vectors = self._dense.search(self._query_vector(query), CANDIDATES_PER_CHANNEL)
            except Exception as exc:
                # Таймаут или ошибка эмбеддингов не должны обнулять выдачу:
                # результаты BM25 уже получены и остаются валидными.
                _log(f"векторный канал недоступен, остаёмся на BM25: {type(exc).__name__}")
                vectors = []

        fused: Dict[int, float] = {}
        lexical_max = max((score for _, score in lexical), default=0.0)
        for index, score in lexical:
            fused[index] = LEXICAL_WEIGHT * (score / lexical_max if lexical_max > 0 else 0.0)

        kept = 0
        for index, similarity in vectors:
            if similarity < DENSE_MIN_SIMILARITY:
                continue
            kept += 1
            # Косинус лежит в [-1, 1], переводим в [0, 1].
            fused[index] = fused.get(index, 0.0) + DENSE_WEIGHT * ((similarity + 1.0) / 2.0)
        dropped = len(vectors) - kept
        if dropped:
            _log(
                "векторный канал: отсечено {0} фрагментов ниже порога {1}".format(
                    dropped, DENSE_MIN_SIMILARITY
                )
            )
        if not fused:
            return []

        candidates = sorted(fused.items(), key=lambda item: item[1], reverse=True)
        head = candidates[:RERANK_CANDIDATES]
        # Реранкер сам вернёт None, если RERANKER_MODEL не задан.
        scores = _rerank(query, [self._chunks[index][1] for index, _ in head])
        if scores is None:
            return [(index, score) for index, score in candidates]
        # Логиты реранкера приводятся к (0, 1): движок может включить
        # относительный отсев, а отрицательные скоры его ломают.
        rescored = [(head[position][0], _sigmoid(scores[position])) for position in range(len(head))]
        rescored.sort(key=lambda item: item[1], reverse=True)
        rescored.extend(candidates[len(head) :])
        return rescored

    def _query_vector(self, query: str) -> List[float]:
        assert self._dense is not None
        vectors = _embed_texts([query])
        return vectors[0]


def _dense_enabled() -> bool:
    """Плотный канал включается, только если прокси и модель эмбеддингов заданы."""
    if os.environ.get("RAG_DISABLE_DENSE"):
        return False
    return bool(_base_url() and os.environ.get("EMB_MODEL"))


# --------------------------------------------------------------------------
# Точка подключения к движку
# --------------------------------------------------------------------------


def from_path(kb_path: Path) -> RagRetriever:
    """Фабрика для tools/eval_retrieval.py: --retriever rag_engine:from_path.

    Скрипт замера передаёт путь к базе и ждёт объект с методом ``retrieve``.
    """
    return RagRetriever(Path(kb_path))


def build_default_retriever() -> Optional[RagRetriever]:
    """Собирает retriever из окружения конкурсной платформы.

    В ``build_default_engine`` заменяет строку с ``KnowledgeBaseSearch.from_path``.
    Возвращает None, если база не найдена: движок в этом случае работает без RAG.
    """
    try:
        top_k = int(os.environ.get("TOP_K", DEFAULT_TOP_K))
    except ValueError:
        top_k = DEFAULT_TOP_K
    root = find_knowledge_base()
    if root is None:
        _log("build_default_retriever: база знаний не найдена")
        return None
    return RagRetriever(root, top_k=top_k)
