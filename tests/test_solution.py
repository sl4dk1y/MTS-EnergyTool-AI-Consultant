import csv
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from consultant_contracts import ConsultantResponse
from llm_client import LLMAPIError, LLMClient, LLMConfigurationError
from solution import (
    TIMEOUT_ANSWER,
    ConsultantIntegrationError,
    InputFormatError,
    parse_question,
    read_questions,
    run_adapter,
)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_path = self.root / "questions.csv"
        self.output_path = self.root / "submission.csv"

    def tearDown(self):
        self.temp.cleanup()

    def write_input(self, rows, header=("id", "question")):
        with self.input_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows)

    def test_csv_quotes_newlines_order_and_independent_requests(self):
        transcript = "Пользователь: Первый вопрос\nАссистент: Ответ\nПользователь: Последний, \"вопрос\"\nсо второй строкой"
        self.write_input([("alpha", "Одиночный вопрос"), ("beta", transcript)])
        calls = []

        def consult(request):
            calls.append(request)
            return ConsultantResponse(answer=f"Ответ {len(calls)}")

        count = run_adapter(self.input_path, self.output_path, consult)
        self.assertEqual(count, 2)
        self.assertEqual([call.question for call in calls], ["Одиночный вопрос", "Последний, \"вопрос\"\nсо второй строкой"])
        self.assertEqual(calls[0].history, [])
        self.assertIsNot(calls[0].history, calls[1].history)
        self.assertEqual(calls[1].history, [
            {"role": "user", "content": "Первый вопрос"},
            {"role": "assistant", "content": "Ответ"},
        ])
        with self.output_path.open(encoding="utf-8", newline="") as stream:
            output = list(csv.DictReader(stream))
        self.assertEqual(list(output[0]), ["id", "answer"])
        self.assertEqual([row["id"] for row in output], ["alpha", "beta"])
        self.assertEqual(output[1]["answer"], "Ответ 2")

    def test_missing_columns_empty_question_and_duplicate_id_fail(self):
        self.write_input([("1", "q")], header=("id", "text"))
        with self.assertRaises(InputFormatError):
            read_questions(self.input_path)
        self.write_input([("1", " ")])
        with self.assertRaises(InputFormatError):
            read_questions(self.input_path)
        self.write_input([("1", "q1"), ("1", "q2")])
        with self.assertRaises(InputFormatError):
            read_questions(self.input_path)

    def test_malformed_csv_row_fails(self):
        self.input_path.write_text('id,question\n1,"unterminated\n', encoding="utf-8")
        with self.assertRaises(csv.Error):
            read_questions(self.input_path)

    def test_empty_and_overlong_engine_responses_do_not_create_submission(self):
        self.write_input([("1", "q")])
        for answer in ("  ", "x" * 10_001):
            with self.subTest(length=len(answer)):
                if self.output_path.exists():
                    self.output_path.unlink()
                with self.assertRaises(ConsultantIntegrationError):
                    run_adapter(self.input_path, self.output_path, lambda _: ConsultantResponse(answer))
                self.assertFalse(self.output_path.exists())

    def test_output_csv_escapes_commas_quotes_and_newlines(self):
        self.write_input([("id,\"x\"", "вопрос")])
        run_adapter(
            self.input_path,
            self.output_path,
            lambda _: ConsultantResponse('Ответ, "точный"\nв две строки'),
        )
        with self.output_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows, [{"id": 'id,"x"', "answer": 'Ответ, "точный"\nв две строки'}])

    def test_engine_exception_is_sanitized(self):
        self.write_input([("1", "q")])
        with self.assertRaisesRegex(ConsultantIntegrationError, "RuntimeError") as error:
            run_adapter(self.input_path, self.output_path, lambda _: (_ for _ in ()).throw(RuntimeError("secret=do-not-log")))
        self.assertNotIn("do-not-log", str(error.exception))
        self.assertFalse(self.output_path.exists())

    def test_parallel_run_keeps_input_order(self):
        self.write_input([(str(i), f"вопрос {i}") for i in range(12)])
        active = []
        peak = [0]
        lock = threading.Lock()

        def consult(request):
            number = int(request.question.split()[-1])
            with lock:
                active.append(number)
                peak[0] = max(peak[0], len(active))
            # Ранние вопросы отвечают дольше, чтобы ответы пришли не по порядку.
            time.sleep(0.02 * (12 - number) / 12)
            with lock:
                active.remove(number)
            return ConsultantResponse(answer=f"ответ {number}")

        count = run_adapter(self.input_path, self.output_path, consult, max_workers=4)
        self.assertEqual(count, 12)
        with self.output_path.open(encoding="utf-8", newline="") as stream:
            output = list(csv.DictReader(stream))
        self.assertEqual([row["id"] for row in output], [str(i) for i in range(12)])
        self.assertEqual([row["answer"] for row in output], [f"ответ {i}" for i in range(12)])
        self.assertGreater(peak[0], 1)

    def test_deadline_fills_unfinished_answers_and_does_not_hang(self):
        self.write_input([("fast", "быстрый"), ("slow", "медленный")])
        release = threading.Event()

        def consult(request):
            if request.question == "медленный":
                release.wait(5)
            return ConsultantResponse(answer="готово")

        started = time.monotonic()
        try:
            run_adapter(
                self.input_path, self.output_path, consult, max_workers=2, deadline=time.monotonic() + 0.3
            )
        finally:
            release.set()
        self.assertLess(time.monotonic() - started, 2)
        with self.output_path.open(encoding="utf-8", newline="") as stream:
            output = {row["id"]: row["answer"] for row in csv.DictReader(stream)}
        self.assertEqual(output["fast"], "готово")
        self.assertEqual(output["slow"], TIMEOUT_ANSWER)

    def test_parallel_engine_exception_is_sanitized(self):
        self.write_input([("1", "ok"), ("2", "bad")])

        def consult(request):
            if request.question == "bad":
                raise RuntimeError("secret=do-not-log")
            return ConsultantResponse(answer="ответ")

        with self.assertRaisesRegex(ConsultantIntegrationError, "строки 3") as error:
            run_adapter(self.input_path, self.output_path, consult, max_workers=2)
        self.assertNotIn("do-not-log", str(error.exception))
        self.assertFalse(self.output_path.exists())

    def test_missing_engine_is_explicit(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        code = (
            "import importlib, solution\n"
            "def fail(name):\n"
            "    raise ImportError('secret')\n"
            "importlib.import_module = fail\n"
            "solution._load_engine()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Consultant Engine не подключён", result.stderr)
        self.assertNotIn("secret", result.stderr.lower())

    def test_engine_module_is_connected(self):
        import consultant_engine
        from solution import _load_engine

        self.assertIs(_load_engine(), consultant_engine.consult)


class LLMClientTests(unittest.TestCase):
    def test_environment_aliases_and_request_shape(self):
        requests = []

        def transport(url, headers, body, timeout):
            requests.append((url, headers, body, timeout))
            return 200, b'{"choices":[{"message":{"content":"  \\u0413\\u043e\\u0442\\u043e\\u0432\\u043e  "}}]}'

        client = LLMClient.from_env(
            {
                "OPENAI_BASE_URL": "https://proxy.invalid/v1/",
                "OPENAI_API_KEY": "test-key-never-real",
                "GEN_MODEL": "model-from-platform",
            },
            transport=transport,
            sleeper=lambda _: None,
        )
        self.assertEqual(client.complete([{"role": "user", "content": "test"}]), "Готово")
        url, headers, body, timeout = requests[0]
        self.assertEqual(url, "https://proxy.invalid/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-key-never-real")
        self.assertEqual(__import__("json").loads(body)["model"], "model-from-platform")
        self.assertEqual(timeout, 90.0)

    def test_missing_configuration_names_missing_variables_without_values(self):
        with self.assertRaises(LLMConfigurationError) as error:
            LLMClient.from_env({"LLM_API_KEY": "secret-value"})
        self.assertNotIn("secret-value", str(error.exception))
        self.assertIn("адрес API", str(error.exception))

    def test_retries_temporary_failure_then_succeeds(self):
        attempts = []
        delays = []

        def handler(_url, _headers, _body, _timeout):
            attempts.append(1)
            if len(attempts) == 1:
                return 503, b'{"error":"do-not-log"}'
            return 200, b'{"choices":[{"message":{"content":"ok"}}]}'

        client = LLMClient(
            base_url="https://proxy.invalid/v1",
            api_key="test-key",
            model="test-model",
            transport=handler,
            sleeper=delays.append,
            backoff_seconds=0.1,
        )
        self.assertEqual(client.complete([{"role": "user", "content": "test"}]), "ok")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(delays, [0.1])

    def test_timeout_is_retried_a_bounded_number_of_times(self):
        attempts = []
        delays = []

        def timeout_transport(*_args):
            attempts.append(1)
            raise TimeoutError("secret-bearing transport details")

        client = LLMClient(
            base_url="https://proxy.invalid",
            api_key="test-key",
            model="test-model",
            max_attempts=2,
            backoff_seconds=0.2,
            transport=timeout_transport,
            sleeper=delays.append,
        )
        with self.assertRaisesRegex(LLMAPIError, "таймаут") as error:
            client.complete([{"role": "user", "content": "test"}])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(delays, [0.2])
        self.assertNotIn("secret-bearing", str(error.exception))

    def test_api_errors_are_safe_and_invalid_response_rejected(self):
        client = LLMClient(
            base_url="https://proxy.invalid",
            api_key="unit-test-placeholder",
            model="model",
            max_attempts=1,
            sleeper=lambda _: None,
            transport=lambda *_: (401, b'{"error":"secret do-not-log"}'),
        )
        with self.assertRaises(LLMAPIError) as error:
            client.complete([{"role": "user", "content": "test"}])
        self.assertIn("401", str(error.exception))
        self.assertNotIn("do-not-log", str(error.exception))
        malformed = LLMClient(
            base_url="https://proxy.invalid",
            api_key="test-key",
            model="model",
            transport=lambda *_: (200, b'{"choices":[]}'),
        )
        with self.assertRaisesRegex(LLMAPIError, "неожиданной структуры"):
            malformed.complete([{"role": "user", "content": "test"}])


if __name__ == "__main__":
    unittest.main()
