import json
import unittest

from leads import (
    LeadDraft,
    extract_contacts,
    find_phone,
    missing_fields_message,
    normalize_company,
    normalize_full_name,
    normalize_phone,
    prepare_lead,
    summarize_dialog,
    validate_lead,
    wants_to_leave_request,
)

# Только выдуманные данные.
HISTORY = [
    {"role": "user", "content": "Хотим поставить солнечную станцию на складе в Казани."},
    {"role": "assistant", "content": "Какая площадь кровли? Мой номер для справок +7 999 111-11-11."},
    {"role": "user", "content": "Около 800 м². Оставлю заявку: Тестов Тест Тестович, ООО Ромашка, 8 (900) 123-45-67."},
]


class ScriptedLLM:
    def __init__(self, *replies, error=None):
        self.replies = list(replies)
        self.error = error
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return self.replies.pop(0)


class NormalizeTests(unittest.TestCase):
    def test_phone_formats(self):
        for raw in ("+7 900 123-45-67", "8 (900) 123-45-67", "89001234567", "9001234567", "7-900-123-45-67"):
            self.assertEqual(normalize_phone(raw), "+79001234567", raw)
        self.assertEqual(normalize_phone("+44 20 7946 0958"), "+442079460958")
        for raw in ("", "123", "8900123456", "+7 900 123", "тел 89001234567", "1234567890"):
            self.assertIsNone(normalize_phone(raw), raw)

    def test_full_name(self):
        self.assertEqual(normalize_full_name("  тестов   тест "), "Тестов Тест")
        self.assertEqual(normalize_full_name("Тестова-Пробная Анна Сергеевна"), "Тестова-Пробная Анна Сергеевна")
        for raw in ("Тест", "Тест 123", "Тестов Тест; drop", "a b c d e"):
            self.assertIsNone(normalize_full_name(raw), raw)

    def test_company(self):
        self.assertEqual(normalize_company(' ООО  "Ромашка" '), 'ООО "Ромашка"')
        self.assertIsNone(normalize_company("-"))
        self.assertIsNone(normalize_company("x" * 201))


class ValidateTests(unittest.TestCase):
    def test_complete_lead(self):
        draft = validate_lead(
            {"full_name": "тестов тест", "phone": "89001234567", "company": "ООО Ромашка"}, "Нужна станция."
        )
        self.assertEqual(draft.phone, "+79001234567")
        self.assertEqual(draft.full_name, "Тестов Тест")
        self.assertTrue(draft.is_complete)
        self.assertEqual(draft.to_dict()["missing"], [])

    def test_missing_and_invalid_fields(self):
        draft = validate_lead({"full_name": "Тест", "phone": "", "company": "ООО Ромашка"}, "Резюме")
        self.assertEqual(draft.missing, ["full_name", "phone"])
        self.assertIn("full_name", draft.errors)
        self.assertNotIn("phone", draft.errors)
        self.assertFalse(draft.is_complete)
        self.assertEqual(draft.full_name, "Тест")

    def test_lead_without_summary_is_not_complete(self):
        draft = validate_lead({"full_name": "Тестов Тест", "phone": "89001234567", "company": "Ромашка"})
        self.assertEqual(draft.missing, [])
        self.assertFalse(draft.is_complete)

    def test_missing_message(self):
        self.assertEqual(
            missing_fields_message(LeadDraft(company="Ромашка")),
            "Чтобы передать заявку менеджеру, напишите, пожалуйста, ФИО и телефон.",
        )
        self.assertEqual(
            missing_fields_message(LeadDraft()),
            "Чтобы передать заявку менеджеру, напишите, пожалуйста, ФИО, телефон и название компании.",
        )
        draft = validate_lead({"phone": "123"})
        self.assertIn("+7 900 000-00-00", missing_fields_message(draft))
        complete = validate_lead({"full_name": "Тестов Тест", "phone": "89001234567", "company": "Ромашка"}, "s")
        self.assertEqual(missing_fields_message(complete), "")


class IntentTests(unittest.TestCase):
    def test_request_intent(self):
        for text in ("Хочу оставить заявку", "Перезвоните мне завтра", "Можно связаться с менеджером?"):
            self.assertTrue(wants_to_leave_request(text), text)
        for text in ("Я пока не готов оставлять заявку", "Сколько стоит учёт?", "Не надо мне перезванивать"):
            self.assertFalse(wants_to_leave_request(text), text)


class ExtractionTests(unittest.TestCase):
    def test_extract_uses_only_client_turns(self):
        reply = json.dumps({"full_name": "Тестов Тест Тестович", "phone": "8 (900) 123-45-67", "company": "ООО Ромашка"})
        llm = ScriptedLLM("<think>ищу</think>" + reply)
        contacts = extract_contacts(HISTORY, llm)
        self.assertEqual(contacts["company"], "ООО Ромашка")
        sent = llm.calls[0][1]["content"]
        self.assertIn("Казани", sent)
        self.assertNotIn("+7 999 111-11-11", sent)

    def test_phone_found_without_model(self):
        contacts = extract_contacts(HISTORY, ScriptedLLM(error=RuntimeError("down")))
        self.assertEqual(contacts["phone"], "+79001234567")
        self.assertEqual(contacts["full_name"], "")

    def test_bad_json_falls_back_to_regex_phone(self):
        contacts = extract_contacts(HISTORY, ScriptedLLM("не JSON"))
        self.assertEqual(contacts["phone"], "+79001234567")

    def test_find_phone_skips_short_numbers(self):
        self.assertEqual(find_phone("Около 800 м², 40 счётчиков"), "")


class SummaryTests(unittest.TestCase):
    def test_summary_from_model(self):
        llm = ScriptedLLM("Клиент хочет солнечную станцию на складе в Казани, кровля около 800 м².")
        self.assertIn("Казани", summarize_dialog(HISTORY, llm))
        dialog = llm.calls[0][1]["content"]
        self.assertTrue(dialog.startswith("Клиент: "))
        self.assertIn("Консультант: ", dialog)

    def test_summary_fallback_without_model(self):
        summary = summarize_dialog(HISTORY, ScriptedLLM(error=RuntimeError("down")))
        self.assertTrue(summary.startswith("Запрос клиента: Хотим поставить"))

    def test_empty_history(self):
        self.assertEqual(summarize_dialog([], None), "")


class PrepareLeadTests(unittest.TestCase):
    def test_form_values_override_dialog(self):
        extracted = json.dumps({"full_name": "Тестов Тест", "phone": "", "company": "ООО Ромашка"})
        llm = ScriptedLLM(extracted, "Клиенту нужна солнечная станция.")
        draft = prepare_lead(HISTORY, llm, form={"company": "АО Тест", "phone": " "})
        self.assertEqual(draft.company, "АО Тест")
        self.assertEqual(draft.phone, "+79001234567")
        self.assertEqual(draft.summary, "Клиенту нужна солнечная станция.")
        self.assertTrue(draft.is_complete)

    def test_works_without_model(self):
        draft = prepare_lead(HISTORY, None, form={"full_name": "Тестов Тест", "company": "Ромашка"})
        self.assertTrue(draft.is_complete)
        self.assertTrue(draft.summary.startswith("Запрос клиента"))


if __name__ == "__main__":
    unittest.main()
