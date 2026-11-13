# МТС EnergyTool — ИИ-консультант (React + JS)

Переписанная на React и JavaScript версия веб-интерфейса ИИ-консультанта MTS EnergyTool. Лежит в папке react-frontend/ этого репозитория и не трогает Python-код в корне.

## Стек

- Клиент: Vite + React 18, чистый JS (JSX), стили перенесены из исходного static/index.html.
- Сервер: Node.js + Express, порт 8000. Логика 1-в-1 из app.py и knowledge.py: поиск по базе знаний, определение темы, сессии и заявки в JSON-файлах.
- База знаний: в репозиторий не включена (материалы кейса нельзя публиковать). Положите папку knowledge-base/ рядом с react-frontend/ или укажите KB_DIR — без неё используются встроенные карточки продуктов из knowledge.js.

## Структура

- client/ — React-приложение: src/App.jsx, src/components/Sidebar.jsx, src/components/LeadModal.jsx, src/api.js, src/styles.css.
- server/ — server.js (API: /api/health, /api/products, /api/sessions, /api/chat, /api/lead), knowledge.js, data/ (sessions.json, leads.json).

## Запуск для разработки

Терминал 1: cd server, npm install, npm start — API на http://localhost:8000

Терминал 2: cd client, npm install, npm run dev — UI на http://localhost:5173, /api проксируется на 8000.

## Продакшн одной командой

В client выполнить npm run build, затем в server выполнить npm start — сервер сам раздаёт собранный client/dist на http://localhost:8000.
