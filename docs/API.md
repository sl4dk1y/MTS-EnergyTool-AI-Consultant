# Backend API веб-прототипа

HTTP API для чата, истории, заявок и передачи вопроса специалисту. Использует тот же Consultant Engine, что и конкурсный `solution.py`. Интерактивная документация доступна на `/docs` после запуска.

## Запуск

```bash
pip install -r backend/requirements.txt
```

```bash
DATA_DIR=local-data LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=... uvicorn backend.app:app --port 8000
```

Команды выполняются из корня репозитория. В `DATA_DIR` должен лежать `knowledge-base.zip`. Без переменных LLM сервер стартует, но чат отвечает 503, а `/api/health` показывает `"engine": "not_configured"`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DATABASE_PATH` | `local-data/web.sqlite3` | Файл SQLite с сессиями и заявками |
| `CORS_ORIGINS` | `http://localhost:5173` | Адреса фронтенда через запятую |
| `ADMIN_TOKEN` | пусто | Если задан, списки сессий, заявок и передач требуют заголовок `X-Admin-Token` |
| `SESSION_RETENTION_DAYS` | 30 | Через сколько дней без активности сессия удаляется, 0 отключает |
| `LEAD_RETENTION_DAYS` | 90 | Срок хранения заявок и передач специалисту, 0 отключает |

Остальные переменные движка описаны в [ENGINE.md](ENGINE.md).

## Сценарий для фронтенда

1. При открытии чата или по кнопке «Новый диалог» вызвать `POST /api/sessions` и запомнить `id`. Новая сессия не видит историю других.
2. Каждое сообщение отправлять в `POST /api/sessions/{id}/messages`. В ответе реплика консультанта, источники и время ответа для режима отладки.
3. Если в ответе `lead_suggested: true` или клиент нажал «Оставить заявку», вызвать `POST /api/sessions/{id}/lead/draft`. Вернётся черновик с найденными в диалоге полями, резюме и списком недостающих полей.
4. Показать черновик клиенту, дать поправить поля и поставить галочку согласия на обработку данных.
5. Отправить `POST /api/sessions/{id}/lead`. При ошибках вернётся 422 с текстом и полями, которые нужно исправить.
6. Если консультант не может ответить, кнопка «Передать специалисту» вызывает `POST /api/sessions/{id}/handoff`.

Для восстановления чата после перезагрузки страницы `id` сессии можно хранить в `localStorage` и загружать историю через `GET /api/sessions/{id}`.

## Эндпоинты

### `GET /api/health`
```json
{"status": "ok", "engine": "ready"}
```

### `POST /api/sessions` → 201
```json
{"id": "6f1c...", "created_at": "2026-11-13T10:00:00+00:00", "updated_at": "2026-11-13T10:00:00+00:00"}
```

### `GET /api/sessions/{id}`
Сессия и все сообщения. 404, если сессии нет.
```json
{
  "id": "6f1c...",
  "created_at": "...",
  "updated_at": "...",
  "messages": [
    {"id": 1, "role": "user", "content": "Хотим солнечную станцию", "sources": [], "elapsed_ms": null, "created_at": "..."},
    {"id": 2, "role": "assistant", "content": "Да, ...", "sources": ["20-products/23-solar.md > Размещение"], "elapsed_ms": 2350, "created_at": "..."}
  ]
}
```

### `DELETE /api/sessions/{id}` → 204
Удаляет сессию и её сообщения. Заявки остаются, у них обнуляется `session_id`.

### `POST /api/sessions/{id}/messages`
Тело `{"text": "вопрос"}`, от 1 до 4000 символов.
```json
{
  "user_message": {"id": 3, "role": "user", "content": "...", "sources": [], "elapsed_ms": null, "created_at": "..."},
  "assistant_message": {"id": 4, "role": "assistant", "content": "...", "sources": ["..."], "elapsed_ms": 2100, "created_at": "..."},
  "lead_suggested": false
}
```
`sources` и `elapsed_ms` нужны для режима отладки. 503, если модель не настроена.

### `POST /api/sessions/{id}/lead/draft`
Тело может содержать уже введённые поля `{"full_name": "...", "phone": "...", "company": "..."}`, они важнее найденных в диалоге.
```json
{
  "full_name": "",
  "phone": "+79001234567",
  "company": "Ромашка",
  "summary": "Клиенту нужна солнечная станция на складе...",
  "errors": {},
  "missing": ["full_name"],
  "is_complete": false,
  "message": "Чтобы передать заявку менеджеру, напишите, пожалуйста, ФИО."
}
```

### `POST /api/sessions/{id}/lead` → 201
Тело `{"full_name": "...", "phone": "...", "company": "...", "summary": "...", "consent": true}`. Если `summary` пустое, резюме пишется по диалогу. Без `consent: true` или при ошибках в полях ответ 422.
```json
{"id": "a2b4...", "session_id": "6f1c...", "full_name": "Тестов Тест", "phone": "+79001234567", "company": "ООО Ромашка", "summary": "...", "created_at": "..."}
```
Ошибка валидации
```json
{"detail": {"message": "Укажите телефон в формате +7 900 000-00-00.", "draft": {"errors": {"phone": "..."}, "missing": ["phone"]}}}
```
После сохранения в историю сессии добавляется сообщение консультанта о том, что заявка передана.

### `POST /api/sessions/{id}/handoff` → 201
Тело `{"question": "..."}` или `{}`, тогда берётся последний вопрос клиента. В историю добавляется сообщение консультанта о передаче.
```json
{"id": "...", "session_id": "...", "question": "...", "created_at": "...", "assistant_message": {"id": 7, "role": "assistant", "content": "Передали ваш вопрос специалисту...", "sources": [], "elapsed_ms": null, "created_at": "..."}}
```

### Списки для менеджера
`GET /api/sessions`, `GET /api/leads`, `GET /api/handoffs`, параметр `limit` до 500. Если задан `ADMIN_TOKEN`, нужен заголовок `X-Admin-Token`. В списке сессий есть `title` (первый вопрос), `message_count` и `has_lead`, туда попадают и диалоги без заявки.

## Данные и безопасность

- Сессия адресуется случайным UUID, без него чужую историю не получить.
- Тексты сообщений и контакты не пишутся в логи.
- Заявка сохраняется только с согласием клиента.
- Устаревшие сессии и заявки удаляются при старте сервера по срокам из переменных.
- База лежит в `local-data/`, папка в `.gitignore`. В разработке используются только выдуманные данные.
