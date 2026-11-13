import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, EXAMPLE_QUESTIONS } from './api.js';
import { PRODUCTS } from './products.js';
import Sidebar from './components/Sidebar.jsx';
import LeadModal from './components/LeadModal.jsx';

const GREETING = 'Здравствуйте! Я консультант МТС EnergyTool. С чего начнём — снизить платёж, наладить учёт, найти потери или поставить солнечную станцию?';

export default function App() {
  const [sid, setSid] = useState('');
  const [msgs, setMsgs] = useState([{ role: 'assistant', content: GREETING }]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [products] = useState(PRODUCTS.filter((p) => p.id !== 'general'));
  const [sessions, setSessions] = useState([]);
  const [showDebug, setShowDebug] = useState(false);
  const [debugText, setDebugText] = useState('Отладка: пока нет ответов. Здесь появятся время ответа и источники из базы знаний.');
  const [leadOpen, setLeadOpen] = useState(false);
  const [leadDraft, setLeadDraft] = useState(null);
  const [toast, setToast] = useState('');
  const [sideOpen, setSideOpen] = useState(false);
  const [theme, setTheme] = useState(() => {
    try {
      const saved = localStorage.getItem('mts-theme');
      if (saved === 'dark' || saved === 'light') return saved;
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    } catch { return 'light'; }
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem('mts-theme', theme); } catch {}
  }, [theme]);
  const chatRef = useRef(null);
  const inputRef = useRef(null);
  const toastTimer = useRef(null);
  const startedRef = useRef(false);

  const showToast = useCallback((text) => {
    setToast(text);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 3200);
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const list = await api('/api/sessions');
      setSessions(Array.isArray(list) ? list : []);
    } catch {}
  }, []);

  const startFresh = useCallback(async () => {
    try {
      const d = await api('/api/sessions', 'POST', {});
      setSid(d.id || '');
    } catch { setSid(''); }
    setMsgs([{ role: 'assistant', content: GREETING }]);
    setDebugText('Отладка: создан новый диалог.');
    loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    startFresh();
  }, [startFresh]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [msgs, busy]);

  const send = useCallback(async (text) => {
    const t = (text ?? input).trim();
    if (!t || busy || !sid) return;
    setBusy(true);
    setInput('');
    if (inputRef.current) inputRef.current.style.height = 'auto';
    setMsgs((prev) => [...prev, { role: 'user', content: t }]);
    try {
      const d = await api(`/api/sessions/${sid}/messages`, 'POST', { text: t });
      const ans = d.assistant_message || {};
      const sources = Array.isArray(ans.sources) ? ans.sources : [];
      const meta = ans.elapsed_ms != null ? `Ответ за ${ans.elapsed_ms} мс` : '';
      setMsgs((prev) => [...prev, { role: 'assistant', content: ans.content || 'Нет ответа', meta }]);
      setDebugText(`Время: ${ans.elapsed_ms ?? '—'} мс\nИсточники:\n${sources.length ? sources.map((s) => `- ${s}`).join('\n') : 'не найдены'}`);
      if (d.lead_suggested) showToast('Похоже, вам стоит оставить заявку менеджеру');
    } catch (e) {
      const msg = e && e.status === 503 ? 'Модель пока не настроена на сервере. Попробуйте позже или передайте вопрос специалисту.' : `Не удалось получить ответ: ${(e && e.message) || 'ошибка сервера'}`;
      setMsgs((prev) => [...prev, { role: 'assistant', content: msg }]);
    }
    setBusy(false);
    loadHistory();
    inputRef.current?.focus();
  }, [input, busy, sid, loadHistory, showToast]);

  async function openLead() {
    try {
      const draft = await api(`/api/sessions/${sid}/lead/draft`, 'POST', {});
      setLeadDraft(draft);
    } catch { setLeadDraft(null); }
    setLeadOpen(true);
  }

  async function submitLead({ full_name, phone, company, consent }) {
    try {
      await api(`/api/sessions/${sid}/lead`, 'POST', { full_name, phone, company, consent });
      setLeadOpen(false);
      showToast('Заявка отправлена, резюме диалога передано менеджеру');
      loadHistory();
      try {
        const s = await api(`/api/sessions/${sid}`);
        if (Array.isArray(s.messages)) setMsgs(s.messages.map((m) => ({ role: m.role, content: m.content })));
      } catch {}
      return true;
    } catch (e) {
      return (e && e.message) || 'Не удалось отправить заявку';
    }
  }

  async function doHandoff() {
    if (!sid || busy) return;
    try {
      const d = await api(`/api/sessions/${sid}/handoff`, 'POST', {});
      if (d.assistant_message && d.assistant_message.content) setMsgs((prev) => [...prev, { role: 'assistant', content: d.assistant_message.content }]);
      showToast('Вопрос передан специалисту');
    } catch (e) {
      showToast((e && e.message) || 'Не удалось передать вопрос');
    }
  }

  const hasUserMsg = msgs.some((m) => m.role === 'user');

  return (
    <>
      <header className="top"><div className="top-in">
        <div className="brand"><div className="mark">МТС</div><div>EnergyTool<small>ИИ-консультант по энергетике для бизнеса</small></div></div>
        <div className="actions">
          <button className="btn menu" type="button" onClick={() => setSideOpen((v) => !v)}>Меню</button>
          <button className="btn" type="button" aria-label="Переключить тему" onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}>{theme === 'dark' ? '☀️ Светлая' : '🌙 Тёмная'}</button>
          <button className="btn" type="button" onClick={() => setShowDebug((v) => !v)}>Отладка</button>
          <button className="btn primary" type="button" onClick={startFresh}>Новый диалог</button>
        </div>
      </div></header>
      <div className="wrap">
        <Sidebar products={products} sessions={sessions} onAsk={send} open={sideOpen} />
        <main className="main">
          <div className={hasUserMsg ? 'hero hide' : 'hero'}>
            <h1>Разберёмся в вашей задаче <span>и подберём решение</span></h1>
            <p>Опишите ситуацию своими словами: выросли счета, нужен учёт по цехам, хотите солнечную станцию или проверить потери. Консультант задаст уточняющий вопрос и предложит заявку менеджеру с резюме диалога.</p>
          </div>
          <div id="chat" aria-live="polite" ref={chatRef}>
            {msgs.map((m, i) => (
              <div className={`row ${m.role === 'user' ? 'user' : 'bot'}`} key={i}>
                <div className="avatar">{m.role === 'user' ? 'Вы' : 'ET'}</div>
                <div><div className="msg">{m.content}</div>{m.meta ? <div className="meta">{m.meta}</div> : null}</div>
              </div>
            ))}
            {busy && <div className="typing">Консультант печатает…</div>}
          </div>
          <div className="chips">
            {EXAMPLE_QUESTIONS.map((q, i) => <button className="chip" type="button" key={i} onClick={() => send(q)}>{q}</button>)}
          </div>
          {showDebug && <div id="debug" className="on">{debugText}</div>}
          <div className="composer">
            <div className="composer-in">
              <textarea ref={inputRef} rows={1} placeholder="Например: у нас склад, счёт вырос за зиму…" aria-label="Ваш вопрос" value={input}
                onChange={(e) => { setInput(e.target.value); e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'; }}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} />
              <button className="btn primary" type="button" onClick={() => send()}>Отправить</button>
            </div>
            <div className="lead-row"><button className="linkbtn" type="button" onClick={openLead}>Оставить заявку менеджеру →</button><button className="linkbtn" type="button" onClick={doHandoff}>Передать специалисту →</button></div>
          </div>
        </main>
      </div>
      <LeadModal open={leadOpen} draft={leadDraft} onClose={() => setLeadOpen(false)} onSubmit={submitLead} />
      <div className={toast ? 'toast on' : 'toast'}>{toast || 'Заявка отправлена'}</div>
    </>
  );
}
