import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, EXAMPLE_QUESTIONS } from './api.js';
import Sidebar from './components/Sidebar.jsx';
import LeadModal from './components/LeadModal.jsx';

const SESSION_IDS_KEY = 'mts-session-ids';
const GREETING = 'Здравствуйте! Я консультант МТС EnergyTool. Опишите задачу — вместе разберёмся, какие сведения нужны и что можно сделать дальше.';

function readSessionIds() {
  try {
    const value = JSON.parse(localStorage.getItem(SESSION_IDS_KEY) || '[]');
    return Array.isArray(value) ? [...new Set(value.filter((id) => typeof id === 'string'))].slice(-50) : [];
  } catch {
    return [];
  }
}

function rememberSession(id) {
  try {
    const ids = readSessionIds();
    if (!ids.includes(id)) localStorage.setItem(SESSION_IDS_KEY, JSON.stringify([...ids, id].slice(-50)));
  } catch {}
}

function getErrorMessage(error, fallback) {
  return error && error.message ? error.message : fallback;
}

export default function App() {
  const [sid, setSid] = useState('');
  const [sessionLoading, setSessionLoading] = useState(false);
  const [sessionError, setSessionError] = useState('');
  const [msgs, setMsgs] = useState([{ role: 'assistant', content: GREETING }]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [sessions, setSessions] = useState([]);
  const [showDebug, setShowDebug] = useState(false);
  const [debugText, setDebugText] = useState('Отладка: пока нет ответов. Здесь появятся время ответа и источники.');
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

  const chatRef = useRef(null);
  const inputRef = useRef(null);
  const toastTimer = useRef(null);
  const startedRef = useRef(false);

  const showToast = useCallback((text) => {
    setToast(text);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(''), 3200);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem('mts-theme', theme); } catch {}
  }, [theme]);

  const loadHistory = useCallback(async () => {
    const ids = readSessionIds();
    if (ids.length === 0) {
      setSessions([]);
      return;
    }
    try {
      const list = await api('/api/sessions/lookup', 'POST', { ids });
      setSessions(Array.isArray(list) ? list : []);
    } catch {
      setSessions([]);
    }
  }, []);

  const startFresh = useCallback(async () => {
    setSessionLoading(true);
    setSid('');
    setSessionError('');
    setMsgs([{ role: 'assistant', content: GREETING }]);
    setDebugText('Отладка: создаётся новый диалог.');
    try {
      const session = await api('/api/sessions', 'POST', {});
      if (!session || typeof session.id !== 'string' || !session.id) throw new Error('Сервер не вернул идентификатор сессии.');
      setSid(session.id);
      rememberSession(session.id);
      setDebugText('Отладка: создан новый диалог.');
      await loadHistory();
    } catch (error) {
      const message = getErrorMessage(error, 'Не удалось создать диалог. Проверьте соединение с сервером.');
      setSessionError(message);
      showToast(message);
    } finally {
      setSessionLoading(false);
    }
  }, [loadHistory, showToast]);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    loadHistory();
    startFresh();
  }, [loadHistory, startFresh]);

  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [msgs, busy]);

  const openSession = useCallback(async (id) => {
    if (!id || busy || sessionLoading) return;
    setBusy(true);
    try {
      const session = await api(`/api/sessions/${encodeURIComponent(id)}`);
      setSid(session.id);
      rememberSession(session.id);
      const history = Array.isArray(session.messages) ? session.messages : [];
      setMsgs(history.length ? history.map((message) => ({
        role: message.role,
        content: message.content,
        meta: message.elapsed_ms == null ? '' : `Ответ за ${message.elapsed_ms} мс`,
      })) : [{ role: 'assistant', content: GREETING }]);
      setSessionError('');
      setSideOpen(false);
    } catch (error) {
      showToast(getErrorMessage(error, 'Не удалось открыть диалог.'));
    } finally {
      setBusy(false);
    }
  }, [busy, sessionLoading, showToast]);

  const send = useCallback(async (text) => {
    const question = (text ?? input).trim();
    if (!question || busy) return;
    if (!sid) {
      showToast(sessionError || 'Сначала дождитесь создания диалога.');
      return;
    }
    setBusy(true);
    setInput('');
    if (inputRef.current) inputRef.current.style.height = 'auto';
    setMsgs((previous) => [...previous, { role: 'user', content: question }]);
    try {
      const result = await api(`/api/sessions/${encodeURIComponent(sid)}/messages`, 'POST', { text: question });
      const answer = result.assistant_message || {};
      if (typeof answer.content !== 'string') throw new Error('Сервер вернул ответ в неизвестном формате.');
      const sources = Array.isArray(answer.sources) ? answer.sources : [];
      const meta = answer.elapsed_ms == null ? '' : `Ответ за ${answer.elapsed_ms} мс`;
      setMsgs((previous) => [...previous, { role: 'assistant', content: answer.content, meta }]);
      setDebugText(`Время: ${answer.elapsed_ms ?? '—'} мс\nИсточники:\n${sources.length ? sources.map((source) => `- ${source}`).join('\n') : 'не найдены'}`);
      if (result.lead_suggested) showToast('Можно оформить заявку менеджеру.');
    } catch (error) {
      const message = error && error.status === 503
        ? 'Модель пока не настроена на сервере. Попробуйте позже или передайте вопрос специалисту.'
        : `Не удалось получить ответ: ${getErrorMessage(error, 'ошибка сервера')}`;
      setMsgs((previous) => [...previous, { role: 'assistant', content: message }]);
    } finally {
      setBusy(false);
      await loadHistory();
      inputRef.current?.focus();
    }
  }, [input, busy, sid, sessionError, loadHistory, showToast]);

  const openLead = useCallback(async () => {
    if (!sid) {
      showToast(sessionError || 'Сначала дождитесь создания диалога.');
      return;
    }
    try {
      const draft = await api(`/api/sessions/${encodeURIComponent(sid)}/lead/draft`, 'POST', {});
      setLeadDraft(draft);
    } catch {
      setLeadDraft(null);
    }
    setLeadOpen(true);
  }, [sid, sessionError, showToast]);

  const submitLead = useCallback(async ({ full_name, phone, company, consent }) => {
    if (!sid) return 'Не удалось определить диалог. Создайте новый и повторите отправку.';
    try {
      await api(`/api/sessions/${encodeURIComponent(sid)}/lead`, 'POST', {
        full_name,
        phone,
        company,
        summary: leadDraft && leadDraft.summary ? leadDraft.summary : '',
        consent,
      });
      setLeadOpen(false);
      showToast('Заявка отправлена менеджеру.');
      await loadHistory();
      try {
        const session = await api(`/api/sessions/${encodeURIComponent(sid)}`);
        if (Array.isArray(session.messages)) {
          setMsgs(session.messages.map((message) => ({ role: message.role, content: message.content })));
        }
      } catch {}
      return true;
    } catch (error) {
      return getErrorMessage(error, 'Не удалось отправить заявку.');
    }
  }, [sid, leadDraft, loadHistory, showToast]);

  const doHandoff = useCallback(async () => {
    if (!sid || busy) {
      if (!sid) showToast(sessionError || 'Сначала дождитесь создания диалога.');
      return;
    }
    setBusy(true);
    try {
      const result = await api(`/api/sessions/${encodeURIComponent(sid)}/handoff`, 'POST', {});
      if (result.assistant_message && typeof result.assistant_message.content === 'string') {
        setMsgs((previous) => [...previous, { role: 'assistant', content: result.assistant_message.content }]);
      }
      showToast('Вопрос передан специалисту.');
      await loadHistory();
    } catch (error) {
      showToast(getErrorMessage(error, 'Не удалось передать вопрос.'));
    } finally {
      setBusy(false);
    }
  }, [sid, busy, sessionError, loadHistory, showToast]);

  const hasUserMsg = msgs.some((message) => message.role === 'user');
  const controlsDisabled = busy || sessionLoading || !sid;

  return (
    <>
      <header className="top"><div className="top-in">
        <div className="brand"><div className="mark">МТС</div><div>EnergyTool<small>ИИ-консультант для бизнеса</small></div></div>
        <div className="actions">
          <button className="btn menu" type="button" onClick={() => setSideOpen((value) => !value)}>Меню</button>
          <button className="btn" type="button" aria-label="Переключить тему" onClick={() => setTheme((value) => (value === 'dark' ? 'light' : 'dark'))}>{theme === 'dark' ? '☀️ Светлая' : '🌙 Тёмная'}</button>
          <button className="btn" type="button" onClick={() => setShowDebug((value) => !value)}>Отладка</button>
          <button className="btn primary" type="button" onClick={startFresh} disabled={sessionLoading || busy}>Новый диалог</button>
        </div>
      </div></header>
      <div className="wrap">
        <Sidebar sessions={sessions} onAsk={send} onOpenSession={openSession} open={sideOpen} />
        <main className="main">
          <div className={hasUserMsg ? 'hero hide' : 'hero'}>
            <h1>Разберёмся в вашей задаче <span>и подберём дальнейшие шаги</span></h1>
            <p>Опишите ситуацию своими словами. Консультант уточнит детали и, если вы захотите, поможет подготовить заявку менеджеру.</p>
            {sessionError ? <p className="error" role="alert">{sessionError}</p> : null}
          </div>
          <div id="chat" aria-live="polite" ref={chatRef}>
            {msgs.map((message, index) => (
              <div className={`row ${message.role === 'user' ? 'user' : 'bot'}`} key={`${message.role}-${index}`}>
                <div className="avatar">{message.role === 'user' ? 'Вы' : 'ET'}</div>
                <div><div className="msg">{message.content}</div>{message.meta ? <div className="meta">{message.meta}</div> : null}</div>
              </div>
            ))}
            {busy && <div className="typing">Консультант печатает…</div>}
            {sessionLoading && <div className="typing">Создаём диалог…</div>}
          </div>
          <div className="chips">
            {EXAMPLE_QUESTIONS.map((question) => <button className="chip" type="button" key={question} onClick={() => send(question)} disabled={controlsDisabled}>{question}</button>)}
          </div>
          {showDebug && <div id="debug" className="on">{debugText}</div>}
          <div className="composer">
            <div className="composer-in">
              <textarea ref={inputRef} rows={1} placeholder="Опишите вашу задачу…" aria-label="Ваш вопрос" value={input}
                disabled={controlsDisabled}
                onChange={(event) => { setInput(event.target.value); event.target.style.height = 'auto'; event.target.style.height = `${Math.min(event.target.scrollHeight, 120)}px`; }}
                onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); } }} />
              <button className="btn primary" type="button" onClick={() => send()} disabled={controlsDisabled || !input.trim()}>Отправить</button>
            </div>
            <div className="lead-row">
              <button className="linkbtn" type="button" onClick={openLead} disabled={controlsDisabled}>Оставить заявку менеджеру →</button>
              <button className="linkbtn" type="button" onClick={doHandoff} disabled={controlsDisabled}>Передать вопрос специалисту →</button>
            </div>
          </div>
        </main>
      </div>
      <LeadModal open={leadOpen} draft={leadDraft} onClose={() => setLeadOpen(false)} onSubmit={submitLead} />
      <div className={toast ? 'toast on' : 'toast'} role="status">{toast}</div>
    </>
  );
}
