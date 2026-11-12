export async function api(path, method = 'GET', body) {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

export const EXAMPLE_QUESTIONS = [
  'У нас крыша на два ската — восток и запад. Панели можно поставить на оба?',
  'Хочу видеть расход отдельно по цехам. В вашем кабинете так можно?',
  'Мы стали больше работать вечером, и счёт вырос. Можно разобраться, из-за чего?',
  'Хотим разобраться, подходит ли нам оптовый рынок',
  'Можно сначала поставить станцию на 60 кВт, а потом расширить?',
  'В счётчиках стоят симки другого оператора. Их надо менять?',
];
