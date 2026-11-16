"""Конкурсный адаптер: вопросы из CSV → Consultant Engine → submission.csv."""

from __future__ import annotations

import csv
import importlib
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from consultant_contracts import ConsultantRequest, ConsultantResponse

START_TIME = time.monotonic()
MAX_ANSWER_CHARS = 10_000
DEFAULT_MAX_WORKERS = 4
# Как у учебного примера организаторов. На каждый вопрос уходит 3-4 запроса в прокси
# (эмбеддинги, реранкер, модель), больше 8 потоков легко упираются в его лимиты.
MAX_WORKERS_LIMIT = 8
# Платформа даёт 20 минут на запуск. Оставляем запас на загрузку и запись CSV.
DEFAULT_TIME_BUDGET_SECONDS = 17 * 60
TIMEOUT_ANSWER = (
    "Чтобы ответить точно, нам нужно чуть больше сведений о вашем объекте и задаче. "
    "Опишите, пожалуйста, ситуацию подробнее или оставьте заявку, и специалист "
    "МТС EnergyTool свяжется с вами."
)
# Тот же нейтральный ответ при сбое движка на отдельном вопросе.
FALLBACK_ANSWER = TIMEOUT_ANSWER
# Подписи ролей в вопросах с историей. В демо «Пользователь» и «Ассистент»,
# остальные варианты на случай другого оформления в скрытом наборе.
ROLE_LABELS = {
    "пользователь": "user",
    "клиент": "user",
    "user": "user",
    "ассистент": "assistant",
    "консультант": "assistant",
    "бот": "assistant",
    "assistant": "assistant",
}


class InputFormatError(ValueError):
    """Входной CSV не соответствует контракту конкурса."""


class ConsultantIntegrationError(RuntimeError):
    """Consultant Engine не подключён либо нарушил внутренний контракт."""


def parse_question(raw_question: str) -> ConsultantRequest:
    """Выделяет историю из официального многострочного формата.

    Demo-вопросы используют строки «Пользователь: ...» и «Ассистент: ...».
    Если весь текст не соответствует этому формату, он передаётся как вопрос
    без преобразований, чтобы не потерять содержимое.
    """
    lines = raw_question.splitlines()
    turns: list[dict[str, str]] = []
    current_role: Optional[str] = None
    current_parts: list[str] = []
    recognized = False

    def flush() -> None:
        nonlocal current_role, current_parts
        if current_role is not None:
            turns.append({"role": current_role, "content": "\n".join(current_parts).strip()})
        current_role = None
        current_parts = []

    for line in lines:
        prefix, separator, rest = line.partition(":")
        role = ROLE_LABELS.get(prefix.strip().casefold()) if separator else None
        if role is not None:
            recognized = True
            flush()
            current_role = role
            current_parts = [rest.lstrip()]
        elif current_role is not None:
            current_parts.append(line)
        elif line.strip():
            return ConsultantRequest(question=raw_question)
    flush()

    if not recognized or not turns or turns[-1]["role"] != "user":
        return ConsultantRequest(question=raw_question)
    final_question = turns[-1]["content"]
    history = turns[:-1]
    if not final_question:
        return ConsultantRequest(question=raw_question)
    return ConsultantRequest(question=final_question, history=history)


