import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, EXAMPLE_QUESTIONS } from './api.js';
import Sidebar from './components/Sidebar.jsx';
import LeadModal from './components/LeadModal.jsx';

const GREETING = 'Здравствуйте! Я консультант МТС EnergyTool. С чего начнём — снизить платёж, наладить учёт, найти потери или поставить солнечную станцию?';

export default function App() {
  const [sid, setSid] = useState('');
  const [msgs, setMsgs] = useState([{ role: 'assistant', content: GREETING }]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [products, setProducts] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [showDebug, setShowDebug] = useState(false);
  const [debugText, setDebugText] = useState('Отладка: пока нет ответов. Здесь появятся время ответа, тема, продукт и источники из базы знаний.');
  const [leadOpen, setLeadOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [sideOpen, setSideOpen] = useState(false);
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
    try { setSessions(await api('/api/sessions')); } catch {}
  }, []);

  const loadProducts = useCallback(async () => {
    try {
      const ps = await api('/api/products');
      setProducts(ps.filter((p) => p.id !== 'general'));
    } catch { setProducts([]); }
  }, []);

  const startFresh = useCallback(async () => {
    try {
      const d = await api('/api/sessions/new', 'POST', {});
      setSid(d.session_id || '');
    } catch { setSid(''); }
    setMsgs([{ role: 'assistant', content: GREETING }]);
    setDebugText('Отладка: создан новый диалог. Старая сессия из localStorage больше не восстанавливается.');
    loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    loadProducts();
    startFresh();
  }, [loadProducts, startFresh]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [msgs, busy]);

  const send = useCallback(async (text) => {
    const t = (text ?? input).trim();
    if (!t || busy) return;
    setBusy(true);
    setInput('');
    if (inputRef.current) inputRef.current.style.height = 'auto';
    setMsgs((prev) => [...prev, { role: 'user', content: t }]);
    try {
      const d = await api('/api/chat', 'POST', { session_id: sid, message: t });
      setSid(d.session_id || sid);
      const meta = `Ответ за ${d.latency_ms} мс · ${d.product || ''}`;
      setMsgs(d.messages.map((m, i, arr) => i === arr.length - 1 && m.role === 'assistant' ? { ...m, meta } : m));
      setDebugText(`Время: ${d.latency_ms} мс\nТема: ${d.intent}\nПродукт: ${d.product}\nИсточники:\n${d.sources.length ? d.sources.map((s) => `- ${s.file} — ${s.title}`).join('\n') : 'не найдены'}`);
    } catch {
      setMsgs((prev) => [...prev, { role: 'assistant', content: 'Не удалось получить ответ от сервера. Проверьте, что сервер запущен, и попробуйте ещё раз.' }]);
    }
    setBusy(false);
    loadHistory();
    inputRef.current?.focus();
  }, [input, busy, sid, loadHistory]);

  async function submitLead({ name, phone, company, consent }) {
    const d = await api('/api/lead', 'POST', { session_id: sid, name, phone, company, consent });
    if (d.ok) {
      setLeadOpen(false);
      showToast('Заявка отправлена, резюме диалога передано менеджеру');
      loadHistory();
      return true;
    }
    showToast(d.error || 'Не удалось отправить заявку');
    return false;
  }

  const hasUserMsg = msgs.some((m) => m.role === 'user');

  return (
    <>
      <header className="top"><div className="top-in">
        <div className="brand"><div className="mark">МТС</div><div>EnergyTool<small>ИИ-консультант по энергетике для бизнеса</small></div></div>
        <div className="actions">
          <button className="btn menu" type="button" onClick={() => setSideOpen((v) => !v)}>Меню</button>
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
            <div className="lead-row"><button className="linkbtn" type="button" onClick={() => setLeadOpen(true)}>Оставить заявку менеджеру →</button></div>
          </div>
        </main>
      </div>
      <LeadModal open={leadOpen} onClose={() => setLeadOpen(false)} onSubmit={submitLead} />
      <div className={toast ? 'toast on' : 'toast'}>{toast || 'Заявка отправлена'}</div>
    </>
  );
}
