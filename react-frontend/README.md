# React-интерфейс

Клиент собран на Vite и React. Он обращается к FastAPI из корня репозитория; отдельный Node/Express-сервер не используется.

## Локальный запуск

1. Установите зависимости backend из корня проекта:

   ```bash
   python -m pip install -r backend/requirements.txt
   ```

2. Запустите FastAPI из корня:

   ```bash
   uvicorn backend.app:app --port 8000
   ```

3. В другом терминале установите зависимости и запустите Vite:

   ```bash
   cd react-frontend/client
   npm ci
   npm run dev
   ```

Vite проксирует `/api` на `http://localhost:8000`. Интерфейс открывается по адресу `http://localhost:5173`.

Для ответов консультанта настройте разрешённые переменные LLM API локально в окружении backend. Не добавляйте ключи в файлы проекта.
