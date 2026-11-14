"""HTTP API веб-прототипа консультанта EnergyTool.

Запуск из корня репозитория

    uvicorn backend.app:app --port 8000

Конкурсный solution.py этот модуль не использует.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from typing import Callable, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.storage import Storage
from consultant_contracts import ConsultantRequest
from consultant_engine import ConsultantEngine, build_default_engine
from leads import (
    REQUIRED_FIELDS,
    mask_contacts,
    missing_fields_message,
    prepare_lead,
    summarize_dialog,
    validate_lead,
    wants_to_leave_request,
)

logger = logging.getLogger("energytool.backend")

MAX_MESSAGE_CHARS = 4000
MAX_LOOKUP_IDS = 50
LEAD_SAVED_MESSAGE = (
    "Спасибо, заявка передана менеджеру МТС EnergyTool. Он свяжется с вами по указанному телефону. "
    "Если появятся вопросы, можно продолжить разговор здесь."
)
HANDOFF_MESSAGE = (
    "Передали ваш вопрос специалисту МТС EnergyTool. Чтобы он смог ответить, оставьте, пожалуйста, "
    "заявку с контактами."
)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class LeadFieldsIn(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=50)
    company: Optional[str] = Field(default=None, max_length=300)


class LeadIn(LeadFieldsIn):
    summary: Optional[str] = Field(default=None, max_length=2000)
    consent: bool = False


class SessionLookupIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=MAX_LOOKUP_IDS)


class HandoffIn(BaseModel):
    question: Optional[str] = Field(default=None, max_length=MAX_MESSAGE_CHARS)


class EngineProvider:
    """Лениво создаёт движок. Без ключей LLM API работает, а чат отвечает 503."""

    def __init__(self, factory: Callable[[], ConsultantEngine]) -> None:
        self._factory = factory
        self._engine: Optional[ConsultantEngine] = None
        self._lock = threading.Lock()

    def get(self) -> Optional[ConsultantEngine]:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    try:
                        self._engine = self._factory()
                    except Exception as exc:
                        # Тип ошибки без текста, в тексте может оказаться адрес или ключ.
                        logger.warning("Consultant Engine не создан: %s", type(exc).__name__)
                        return None
        return self._engine


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def build_web_engine() -> ConsultantEngine:
    """Движок веб-прототипа. В отличие от конкурсного, просит контакты для заявки."""
    return build_default_engine(collect_contacts=True)


def create_app(
    storage: Optional[Storage] = None,
    engine_factory: Callable[[], ConsultantEngine] = build_web_engine,
    admin_token: Optional[str] = None,
) -> FastAPI:
    storage = storage or Storage(os.environ.get("DATABASE_PATH", "local-data/web.sqlite3"))
    provider = EngineProvider(engine_factory)
    admin_token = admin_token if admin_token is not None else os.environ.get("ADMIN_TOKEN", "")
    removed = storage.purge_older_than(
        _int_env("SESSION_RETENTION_DAYS", 30), _int_env("LEAD_RETENTION_DAYS", 90)
    )
    if any(removed.values()):
        logger.info("Удалены устаревшие данные: %s", removed)

    app = FastAPI(title="MTS EnergyTool AI Consultant", version="0.1.0")
    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
    )

    def require_session(session_id: str) -> dict:
        session = storage.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Сессия не найдена")
        return session

    def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
        # Без ADMIN_TOKEN административные списки закрыты, а не открыты всем.
        if not admin_token:
            raise HTTPException(status_code=403, detail="Административный доступ выключен, задайте ADMIN_TOKEN")
        if not x_admin_token or not hmac.compare_digest(x_admin_token.encode(), admin_token.encode()):
            raise HTTPException(status_code=401, detail="Нужен токен администратора")

    def require_engine() -> ConsultantEngine:
        engine = provider.get()
        if engine is None:
            raise HTTPException(status_code=503, detail="Модель не настроена, проверьте LLM_BASE_URL, LLM_API_KEY и LLM_MODEL")
        return engine

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "engine": "ready" if provider.get() else "not_configured"}

    @app.post("/api/sessions", status_code=201)
    def create_session() -> dict:
        return storage.create_session()

    @app.get("/api/sessions", dependencies=[Depends(require_admin)])
    def list_sessions(limit: int = 100) -> list:
        return storage.list_sessions(min(max(limit, 1), 500))

    @app.post("/api/sessions/lookup")
    def lookup_sessions(body: SessionLookupIn) -> list:
        """История для клиента. Отдаёт только сессии, id которых клиент уже знает."""
        return storage.list_sessions_by_ids(body.ids)

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        session = require_session(session_id)
        return {**session, "messages": storage.get_messages(session_id)}

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> None:
        if not storage.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Сессия не найдена")

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, body: MessageIn) -> dict:
        require_session(session_id)
        engine = require_engine()
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Пустое сообщение")
        history = storage.get_history(session_id)
        started = time.monotonic()
        response = engine.consult(ConsultantRequest(question=text, history=history))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        user_message = storage.add_message(session_id, "user", text)
        assistant_message = storage.add_message(
            session_id, "assistant", response.answer, sources=response.sources, elapsed_ms=elapsed_ms
        )
        return {
            "user_message": user_message,
            "assistant_message": assistant_message,
            "lead_suggested": wants_to_leave_request(text),
        }

    @app.post("/api/sessions/{session_id}/lead/draft")
    def lead_draft(session_id: str, body: LeadFieldsIn) -> dict:
        require_session(session_id)
        # До согласия на обработку данных история с контактами в модель не уходит.
        # Черновик собирается регулярками и маскированным резюме из реплик клиента.
        draft = prepare_lead(
            storage.get_history(session_id),
            None,
            form={k: v for k, v in body.model_dump().items() if v},
        )
        return {**draft.to_dict(), "message": missing_fields_message(draft)}

    @app.post("/api/sessions/{session_id}/lead", status_code=201)
    def submit_lead(session_id: str, body: LeadIn) -> dict:
        require_session(session_id)
        if not body.consent:
            raise HTTPException(status_code=422, detail="Нужно согласие на обработку контактных данных")
        fields = {name: getattr(body, name) or "" for name in REQUIRED_FIELDS}
        known = [fields["full_name"], fields["phone"]]
        summary = mask_contacts((body.summary or "").strip(), known)
        if not summary:
            # Согласие получено, теперь резюме может написать модель.
            engine = provider.get()
            summary = summarize_dialog(storage.get_history(session_id), engine.llm if engine else None, known)
        draft = validate_lead(fields, summary or "Клиент оставил заявку без описания задачи.")
        if not draft.is_complete:
            raise HTTPException(
                status_code=422,
                detail={"message": missing_fields_message(draft), "draft": draft.to_dict()},
            )
        lead = storage.add_lead(session_id, draft.full_name, draft.phone, draft.company, draft.summary)
        storage.add_message(session_id, "assistant", LEAD_SAVED_MESSAGE)
        return lead

    @app.post("/api/sessions/{session_id}/handoff", status_code=201)
    def handoff(session_id: str, body: HandoffIn) -> dict:
        require_session(session_id)
        question = (body.question or "").strip()
        if not question:
            user_turns = [m["content"] for m in storage.get_history(session_id) if m["role"] == "user"]
            question = user_turns[-1] if user_turns else ""
        if not question:
            raise HTTPException(status_code=422, detail="Нет вопроса для передачи специалисту")
        record = storage.add_handoff(session_id, question)
        message = storage.add_message(session_id, "assistant", HANDOFF_MESSAGE)
        return {**record, "assistant_message": message}

    @app.get("/api/leads", dependencies=[Depends(require_admin)])
    def list_leads(limit: int = 100) -> list:
        return storage.list_leads(min(max(limit, 1), 500))

    @app.get("/api/handoffs", dependencies=[Depends(require_admin)])
    def list_handoffs(limit: int = 100) -> list:
        return storage.list_handoffs(min(max(limit, 1), 500))

    return app


def __getattr__(name: str):
    # uvicorn backend.app:app создаёт приложение при первом обращении,
    # а тесты импортируют модуль без побочных эффектов.
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)
