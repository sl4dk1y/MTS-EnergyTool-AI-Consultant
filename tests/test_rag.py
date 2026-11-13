"""Тесты поиска по базе знаний.

CI проекта запускает ``python -m unittest discover -s tests``, поэтому тесты
написаны на ``unittest``, а не на pytest-функциях. Реальных запросов к прокси
нет: эмбеддинги и реранкер подменяются через ``unittest.mock``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rag_engine  # noqa: E402
from rag_engine import (  # noqa: E402
    MAX_CHUNK_CHARS,
    OVERLAP_CHARS,
    RagRetriever,
    chunk_knowledge_base,
    find_knowledge_base,
    split_markdown,
)

SOLAR_DOC = """# Солнечные станции

## Ориентация скатов

Крыша на два ската, восток и запад: панели можно ставить на оба ската.
Потери на восточном скате утром компенсируются выработкой западного вечером.
"""

ASKUE_DOC = """# Учёт энергоресурсов

## Совместимость модемов

Сим-карта стороннего оператора подойдёт: менять её при подключении не нужно,
связь с платформой идёт через любого мобильного оператора.
"""

TARIFF_DOC = """# Тарифы

| Категория | Описание |
| --- | --- |
| Первая | Одноставочный тариф |
| Вторая | День и ночь |
| Третья | По часам суток |
"""


def _write_kb(root: Path) -> None:
    (root / "20-products").mkdir(parents=True, exist_ok=True)
    (root / "20-products" / "23-solar.md").write_text(SOLAR_DOC, encoding="utf-8")
    (root / "20-products" / "22-askue.md").write_text(ASKUE_DOC, encoding="utf-8")
    (root / "10-energy").mkdir(parents=True, exist_ok=True)
    (root / "10-energy" / "tariffs.md").write_text(TARIFF_DOC, encoding="utf-8")


class ChunkingTests(unittest.TestCase):
    def test_chunks_keep_header_hierarchy_in_source(self) -> None:
        chunks = split_markdown(SOLAR_DOC, "20-products/23-solar.md")
        self.assertTrue(chunks)
        sources = [source for source, _ in chunks]
        self.assertTrue(any(source.startswith("20-products/23-solar.md > Солнечные станции") for source in sources))
        self.assertTrue(all(text.strip() for _, text in chunks))

    def test_table_stays_in_one_chunk(self) -> None:
        chunks = split_markdown(TARIFF_DOC, "10-energy/tariffs.md")
        tables = [text for _, text in chunks if "| Первая |" in text]
        self.assertEqual(len(tables), 1, "таблица разорвана между чанками")
        self.assertIn("| Третья |", tables[0])

    def test_chunk_length_is_limited(self) -> None:
        long_doc = "# Раздел\n\n" + "\n\n".join(f"Абзац номер {index} с текстом про тарифы." for index in range(400))
        chunks = split_markdown(long_doc, "long.md")
        self.assertTrue(chunks)
        for _, text in chunks:
            self.assertLessEqual(len(text), MAX_CHUNK_CHARS + 200)

    def test_merge_does_not_mix_sources(self) -> None:
        """Короткие разделы с разными заголовками не склеиваются."""
        doc = "# Раздел А\n\nКороткий текст про тарифы.\n\n# Раздел Б\n\nКороткий текст про счётчики.\n"
        chunks = split_markdown(doc, "short.md")
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0][0].endswith("Раздел А"))
        self.assertTrue(chunks[1][0].endswith("Раздел Б"))
        self.assertNotIn("счётчик", chunks[0][1])
        self.assertNotIn("тариф", chunks[1][1])

    def test_oversized_single_line_is_split(self) -> None:
        """Строка без переносов длиннее лимита должна резаться, а не оставаться чанком."""
        line = "тариф" * 1000  # 5000 символов без переносов и пробелов
        pieces = rag_engine._split_oversized(line, MAX_CHUNK_CHARS, OVERLAP_CHARS)
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), MAX_CHUNK_CHARS)
        self.assertEqual("".join(pieces), line, "текст разбит с потерями или дублями")

        chunks = split_markdown("# Раздел\n\n" + line, "long-line.md")
        self.assertTrue(chunks)
        for _, text in chunks:
            self.assertLessEqual(len(text), MAX_CHUNK_CHARS)

    def test_empty_document_gives_no_chunks(self) -> None:
        self.assertEqual(split_markdown("   \n\n", "empty.md"), [])


class RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.kb_root = Path(self._tmp.name)
        _write_kb(self.kb_root)
        self.addCleanup(self._tmp.cleanup)

    def _retriever(self, **kwargs) -> RagRetriever:
        return RagRetriever(self.kb_root, use_dense=False, **kwargs)

    def test_retrieve_returns_matching_file(self) -> None:
        retriever = self._retriever()
        chunks = retriever.retrieve("Крыша на два ската, панели на восток и запад", top_k=5)
        self.assertTrue(chunks)
        self.assertIn("23-solar.md", chunks[0].source)
        self.assertIsNotNone(chunks[0].score)

    def test_retrieve_second_question(self) -> None:
        retriever = self._retriever()
        chunks = retriever.retrieve("Симки другого оператора в счётчиках менять?", top_k=5)
        self.assertTrue(chunks)
        self.assertIn("22-askue.md", chunks[0].source)

    def test_colloquial_sim_query_matches_sim_cards(self) -> None:
        """«симки» в вопросе и «сим-карты» в базе должны сойтись после усечения."""
        retriever = self._retriever()
        chunks = retriever.retrieve(
            "В счётчиках стоят симки другого оператора. Для вашей платформы их надо менять?", top_k=5
        )
        self.assertTrue(chunks)
        self.assertIn("сим-карт", chunks[0].text.lower())

    def test_scores_are_positive_floats(self) -> None:
        """Движок режет по 0.5 * max(score): ноль и отрицательные значения ломают отсев."""
        chunks = self._retriever().retrieve("крыша скаты панели", top_k=20)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertIsInstance(chunk.score, float)
            self.assertGreater(chunk.score, 0.0)

    def test_empty_query_returns_empty(self) -> None:
        self.assertEqual(self._retriever().retrieve("   ", top_k=5), [])

    def test_empty_knowledge_base_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(RagRetriever(Path(empty), use_dense=False).retrieve("тариф", top_k=5), [])

    def test_bm25_works_when_embeddings_unavailable(self) -> None:
        with mock.patch.object(rag_engine, "_embed_texts", side_effect=RuntimeError("прокси недоступен")):
            retriever = RagRetriever(self.kb_root, use_dense=True)
            chunks = retriever.retrieve("крыша скаты панели", top_k=5)
        self.assertTrue(chunks, "без эмбеддингов BM25 должен продолжать работать")

    def test_bm25_fallback_when_dense_search_fails(self) -> None:
        """Ошибка векторного поиска не обнуляет выдачу: остаются результаты BM25."""
        with mock.patch.object(
            rag_engine, "_embed_texts", side_effect=lambda texts: [[0.5, 0.5] for _ in texts]
        ):
            retriever = RagRetriever(self.kb_root, use_dense=True)
            retriever.retrieve("прогрев", top_k=1)
            with mock.patch.object(RagRetriever, "_query_vector", side_effect=RuntimeError("таймаут")):
                chunks = retriever.retrieve("Крыша на два ската, панели на восток", top_k=5)
        self.assertTrue(chunks, "при падении dense должны вернуться результаты BM25")
        self.assertIn("23-solar.md", chunks[0].source)

    def test_dense_low_similarity_is_filtered(self) -> None:
        """Фрагменты с косинусом ниже порога не добавляются к выдаче."""

        def fake_embed(texts):
            # Документы получают [1, 0], запрос — [0, 1]: косинус 0 ниже порога.
            if len(texts) == 1:
                return [[0.0, 1.0]]
            return [[1.0, 0.0] for _ in texts]

        with mock.patch.object(rag_engine, "_embed_texts", side_effect=fake_embed):
            dense = RagRetriever(self.kb_root, use_dense=True).retrieve("Крыша на два ската панели", top_k=5)
        lexical = self._retriever().retrieve("Крыша на два ската панели", top_k=5)
        self.assertTrue(dense)
        self.assertEqual([chunk.source for chunk in dense], [chunk.source for chunk in lexical])
        self.assertLess(rag_engine.DENSE_MIN_SIMILARITY, 1.0)

    def test_reranker_scores_are_positive(self) -> None:
        def fake_embed(texts):
            # Разные векторы для запроса и документов: длина вектора 4.
            return [[1.0, 0.0, 0.0, 0.0] if index % 2 == 0 else [0.0, 1.0, 0.0, 0.0] for index in range(len(texts))]

        def fake_rerank(query, documents, timeout=20.0):
            return [-8.0, -12.0, -3.5][: len(documents)]

        with mock.patch.dict(os.environ, {"RERANKER_MODEL": "test-reranker"}, clear=False):
            with mock.patch.object(rag_engine, "_embed_texts", side_effect=fake_embed):
                with mock.patch.object(rag_engine, "_rerank", side_effect=fake_rerank):
                    retriever = RagRetriever(self.kb_root, use_dense=True)
                    chunks = retriever.retrieve("крыша скаты", top_k=5)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertIsNotNone(chunk.score)
            self.assertGreater(chunk.score, 0.0, "отрицательные логиты ломают относительный отсев движка")

    # -- второй аудит: реранкер, эмбеддинги, косинус ----------------------

    def _baseline_order(self, query: str = "Крыша на два ската панели"):
        return [chunk.source for chunk in self._retriever().retrieve(query, top_k=5)]

    def _order_with_rerank(self, response):
        with mock.patch.dict(os.environ, {"RERANKER_MODEL": "test-reranker"}, clear=False):
            with mock.patch.object(rag_engine, "_post_json", side_effect=response):
                chunks = self._retriever().retrieve("Крыша на два ската панели", top_k=5)
        return [chunk.source for chunk in chunks]

    def test_rerank_partial_response_keeps_original_order(self) -> None:
        baseline = self._baseline_order()

        def response(path, body, timeout=0, attempts=0):
            return {"results": [{"index": 0, "relevance_score": 5.0}]}

        self.assertEqual(self._order_with_rerank(response), baseline)

    def test_rerank_none_score_keeps_original_order(self) -> None:
        baseline = self._baseline_order()

        def response(path, body, timeout=0, attempts=0):
            documents = body["documents"]
            return {
                "results": [
                    {"index": index, "relevance_score": None if index == 0 else 1.0}
                    for index in range(len(documents))
                ]
            }

        self.assertEqual(self._order_with_rerank(response), baseline)

    def test_rerank_malformed_response_keeps_original_order(self) -> None:
        baseline = self._baseline_order()

        def bad_index(path, body, timeout=0, attempts=0):
            documents = body["documents"]
            return {"results": [{"index": len(documents) + 5, "relevance_score": 1.0} for _ in documents]}

        def duplicate_index(path, body, timeout=0, attempts=0):
            documents = body["documents"]
            return {"results": [{"index": 0, "relevance_score": 1.0} for _ in documents]}

        def non_numeric(path, body, timeout=0, attempts=0):
            documents = body["documents"]
            return {"results": [{"index": index, "relevance_score": "высоко"} for index in range(len(documents))]}

        for response in (bad_index, duplicate_index, non_numeric):
            self.assertEqual(self._order_with_rerank(response), baseline)

    def test_rerank_null_response_keeps_bm25_results(self) -> None:
        """JSON null от /rerank не должен обнулять выдачу BM25."""
        baseline = self._baseline_order()

        def response(path, body, timeout=0, attempts=0):
            return None

        order = self._order_with_rerank(response)
        self.assertTrue(order, "BM25-результаты не должны теряться")
        self.assertEqual(order, baseline)

    def test_rerank_non_object_response_keeps_bm25_results(self) -> None:
        """Не-объект верхнего уровня (список, строка, число) не ломает поиск."""
        baseline = self._baseline_order()
        for payload in (["лишний элемент"], "ошибка прокси", 42):
            def response(path, body, timeout=0, attempts=0, payload=payload):
                return payload

            order = self._order_with_rerank(response)
            self.assertTrue(order, "BM25-результаты не должны теряться")
            self.assertEqual(order, baseline)

    def _order_with_embeddings(self, response, query: str = "Крыша на два ската"):
        environment = {"EMB_MODEL": "test-embed", "LLM_BASE_URL": "http://proxy.local"}
        with mock.patch.dict(os.environ, environment, clear=False):
            with mock.patch.object(rag_engine, "_post_json", side_effect=response):
                retriever = RagRetriever(self.kb_root, use_dense=True)
                chunks = retriever.retrieve(query, top_k=5)
        return retriever, [chunk.source for chunk in chunks]

    def test_embeddings_dimension_mismatch_disables_dense(self) -> None:
        def response(path, body, timeout=0, attempts=0):
            inputs = body.get("input", [])
            return {
                "data": [
                    {"index": index, "embedding": [0.1, 0.2] if index == 0 else [0.1, 0.2, 0.3]}
                    for index in range(len(inputs))
                ]
            }

        retriever, order = self._order_with_embeddings(response)
        self.assertIsNone(retriever._dense, "плотный канал должен отключиться")
        self.assertEqual(order, self._baseline_order("Крыша на два ската"))

    def test_embeddings_non_finite_values_disables_dense(self) -> None:
        def response(path, body, timeout=0, attempts=0):
            inputs = body.get("input", [])
            return {
                "data": [
                    {"index": index, "embedding": [0.1, float("nan")] if index == 0 else [0.1, 0.2]}
                    for index in range(len(inputs))
                ]
            }

        retriever, order = self._order_with_embeddings(response)
        self.assertIsNone(retriever._dense, "плотный канал должен отключиться")
        self.assertEqual(order, self._baseline_order("Крыша на два ската"))

    def test_cosine_similarity_rejects_different_lengths(self) -> None:
        self.assertIsNone(rag_engine._cosine_similarity([1.0, 2.0], [1.0]))
        self.assertIsNone(rag_engine._cosine_similarity([], [1.0]))
        self.assertIsNotNone(rag_engine._cosine_similarity([1.0, 0.0], [1.0, 0.0]))

    def test_thread_safety_builds_index_once(self) -> None:
        calls = []

        original = rag_engine.chunk_knowledge_base

        def counting(root):
            calls.append(root)
            return original(root)

        with mock.patch.object(rag_engine, "chunk_knowledge_base", side_effect=counting):
            retriever = RagRetriever(self.kb_root, use_dense=False)
            results = []
            errors = []

            def worker() -> None:
                try:
                    results.append(retriever.retrieve("тариф", top_k=3))
                except Exception as exc:  # pragma: no cover - диагностика
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1, "индекс должен строиться один раз")
        self.assertTrue(all(results))


class KnowledgeBaseDiscoveryTests(unittest.TestCase):
    def test_zip_is_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "doc.md").write_text("# Раздел\n\nТекст про счётчики.", encoding="utf-8")
            archive = root / "knowledge-base.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.write(source / "doc.md", "doc.md")

            extracted = find_knowledge_base(root)
            self.assertIsNotNone(extracted)
            self.assertTrue((extracted / "doc.md").is_file())
            chunks = chunk_knowledge_base(extracted)
            self.assertTrue(chunks)

    def test_missing_archive_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_knowledge_base(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
