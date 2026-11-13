import React from 'react';

export default function Sidebar({ products, sessions, onAsk, open }) {
  return (
    <aside className={open ? 'side open' : 'side'} id="side">
      <section className="panel">
        <h2>EnergyTool в цифрах</h2>
        <div className="stats">
          <div className="stat"><b>90 000+</b><span>приборов на платформе учёта</span></div>
          <div className="stat"><b>15–500 кВт</b><span>типовые солнечные станции</span></div>
          <div className="stat"><b>от 750 кВт</b><span>порог выхода на оптовый рынок</span></div>
          <div className="stat"><b>&gt;95%</b><span>точность показаний по фото</span></div>
        </div>
      </section>
      <section className="panel">
        <h2>Продукты</h2>
        <div id="products">
          {products.length === 0 ? 'Загружаю…' : products.map((p) => (
            <article className="prod" key={p.id}>
              <b>{p.name}</b>
              <p>{p.short}</p>
              <p>{p.audience}</p>
              <button className="btn" type="button" onClick={() => onAsk(`Расскажи про продукт: ${p.name}`)}>Спросить про это</button>
            </article>
          ))}
        </div>
      </section>
      <section className="panel">
        <h2>История диалогов</h2>
        <div id="history">
          {sessions.length === 0 ? 'Пока пусто' : sessions.slice(0, 8).map((s) => (
            <div className="hist" key={s.id}>{(s.created_at || '').replace('T', ' ').slice(0, 16)} · {s.title || 'Диалог'} · сообщений: {s.message_count ?? 0}{s.has_lead ? ' · есть заявка' : ''}</div>
          ))}
        </div>
        <p style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>История сохраняется для менеджера, но не открывается кликом — чтобы случайно не вернуться в прошлый диалог. Каждая загрузка страницы начинает новый диалог.</p>
      </section>
    </aside>
  );
}
