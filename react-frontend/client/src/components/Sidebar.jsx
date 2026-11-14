import React from 'react';

const topics = [
  'Разобраться в энергопотреблении',
  'Подготовить данные к консультации',
  'Обсудить возможные следующие шаги',
];

export default function Sidebar({ sessions, onAsk, onOpenSession, open }) {
  return (
    <aside className={open ? 'side open' : 'side'} id="side">
      <section className="panel">
        <h2>Темы для разговора</h2>
        <div id="topics">
          {topics.map((topic) => (
            <button className="btn" type="button" key={topic} onClick={() => onAsk(topic)}>{topic}</button>
          ))}
        </div>
      </section>
      <section className="panel">
        <h2>История диалогов</h2>
        <div id="history">
          {sessions.length === 0 ? 'Пока пусто' : sessions.slice(0, 8).map((session) => (
            <button className="hist" type="button" key={session.id} onClick={() => onOpenSession(session.id)}>
              {(session.created_at || '').replace('T', ' ').slice(0, 16)} · {session.title || 'Диалог'} · сообщений: {session.message_count ?? 0}{session.has_lead ? ' · есть заявка' : ''}
            </button>
          ))}
        </div>
        <p style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>Здесь показаны диалоги, идентификаторы которых сохранены в этом браузере. Нажмите на запись, чтобы открыть её.</p>
      </section>
    </aside>
  );
}
