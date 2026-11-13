"""Минимальные контракты конкурсного адаптера, RAG и Consultant Engine."""

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class RetrievedChunk:
    """Фрагмент базы знаний, найденный поиском.

    source имеет вид «путь/к/файлу.md > Заголовок > Подзаголовок». Путь берётся
    относительно корня knowledge-base.zip, заголовки идут от верхнего уровня.
    Эта строка показывается в режиме отладки и попадает в ConsultantResponse.sources.

    score может быть любой шкалы, больше значит релевантнее. Consultant Engine
    сравнивает score только внутри одной выдачи, поэтому абсолютная шкала не важна.
    Фрагменты без score не отсекаются.
    """

    text: str
    source: str
    score: Optional[float] = None


@dataclass
class ConsultantRequest:
    question: str
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ConsultantResponse:
    answer: str
    sources: list[str] = field(default_factory=list)


class Retriever(Protocol):
    """Поиск по базе знаний.

    query уже собран движком из вопроса и истории (consultant_engine.build_search_query),
    retriever его не переписывает и историю сам не разбирает. Результат отсортирован
    по убыванию релевантности. Метод вызывается из нескольких потоков одновременно.
    """

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]: ...


class Consultant(Protocol):
    def consult(self, request: ConsultantRequest) -> ConsultantResponse: ...
