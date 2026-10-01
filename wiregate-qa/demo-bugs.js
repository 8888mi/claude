// Демонстрация ключевых багов Wiregate в видимом браузере.
// Запуск: см. README.md рядом. Требуется поднятый проект (docker compose up).
//   APP=http://localhost:3000 API=http://localhost:8000 HEADLESS=0 node demo-bugs.js
const { chromium } = require('playwright');

const APP = process.env.APP || 'http://localhost:3000';
const API = process.env.API || 'http://localhost:8000';
const HEADLESS = process.env.HEADLESS === '1';
const VIDEO_DIR = process.env.VIDEO_DIR; // если задан — пишем видео
const PAUSE = +(process.env.PAUSE || 2500);

const valid = {
  firstName: 'John', lastName: 'Smith', phoneNumber: '12345678',
  emailAddress: `demo${Date.now()}@test.com`, password: 'Passw0rd!', confirmPassword: 'Passw0rd!',
};

(async () => {
  const browser = await chromium.launch({ headless: HEADLESS, slowMo: HEADLESS ? 0 : 120 });
  const ctxOpts = { viewport: { width: 1280, height: 900 } };
  if (VIDEO_DIR) ctxOpts.recordVideo = { dir: VIDEO_DIR, size: { width: 1280, height: 900 } };

  const open = async (extra = {}) => {
    const ctx = await browser.newContext({ ...ctxOpts, ...extra });
    const page = await ctx.newPage();
    page.apiResponses = [];
    page.on('response', (r) => { if (r.url().startsWith(API)) page.apiResponses.push(r.status()); });
    await page.goto(APP);
    await page.waitForTimeout(800);
    return page;
  };
  const banner = async (page, text) => {
    console.log('\n=== ' + text);
    await page.evaluate((t) => {
      let el = document.getElementById('__qa_banner');
      if (!el) {
        el = document.createElement('div'); el.id = '__qa_banner';
        el.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#111;color:#ff0;font:bold 18px sans-serif;padding:10px 16px;pointer-events:none';
        document.body.appendChild(el);
      }
      el.textContent = t;
    }, text);
  };
  const fill = async (page, data) => {
    for (const [k, v] of Object.entries({ ...valid, ...data })) await page.fill(`input[name="${k}"]`, v);
    await page.check('input[name="isAgree"]');
    await page.locator('input[name="isAgree"]').blur();
  };
  const step = async (page, text, fn) => { await banner(page, text); await fn(); await page.waitForTimeout(PAUSE); };

  // BUG 1-2: фронт пропускает пароль без заглавной/спецсимвола, бэк отвечает 400, а UI показывает успех
  let p = await open();
  await step(p, 'BUG: пароль "password1" проходит валидацию фронта (нет заглавной и спецсимвола)', () =>
    fill(p, { password: 'password1', confirmPassword: 'password1' }));
  await step(p, 'BUG: бэкенд вернул 400, но UI показывает "Thank you for subscription!"', () => p.click('form button'));
  console.log('    URL:', p.url(), ' ответ API:', p.apiResponses);
  await p.context().close();

  // BUG 3: Backspace в поле телефона отключает minLength
  p = await open();
  await step(p, 'Телефон "123" — ошибка min 8 символов (ожидаемо)', async () => {
    await fill(p, { phoneNumber: '123' });
  });
  await step(p, 'BUG: после нажатия Backspace телефон из 1 цифры считается валидным', async () => {
    await p.click('input[name="phoneNumber"]');
    await p.keyboard.press('Backspace');
    await p.keyboard.press('Backspace');
  });
  console.log('    кнопка disabled =', await p.$eval('form button', (b) => b.disabled));
  await p.context().close();

  // BUG 4: смена пароля после подтверждения — кнопка блокируется без сообщения
  p = await open();
  await fill(p, {});
  await step(p, 'BUG: меняем пароль после подтверждения — кнопка неактивна, ошибки не видно', async () => {
    await p.fill('input[name="password"]', 'Another1!');
    await p.locator('input[name="password"]').blur();
  });
  await p.context().close();

  // BUG 5: валидные французские имена отклоняются, мусор принимается
  p = await open();
  await step(p, 'BUG: реальные имена "Hélène" и "Bob" отклоняются', () =>
    fill(p, { firstName: 'Hélène', lastName: 'Bob' }));
  await step(p, 'BUG: а "....." и "--- \'" принимаются как имя и фамилия', () =>
    fill(p, { firstName: '.....', lastName: "--- '" }));
  await p.context().close();

  // BUG 6: неверные тексты ошибок
  p = await open();
  await step(p, 'BUG: ошибки "Ce champ est requis" для телефона и "valid first name" для фамилии', () =>
    fill(p, { phoneNumber: '+33612345678', lastName: 'Smith1' }));
  await step(p, 'BUG: чекбокс снят — сообщение об ошибке пустое', () => p.uncheck('input[name="isAgree"]'));
  await p.context().close();

  // BUG 7: мобильная вёрстка
  p = await open({ viewport: { width: 375, height: 812 }, isMobile: true });
  await step(p, 'BUG: на 375px поле "Votre nom" вылезает за карточку (min-width: 400px)', () => fill(p, {}));
  await p.context().close();

  // BUG 8: страница успеха доступна напрямую, кнопка Go back сжата
  p = await open();
  await step(p, 'BUG: /success открывается без регистрации; кнопка "Go back" 55px вместо 320px', () =>
    p.goto(APP + '/success'));
  await step(p, 'BUG: несуществующий маршрут — пустая белая страница без 404', () => p.goto(APP + '/no-such-page'));
  await p.context().close();

  // BUG 9 (API): перезапись чужой записи через поле id
  const r = await fetch(API + '/users/create', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: 1, first_name: 'HACKED', last_name: 'x', email: 'h@h.com', phone_number: '1', password: 'Passw0rd!', is_agree: true }),
  });
  console.log('\n=== BUG (API): POST с "id":1 перезаписывает пользователя #1 ->', r.status, await r.text());

  await browser.close();
})();
