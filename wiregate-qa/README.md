# Wiregate QA — запуск у себя

Здесь лежат результаты тестирования:

- `BUGS.md` — баг-репорт (45 пунктов);
- `demo-bugs.webm` — видео с демонстрацией ключевых багов (42 с);
- `screenshots/` — скриншоты;
- `demo-bugs.js` — Playwright-сценарий, который воспроизводит баги в видимом браузере.

## 1. Поднять проект

Нужен установленный Docker Desktop (или Docker Engine с compose).

```bash
git clone https://gitlab.com/rndx2-public/wiregate-test-task.git
cd wiregate-test-task
docker compose up
```

Первый запуск занимает несколько минут: внутри контейнеров выполняется `yarn install`. Проект готов, когда:

- http://localhost:3000 показывает форму «Accueillir»;
- http://localhost:8000 отвечает JSON-ом (там будет 404, это само по себе баг №26).

> Windows: если `${PWD}` не подставляется, запускайте из Git Bash/WSL или замените в
> `docker-compose.yml` `${PWD:-./}` на `./`.

## 2. Посмотреть баги вживую в браузере

Нужен Node.js 18+. В отдельном терминале, в папке `wiregate-qa`:

```bash
npm init -y
npm i playwright
npx playwright install chromium
node demo-bugs.js
```

Откроется окно Chromium. Скрипт сам заполняет форму и подписывает каждый шаг жёлтым баннером сверху, в терминале
дублируется лог. Настройки:

```bash
PAUSE=5000 node demo-bugs.js                 # пауза между шагами дольше (мс)
APP=http://localhost:3000 API=http://localhost:8000 node demo-bugs.js
VIDEO_DIR=./videos node demo-bugs.js         # дополнительно записать видео
```

В PowerShell переменные задаются так: `$env:PAUSE=5000; node demo-bugs.js`.

## 3. Проверить данные в БД (баги №6, 18, 19)

```bash
docker compose exec db psql -U postgres -c "select id, first_name, email, password from users order by id;"
```

## 4. Логи бэкенда с паролями (баг №20)

```bash
docker compose logs backend | grep -A6 "POST request"
```
