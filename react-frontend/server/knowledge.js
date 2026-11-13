const fs = require('fs');
const path = require('path');

const WORD_RE = /[0-9a-z\u0430-\u044f\u0451]{3,}/gi;
const STOP = new Set(['что', 'как', 'для', 'при', 'это', 'или', 'нам', 'наш', 'ваш', 'чтобы', 'если', 'есть', 'можно', 'нужно', 'какой', 'какая']);

const PRODUCTS = [
  { id: 'solar', name: 'Солнечные электростанции', file: '20-products/23-solar.md', short: 'Станция под ключ на кровле или участке: покрывает часть собственного потребления и снижает платёж сбытовой компании.', audience: 'Бизнес с дневной нагрузкой: склады, производства, торговые и бизнес-центры', facts: ['Типовой проект: 15-500 кВт, больше 500 кВт - индивидуальный проект', 'Ориентир: около 70 руб/Вт под ключ, станция 15 кВт - около 1 млн руб', 'Окупаемость: 7-9 лет по индикативной цене, обычно 4-6 лет после проработки площадки', 'Панели Grade A, гарантия 25 лет, инвертор 10-15 лет', 'Типовая станция 100 кВт: около 2 месяцев от подписания до запуска', 'Рассрочка до 5 лет, возможен сезонный график и поэтапный ввод', 'Сетевая станция при отключении сети не работает; автономию даёт только гибридная с накопителями'] },
  { id: 'orem', name: 'Выход на оптовый рынок', file: '20-products/21-orem.md', short: 'Сопровождение перехода с розничного рынка на оптовый: предприятие покупает электроэнергию ближе к оптовой цене.', audience: 'Промышленные предприятия с присоединённой мощностью от 750 кВт', facts: ['Порог: от 750 кВт присоединённой мощности, ниже выход невозможен', '750 кВт - 20 МВт: участие через энергосбытовую компанию-посредника', 'От 20 МВт: возможно прямое участие', 'Экономия по реализованным проектам: 5-15%, точная цифра только после расчёта', 'Сопровождение от 300 000 руб, типовой срок около полугода', 'Расчёт бесплатный, делается до старта проекта'] },
  { id: 'askue', name: 'Облачная платформа учёта', file: '20-products/22-askue.md', short: 'Приборы учёта передают данные в облако МТС, а клиент видит потребление, отчёты и события в веб-кабинете и приложении.', audience: 'Сетевые и сбытовые компании, промышленность, УК, ретейл, бизнес-центры', facts: ['На платформе более 90 000 приборов учёта', 'Поддерживается более 300 типов отечественных приборов, УСПД и концентраторов', 'Ориентир: 25 000 руб в год за прибор, цена зависит от проекта', 'Конфигурации Профи и Лайт, ограничений по числу приборов нет', 'Трафик включён, свои серверы клиенту не нужны', 'Демо-доступ по запросу и пилотное подключение части приборов'] },
  { id: 'losses', name: 'Поиск коммерческих потерь', file: '20-products/24-losses.md', short: 'Платформа анализирует потребление и формирует список приборов для приоритетной выездной проверки.', audience: 'Электросетевые компании', facts: ['Только для электросетевых компаний', 'Результат: ранжированный список приборов для проверки, а не факт хищения', 'Подтверждаемость на одном внедрении: 42% против 17-18% на старте', 'Обучающий массив: более 30 млн записей суточного потребления', 'Минимум: суточные данные день/ночь, архив от 1 года', 'Персональные данные абонентов не требуются'] },
  { id: 'meter', name: 'Распознавание показаний по фото', file: '20-products/25-meter-reading.md', short: 'Абонент фотографирует прибор в Telegram-бот, модель считывает показание и передаёт его в систему организации.', audience: 'Ресурсоснабжающие и сетевые организации, управляющие компании', facts: ['Приём показаний по фото через Telegram-бот, отдельное приложение не нужно', 'Электроэнергия и вода; тепло и газ - только как отдельная доработка', 'Точность: показания электроэнергии более 95%, воды и штрих-кодов более 90%', 'Считываются показания по тарифам, серийный номер, год производства, штрих-код', 'В коммерческий учёт показание попадает только после верификации', 'Персональные данные абонентов на стороне МТС не обрабатываются'] },
  { id: 'general', name: 'Общие вопросы EnergyTool', file: '20-products/26-general.md', short: 'Коммерческий и технический учёт, аналитика потребления, снижение затрат на электроэнергию и солнечная генерация.', audience: 'Любой бизнес, которому нужно снизить расходы на электроэнергию', facts: ['Предварительная оценка экономического эффекта бесплатна', 'Если расчёт покажет, что проект невыгоден, мы скажем об этом прямо', 'МТС не является энергосбытовой компанией и не продаёт электроэнергию', 'Работаем по всей России; данные клиента остаются у клиента', 'Под ключ от одного исполнителя: приборы, монтаж, платформа, сопровождение'] },
];

