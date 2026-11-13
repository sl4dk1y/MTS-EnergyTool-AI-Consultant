const { spawn } = require('child_process');
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
async function req(p, method, body) {
  const r = await fetch('http://localhost:8000' + p, { method: method || 'GET', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return r.json();
}
(async () => {
  const srv = spawn(process.execPath, ['server.js'], { cwd: __dirname, stdio: 'inherit' });
  try {
    await sleep(2000);
    console.log('health', JSON.stringify(await req('/api/health')));
    const c1 = await req('/api/chat', 'POST', { message: 'Хочу солнечную станцию на крышу склада' });
    console.log('solar intent=' + c1.intent + ' product=' + c1.product + ' sources=' + c1.sources.length);
    console.log('solar answer=' + c1.answer);
    const c2 = await req('/api/chat', 'POST', { message: 'Хочу видеть расход отдельно по цехам, нужен учёт' });
    console.log('askue intent=' + c2.intent + ' sources=' + c2.sources.length);
    const lead = await req('/api/lead', 'POST', { session_id: c1.session_id, name: 'Тест Тестов', phone: '+70000000000', company: 'Тест', consent: true });
    console.log('lead ok=' + lead.ok);
    if (c1.intent !== 'solar' || !c1.sources.length) throw new Error('solar check failed');
    if (c2.intent !== 'askue') throw new Error('askue check failed');
    if (!lead.ok) throw new Error('lead check failed');
    console.log('ALL TESTS PASSED');
  } finally { srv.kill(); }
})().catch((e) => { console.error(e); process.exit(1); });
