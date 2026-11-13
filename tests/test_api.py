import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # конкурсное окружение и CI без зависимостей веба
    raise unittest.SkipTest("FastAPI не установлен, ставится из backend/requirements.txt")

from backend.app import LEAD_SAVED_MESSAGE, create_app
from backend.storage import Storage
from consultant_contracts import RetrievedChunk
from consultant_engine import ConsultantEngine

# Только выдуманные данные.
LEAD = {"full_name": "тестов тест", "phone": "8 900 123-45-67", "company": "ООО Ромашка", "consent": True}


class EchoLLM:
    """Отвечает номером вызова и запоминает, что ему передали."""

    def __init__(self):
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        if messages[0]["content"].startswith("Из реплик клиента"):
            return json.dumps({"full_name": "", "phone": "", "company": ""})
        if messages[0]["content"].startswith("Ты помогаешь менеджеру"):
            return "Клиенту нужна солнечная станция на складе."
        return f"Ответ {len(self.calls)}"


class OneChunkRetriever:
    def retrieve(self, query, top_k=5):
        return [RetrievedChunk(text="Про солнечные станции.", source="23-solar.md > Размещение", score=1.0)]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "web.sqlite3"
        self.storage = Storage(self.db_path)
        self.llm = EchoLLM()
        engine = ConsultantEngine(self.llm, OneChunkRetriever())
        self.client = TestClient(create_app(self.storage, engine_factory=lambda: engine, admin_token=""))

    def tearDown(self):
        self.temp.cleanup()

    def new_session(self):
        response = self.client.post("/api/sessions")
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def say(self, session_id, text):
        response = self.client.post(f"/api/sessions/{session_id}/messages", json={"text": text})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok", "engine": "ready"})

    def test_chat_keeps_history_and_returns_debug_info(self):
        session_id = self.new_session()
        first = self.say(session_id, "Хотим солнечную станцию")
        self.assertEqual(first["assistant_message"]["sources"], ["23-solar.md > Размещение"])
        self.assertIsInstance(first["assistant_message"]["elapsed_ms"], int)
        self.say(session_id, "А на фасад можно?")
        sent = self.llm.calls[-1]
        self.assertEqual([m["role"] for m in sent], ["system", "user", "assistant", "user"])
        self.assertEqual(sent[1]["content"], "Хотим солнечную станцию")
        self.assertIn("Вопрос клиента:\nА на фасад можно?", sent[-1]["content"])
        messages = self.client.get(f"/api/sessions/{session_id}").json()["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user", "assistant"])

    def test_sessions_are_isolated(self):
        first = self.new_session()
        second = self.new_session()
        self.say(first, "Секрет первой сессии")
        self.say(second, "Вопрос второй сессии")
        sent = self.llm.calls[-1]
        self.assertNotIn("Секрет первой сессии", [m["content"] for m in sent])
        self.assertEqual(len(self.client.get(f"/api/sessions/{second}").json()["messages"]), 2)

    def test_history_survives_restart(self):
        session_id = self.new_session()
        self.say(session_id, "Вопрос без заявки")
        restarted = TestClient(create_app(Storage(self.db_path), engine_factory=lambda: None, admin_token=""))
        sessions = restarted.get("/api/sessions").json()
        self.assertEqual(sessions[0]["id"], session_id)
        self.assertFalse(sessions[0]["has_lead"])
        self.assertEqual(sessions[0]["title"], "Вопрос без заявки")

    def test_unknown_session_and_empty_text(self):
        self.assertEqual(self.client.post("/api/sessions/nope/messages", json={"text": "x"}).status_code, 404)
        session_id = self.new_session()
        self.assertEqual(self.client.post(f"/api/sessions/{session_id}/messages", json={"text": ""}).status_code, 422)
        self.assertEqual(
            self.client.post(f"/api/sessions/{session_id}/messages", json={"text": "   "}).status_code, 422
        )

    def test_lead_intent_flag(self):
        session_id = self.new_session()
        self.assertTrue(self.say(session_id, "Хочу оставить заявку")["lead_suggested"])
        self.assertFalse(self.say(session_id, "Сколько стоит учёт?")["lead_suggested"])

    def test_lead_draft_reports_missing_fields(self):
        session_id = self.new_session()
        self.say(session_id, "Нужна станция на складе, телефон 8 900 123-45-67")
        draft = self.client.post(f"/api/sessions/{session_id}/lead/draft", json={"company": "Ромашка"}).json()
        self.assertEqual(draft["phone"], "+79001234567")
        self.assertEqual(draft["company"], "Ромашка")
        self.assertEqual(draft["missing"], ["full_name"])
        self.assertEqual(draft["summary"], "Клиенту нужна солнечная станция на складе.")
        self.assertIn("ФИО", draft["message"])

    def test_lead_submit_flow(self):
        session_id = self.new_session()
        self.say(session_id, "Нужна станция на складе")
        response = self.client.post(f"/api/sessions/{session_id}/lead", json=LEAD)
        self.assertEqual(response.status_code, 201, response.text)
        lead = response.json()
        self.assertEqual(lead["phone"], "+79001234567")
        self.assertEqual(lead["full_name"], "Тестов Тест")
        self.assertEqual(lead["summary"], "Клиенту нужна солнечная станция на складе.")
        leads = self.client.get("/api/leads").json()
        self.assertEqual([item["id"] for item in leads], [lead["id"]])
        messages = self.client.get(f"/api/sessions/{session_id}").json()["messages"]
        self.assertEqual(messages[-1]["content"], LEAD_SAVED_MESSAGE)
        self.assertTrue(self.client.get("/api/sessions").json()[0]["has_lead"])

    def test_lead_requires_consent_and_valid_fields(self):
        session_id = self.new_session()
        response = self.client.post(f"/api/sessions/{session_id}/lead", json={**LEAD, "consent": False})
        self.assertEqual(response.status_code, 422)
        response = self.client.post(f"/api/sessions/{session_id}/lead", json={**LEAD, "phone": "123"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("phone", response.json()["detail"]["draft"]["errors"])
        self.assertEqual(self.client.get("/api/leads").json(), [])

    def test_handoff_uses_last_question(self):
        session_id = self.new_session()
        self.say(session_id, "Как настроить конкретный счётчик?")
        response = self.client.post(f"/api/sessions/{session_id}/handoff", json={})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["question"], "Как настроить конкретный счётчик?")
        self.assertEqual(len(self.client.get("/api/handoffs").json()), 1)
        empty = self.new_session()
        self.assertEqual(self.client.post(f"/api/sessions/{empty}/handoff", json={}).status_code, 422)

    def test_delete_session(self):
        session_id = self.new_session()
        self.say(session_id, "Вопрос")
        self.assertEqual(self.client.delete(f"/api/sessions/{session_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/sessions/{session_id}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/sessions/{session_id}").status_code, 404)

    def test_admin_token_protects_lists(self):
        client = TestClient(create_app(self.storage, engine_factory=lambda: None, admin_token="secret"))
        self.assertEqual(client.get("/api/leads").status_code, 401)
        self.assertEqual(client.get("/api/sessions").status_code, 401)
        self.assertEqual(client.get("/api/leads", headers={"X-Admin-Token": "secret"}).status_code, 200)
        self.assertEqual(client.post("/api/sessions").status_code, 201)

    def test_without_model_chat_is_503_but_lead_works(self):
        def broken():
            raise RuntimeError("LLM_API_KEY=secret")

        client = TestClient(create_app(self.storage, engine_factory=broken, admin_token=""))
        self.assertEqual(client.get("/api/health").json()["engine"], "not_configured")
        session_id = client.post("/api/sessions").json()["id"]
        response = client.post(f"/api/sessions/{session_id}/messages", json={"text": "Вопрос"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text)
        response = client.post(f"/api/sessions/{session_id}/lead", json=LEAD)
        self.assertEqual(response.status_code, 201)

    def test_retention_purges_old_sessions(self):
        old = self.new_session()
        fresh = self.new_session()
        stale = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
        with self.storage._connect() as db:
            db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (stale, old))
        removed = self.storage.purge_older_than(30, 90)
        self.assertEqual(removed["sessions"], 1)
        self.assertIsNone(self.storage.get_session(old))
        self.assertIsNotNone(self.storage.get_session(fresh))


if __name__ == "__main__":
    unittest.main()