function words(text) {
  const out = new Set();
  const found = (text || '').toLowerCase().match(WORD_RE) || [];
  for (const w of found) {
    const n = w.replace(/\u0451/g, 'е');
    if (!STOP.has(n)) out.add(n);
  }
  return out;
}

function split(text, fname) {
  const chunks = [];
  let title = 'Начало';
  let cur = [];
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith('#')) {
      if (cur.length) chunks.push({ file: fname, title, text: cur.join(' ').slice(0, 1500) });
      title = line.replace(/^#+\s*/, '').trim();
      cur = [];
    } else if (line.trim()) cur.push(line.trim());
  }
  if (cur.length) chunks.push({ file: fname, title, text: cur.join(' ').slice(0, 1500) });
  return chunks.length ? chunks : [{ file: fname, title, text: text.slice(0, 1500) }];
}

function candidateDirs() {
  const dirs = [];
  if (process.env.KB_DIR) dirs.push(path.resolve(process.env.KB_DIR));
  dirs.push(path.join(__dirname, '..', 'knowledge-base'));
  dirs.push(path.join(__dirname, 'knowledge-base'));
  dirs.push('D:/для хахахахахахатона/Данные-extracted/Данные/knowledge-base');
  return dirs;
}

let KB = null;
function loadKb() {
  if (KB) return KB;
  let docs = [];
  for (const d of candidateDirs()) {
    if (!fs.existsSync(d)) continue;
    const files = [];
    (function walk(dir) {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else if (e.name.endsWith('.md')) files.push(p);
      }
    })(d);
    files.sort();
    for (const p of files) {
      try { docs.push(...split(fs.readFileSync(p, 'utf-8'), path.basename(p))); } catch {}
    }
    if (docs.length) break;
  }
  if (!docs.length) {
    for (const pr of PRODUCTS) docs.push({ file: pr.file, title: pr.name, text: pr.short + ' ' + pr.facts.join(' ') });
  }
  for (const doc of docs) doc.words = words(doc.title + ' ' + doc.text);
  KB = docs;
  return KB;
}

function search(query, topK = 3) {
  const docs = loadKb();
  const qw = words(query);
  const scored = [];
  for (const doc of docs) {
    let inter = 0;
    for (const w of qw) if (doc.words.has(w)) inter++;
    if (!inter) continue;
    let score = inter;
    if ([...qw].some((w) => doc.title.toLowerCase().includes(w))) score += 2;
    scored.push([score, doc]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  return scored.slice(0, topK).map(([score, doc]) => ({ file: doc.file, title: doc.title, excerpt: doc.text.slice(0, 420), score }));
}

function detectIntent(text) {
  const t = (text || '').toLowerCase();
  if (['солн', 'панел', 'генерац'].some((k) => t.includes(k))) return 'solar';
  if (['оптов', 'орем', 'рынок', '750'].some((k) => t.includes(k))) return 'orem';
  if (['потер', 'хищен', 'краж', 'небаланс'].some((k) => t.includes(k))) return 'losses';
  if (['фото', 'фотограф', 'показан', 'распозна'].some((k) => t.includes(k))) return 'meter';
  if (['учёт', 'учет', 'аскуэ', 'платформ', 'кабинет', 'цех', 'арендатор', 'магазин', 'счётчик', 'счетчик'].some((k) => t.includes(k))) return 'askue';
  return 'general';
}

module.exports = { PRODUCTS, search, detectIntent, loadKb };
