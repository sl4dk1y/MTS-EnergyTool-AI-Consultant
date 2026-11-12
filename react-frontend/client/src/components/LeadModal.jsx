import React, { useState } from 'react';

export default function LeadModal({ open, onClose, onSubmit }) {
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [company, setCompany] = useState('');
  const [consent, setConsent] = useState(false);
  if (!open) return null;
  async function handleSubmit(e) {
    e.preventDefault();
    const ok = await onSubmit({ name, phone, company, consent });
    if (ok) { setName(''); setPhone(''); setCompany(''); setConsent(false); }
  }
  return (
    <div className="modal on" id="leadModal">
      <form onSubmit={handleSubmit}>
        <h3>Заявка менеджеру</h3>
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: 14, lineHeight: 1.5 }}>Менеджер получит контакты и краткое резюме этого диалога. Для хакатона используйте только тестовые данные.</p>
        <label>ФИО<input name="name" type="text" required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>Телефон<input name="phone" type="tel" required autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
        <label>Компания<input name="company" type="text" autoComplete="organization" value={company} onChange={(e) => setCompany(e.target.value)} /></label>
        <label className="check"><input type="checkbox" name="consent" required style={{ marginTop: 3 }} checked={consent} onChange={(e) => setConsent(e.target.checked)} />Согласен на обработку персональных данных</label>
        <button className="btn primary" type="submit">Отправить заявку</button>
        <button className="btn" type="button" onClick={onClose}>Отмена</button>
      </form>
    </div>
  );
}
