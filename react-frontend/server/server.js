const fs = require('fs');
const path = require('path');
const express = require('express');
const cors = require('cors');
const { randomUUID } = require('crypto');
const { PRODUCTS, search, detectIntent } = require('./knowledge');

const DATA = path.join(__dirname, 'data');
fs.mkdirSync(DATA, { recursive: true });
const app = express();
app.use(cors());
app.use(express.json());

function fmt(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function loadJson(name, def) {
  const p = path.join(DATA, name);
  if (fs.existsSync(p)) { try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return def; } }
  return def;
}
function saveJson(name, obj) { fs.writeFileSync(path.join(DATA, name), JSON.stringify(obj, null, 2), 'utf-8'); }

let sessions = loadJson('sessions.json', {});
let leads = loadJson('leads.json', []);

const CLARIFY = {
  solar: 'Уточните: регион, тип и площадь кровли, цель - снизить платеж или надежность?',
  orem: 'Какая присоединенная мощность и месячное потребление?',
  askue: 'Сколько приборов учета и как сейчас собираете показания?',
  losses: 'Какой архив данных есть и сколько приборов в парке?',
  meter: 'Какой ресурс - электроэнергия или вода - и сколько приборов?',
  general: 'Расскажите: тип компании, мощность и задачу - снизить платеж, наладить учет или найти потери?',
};

function getProduct(pid) { return PRODUCTS.find((p) => p.id === pid) || PRODUCTS[PRODUCTS.length - 1]; }

function buildAnswer(msg, hist) {
  const t0 = Date.now();
  const ctx = hist.slice(-4).map((h) => h.content).join(' ');
  const sources = search(msg + ' ' + ctx, 3);
  const intent = detectIntent(msg + ' ' + ctx);
  const prod = getProduct(intent);
  if (!sources.length) {
    return { ans: 'В базе знаний EnergyTool нет ответа на этот вопрос. Предлагаю связаться со специалистом - он разберет вашу ситуацию. Оставить заявку?', sources, intent, prod, ms: Date.now() - t0, offer: false };
  }
  let ans = prod.short + ' ' + prod.facts.slice(0, 2).join(' ');
  if (hist.length < 4) ans += ' ' + CLARIFY[intent];
  const offer = hist.length >= 2 || ['заявк', 'менеджер', 'расч', 'оставить'].some((k) => msg.toLowerCase().includes(k));
  return { ans, sources, intent, prod, ms: Date.now() - t0, offer };
}

app.get('/api/health', (req, res) => res.json({ status: 'ok', products: PRODUCTS.length }));
app.get('/api/products', (req, res) => res.json(PRODUCTS));
app.get('/api/sessions', (req, res) => {
  const out = Object.entries(sessions).map(([sid, s]) => ({ id: sid, created: s.created || '', messages: (s.messages || []).length, lead: Boolean(s.lead) }));
  res.json(out);
});
app.get('/api/sessions/:sid', (req, res) => res.json(sessions[req.params.sid] || { id: req.params.sid, messages: [] }));

app.post('/api/chat', (req, res) => {
  const { session_id = '', message } = req.body || {};
  const sid = session_id || randomUUID();
  let sess = sessions[sid];
  if (!sess) { sess = { id: sid, created: fmt(), messages: [] }; sessions[sid] = sess; }
  const hist = sess.messages;
  const { ans, sources, intent, prod, ms, offer } = buildAnswer(message, hist);
  sess.messages.push({ role: 'user', content: message });
  sess.messages.push({ role: 'assistant', content: ans });
  saveJson('sessions.json', sessions);
  res.json({ session_id: sid, answer: ans, intent, product: prod.name, sources, latency_ms: ms, lead_offer: offer, messages: sess.messages });
});

app.post('/api/lead', (req, res) => {
  const { session_id, name, phone, company = '', consent = false } = req.body || {};
  if (!consent) return res.json({ ok: false, error: 'Нужно согласие на обработку данных' });
  const sess = sessions[session_id] || { messages: [] };
  const summary = (sess.messages || []).map((m) => m.content).join(' ').slice(0, 800);
  const item = { name, phone, company, session_id, summary, created: fmt() };
  leads.push(item);
  saveJson('leads.json', leads);
  if (sessions[session_id]) { sessions[session_id].lead = item; saveJson('sessions.json', sessions); }
  res.json({ ok: true, lead: item });
});

app.post('/api/sessions/new', (req, res) => {
  const sid = randomUUID();
  sessions[sid] = { id: sid, created: fmt(), messages: [] };
  saveJson('sessions.json', sessions);
  res.json({ session_id: sid });
});

const dist = path.join(__dirname, '..', 'client', 'dist');
if (fs.existsSync(dist)) {
  app.use(express.static(dist));
  app.get('/', (req, res) => res.sendFile(path.join(dist, 'index.html')));
} else {
  app.get('/', (req, res) => res.json({ status: 'ok', hint: 'Соберите клиент: cd client && npm install && npm run build, или запустите dev: npm run dev' }));
}

const PORT = process.env.PORT || 8000;
app.listen(PORT, '0.0.0.0', () => console.log(`MTS EnergyTool API on http://localhost:${PORT}`));
