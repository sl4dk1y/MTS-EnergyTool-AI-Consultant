"""Минимальные контракты конкурсного адаптера, RAG и Consultant Engine."""

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass(frozen=True)
class RetrievedChunk:
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
    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]: ...


class Consultant(Protocol):
    def consult(self, request: ConsultantRequest) -> ConsultantResponse: ...