def read_questions(path: Path) -> list[tuple[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames is None:
            raise InputFormatError("Входной CSV пуст или не содержит заголовка")
        if "id" not in reader.fieldnames or "question" not in reader.fieldnames:
            raise InputFormatError("В questions.csv обязательны колонки id и question")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise InputFormatError("В questions.csv повторяются имена колонок")
        questions: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise InputFormatError(f"Некорректное число CSV-полей в строке {row_number}")
            item_id = row["id"]
            question = row["question"]
            if not item_id.strip():
                raise InputFormatError(f"Пустой ID в строке {row_number}")
            if item_id in seen_ids:
                raise InputFormatError(f"Повтор ID во входном CSV в строке {row_number}")
            if not question.strip():
                raise InputFormatError(f"Пустой вопрос в строке {row_number}")
            seen_ids.add(item_id)
            questions.append((item_id, question))
    return questions


def write_submission(path: Path, rows: Iterable[tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=path.parent, delete=False
        ) as stream:
            temp_name = stream.name
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(("id", "answer"))
            for item_id, answer in rows:
                if not answer.strip():
                    raise ConsultantIntegrationError(f"Пустой ответ для ID {item_id}")
                if len(answer) > MAX_ANSWER_CHARS:
                    raise ConsultantIntegrationError(f"Ответ для ID {item_id} превышает лимит символов")
                writer.writerow((item_id, answer))
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


Outcome = tuple[Optional[ConsultantResponse], Optional[BaseException]]


def _consult_all(
    requests: list[ConsultantRequest],
    consult: Callable[[ConsultantRequest], ConsultantResponse],
    max_workers: int,
    deadline: Optional[float],
) -> list[Optional[Outcome]]:
    """Обрабатывает вопросы в нескольких потоках с сохранением порядка.

    Для вопросов, не успевших получить ответ до deadline, возвращается None.
    Потоки служебные (daemon), поэтому зависший запрос не задержит выход
    процесса после записи submission.csv.
    """
    outcomes: list[Optional[Outcome]] = [None] * len(requests)
    if not requests:
        return outcomes
    pending: queue.Queue = queue.Queue()
    for index in range(len(requests)):
        pending.put(index)
    lock = threading.Lock()
    finished = threading.Event()
    remaining = [len(requests)]

    def worker() -> None:
        while deadline is None or time.monotonic() < deadline:
            try:
                index = pending.get_nowait()
            except queue.Empty:
                return
            try:
                outcome: Outcome = (consult(requests[index]), None)
            except Exception as exc:
                outcome = (None, exc)
            with lock:
                outcomes[index] = outcome
                remaining[0] -= 1
                if remaining[0] == 0:
                    finished.set()

    for _ in range(max(1, min(max_workers, len(requests)))):
        threading.Thread(target=worker, daemon=True).start()
    finished.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))
    with lock:
        return list(outcomes)


def run_adapter(
    questions_path: Path,
    output_path: Path,
    consult: Callable[[ConsultantRequest], ConsultantResponse],
    max_workers: int = 1,
    deadline: Optional[float] = None,
    fallback: Optional[Callable[[ConsultantRequest], str]] = None,
) -> int:
    """deadline задаётся в единицах time.monotonic().

    fallback даёт быстрый ответ без модели для вопросов, где основной путь
    не сработал или не успел. Если его нет, пишется общий запасной текст.
    """
    questions = read_questions(questions_path)
    requests = [parse_question(raw_question) for _, raw_question in questions]
    outcomes = _consult_all(requests, consult, max_workers, deadline)
    results: list[tuple[str, str]] = []
    replaced = 0
    for row_number, ((item_id, _), outcome, request) in enumerate(zip(questions, outcomes, requests), start=2):
        answer, problem = _answer_from_outcome(outcome)
        if problem:
            replaced += 1
            answer = _quick_answer(fallback, request) or answer
            # Один сбой не должен оставить весь запуск без submission.csv.
            # Печатаем только номер строки и тип проблемы, без текста исключения,
            # в нём могут оказаться URL, заголовки или фрагмент запроса.
            print(f"Строка {row_number}: {problem}, записан запасной ответ", file=sys.stderr)
        results.append((item_id, answer))
    write_submission(output_path, results)
    if replaced:
        print(f"Запасных ответов: {replaced} из {len(results)}", file=sys.stderr)
    return len(results)


def _quick_answer(fallback: Optional[Callable[[ConsultantRequest], str]], request: ConsultantRequest) -> str:
    if fallback is None:
        return ""
    try:
        answer = fallback(request)
    except Exception:
        return ""
    answer = answer.strip() if isinstance(answer, str) else ""
    return answer[:MAX_ANSWER_CHARS]


def _answer_from_outcome(outcome: Optional[Outcome]) -> tuple[str, str]:
    """Возвращает ответ для CSV и описание проблемы, если ответ запасной."""
    if outcome is None:
        return TIMEOUT_ANSWER, "не успели до конца бюджета времени"
    response, error = outcome
    if error is not None:
        return FALLBACK_ANSWER, f"ошибка Consultant Engine ({type(error).__name__})"
    if not isinstance(response, ConsultantResponse):
        return FALLBACK_ANSWER, "Engine вернул не ConsultantResponse"
    answer = response.answer.strip() if isinstance(response.answer, str) else ""
    if not answer:
        return FALLBACK_ANSWER, "Engine вернул пустой ответ"
    if len(answer) > MAX_ANSWER_CHARS:
        return answer[:MAX_ANSWER_CHARS].rstrip(), ""
    return answer, ""


def _find_input(data_dir: Path, filename: str) -> Path:
    candidates = (
        data_dir / filename,
        Path(__file__).resolve().parent / data_dir / filename,
        Path.cwd() / filename,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Не найден обязательный входной файл {filename}")


def _load_engine() -> Callable[[ConsultantRequest], ConsultantResponse]:
    try:
        module = importlib.import_module("consultant_engine")
        consult = getattr(module, "consult")
    except (ImportError, AttributeError) as exc:
        raise ConsultantIntegrationError(
            "Consultant Engine не подключён: ожидается consultant_engine.consult(request)"
        ) from None
    if not callable(consult):
        raise ConsultantIntegrationError("consultant_engine.consult должен быть вызываемым")
    return consult


def _engine_extras() -> tuple[Optional[Callable[[ConsultantRequest], str]], Callable[[], dict]]:
    """Необязательные функции движка: быстрый ответ из базы и счётчики."""
    try:
        module = importlib.import_module("consultant_engine")
    except ImportError:
        return None, dict
    fallback = getattr(module, "fallback_answer", None)
    stats = getattr(module, "stats", None)
    return (fallback if callable(fallback) else None), (stats if callable(stats) else dict)


def _find_first(data_dir: Path, names: Iterable[str]) -> Path:
    last_error: Optional[FileNotFoundError] = None
    for name in names:
        try:
            return _find_input(data_dir, name)
        except FileNotFoundError as exc:
            last_error = exc
    raise last_error or FileNotFoundError("Не найден входной файл")


def _positive_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    # Регламент называет файл questions.csv, учебный пример принимает и train.csv.
    questions_path = _find_first(data_dir, ("questions.csv", "train.csv"))
    # Базу знаний ищет сам Consultant Engine, он понимает разные имена архива.
    max_workers = min(MAX_WORKERS_LIMIT, int(_positive_env("MAX_WORKERS", DEFAULT_MAX_WORKERS)))
    deadline = START_TIME + _positive_env("TIME_BUDGET_SECONDS", DEFAULT_TIME_BUDGET_SECONDS)
    consult = _load_engine()
    fallback, stats = _engine_extras()
    count = run_adapter(
        questions_path,
        Path("submission.csv"),
        consult,
        max_workers=max_workers,
        deadline=deadline,
        fallback=fallback,
    )
    elapsed = round(time.monotonic() - START_TIME)
    print(f"Создан submission.csv: {count} ответов, потоков {max_workers}, {elapsed} с")
    try:
        print(f"Пути ответов: {stats()}", file=sys.stderr)
    except Exception:
        pass


if __name__ == "__main__":
    main()
