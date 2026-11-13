import React, { useEffect, useState } from 'react';

export default function LeadModal({ open, draft, onClose, onSubmit }) {
  const [fullName, setFullName] = useState('');
  const [phone, setPhone] = useState('');
  const [company, setCompany] = useState('');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (open) {
      setFullName((draft && draft.full_name) || '');
      setPhone((draft && draft.phone) || '');
      setCompany((draft && draft.company) || '');
      setConsent(false);
      setError('');
    }
  }, [open, draft]);
  if (!open) return null;
  async function handleSubmit(e) {
    e.preventDefault();
    const res = await onSubmit({ full_name: fullName, phone, company, consent });
    if (res === true) { setFullName(''); setPhone(''); setCompany(''); setConsent(false); }
    else if (typeof res === 'string') setError(res);
  }
  return (
    <div className="modal on" id="leadModal">
      <form onSubmit={handleSubmit}>
        <h3>Заявка менеджеру</h3>
        <p style={{ margin: 0, color: 'var(--muted)', fontSize: 14, lineHeight: 1.5 }}>Менеджер получит контакты и краткое резюме этого диалога. Поля могут быть уже заполнены из диалога — проверьте их. Для хакатона используйте только тестовые данные.</p>
        {draft && draft.message ? <p style={{ margin: 0, color: 'var(--muted)', fontSize: 13 }}>{draft.message}</p> : null}
        {error ? <p style={{ margin: 0, color: 'var(--red)', fontSize: 13 }}>{error}</p> : null}
        <label>ФИО<input name="full_name" type="text" required autoComplete="name" value={fullName} onChange={(e) => setFullName(e.target.value)} /></label>
        <label>Телефон<input name="phone" type="tel" required autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>
        <label>Компания<input name="company" type="text" required autoComplete="organization" value={company} onChange={(e) => setCompany(e.target.value)} /></label>
        <label className="check"><input type="checkbox" name="consent" required style={{ marginTop: 3 }} checked={consent} onChange={(e) => setConsent(e.target.checked)} />Согласен на обработку персональных данных</label>
        <button className="btn primary" type="submit">Отправить заявку</button>
        <button className="btn" type="button" onClick={onClose}>Отмена</button>
      </form>
    </div>
  );
}
