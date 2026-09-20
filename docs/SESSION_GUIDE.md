# Работа с Claude Tools Factory: гайд по сессиям

Практическая шпаргалка: как начать сессию с конкретным проектом (например,
Board / staging.projectsimple.ai) и как экономить токены при дальнейшей
работе — в первую очередь при генерации тест-кейсов для фичи.

Всё ниже проверено вручную на реальном MCP-сервере `factory`, а не взято
только из README — там местами устаревшее описание (см. раздел
«Расхождения с README» в конце).

---

## 1. Перед первой сессией: проверить `.mcp.json`

Сервер поднимается как сабпроцесс. Если `command` в `.mcp.json` — просто
`"python"`, а на машине в PATH только `python3`, сервер не подключится, и
`factory`-инструменты не появятся в списке (ошибка вида
`Executable not found in $PATH: python`).

Рабочий вариант:

```json
{
  "mcpServers": {
    "factory": {
      "command": "./mcp_server/.venv/bin/python",
      "args": ["-m", "mcp_server"],
      "cwd": "."
    }
  }
}
```

После правки `.mcp.json` инструменты не появятся сами — нужно
переподключить MCP (`/mcp` в Claude Code или перезапуск сессии).

Если venv ещё не собран: `./setup.sh` (сам находит python >= 3.11 на PATH).

---

## 2. Креды: `env/factory.env`, а не keyring

Несмотря на то что README говорит про OS keyring, актуальный `login`
целиком работает от `env/factory.env`. Формат — блок из трёх переменных на
проект:

```
BOARD_URL=https://staging.projectsimple.ai
BOARD_USERNAME=board.simple.qa+08@gmail.com
BOARD_PASSWORD=QA_board00
```

Чтобы добавить новый проект — просто добавить такой блок с новым префиксом
(`<PREFIX>_URL` / `<PREFIX>_USERNAME` / `<PREFIX>_PASSWORD`). Никаких
`patterns/web/<host>.yaml` и `keyring set ...` заводить не нужно (см. раздел 6).

---

## 3. Как начать сессию с конкретным проектом

```
login(target="board", keep_open=true)
```

Важно: `target` для `login` — это **префикс переменных окружения**
(`board`, `autotrade`, `snaprefund_claims`, ...), а не сам URL. Логин ищет
префикс, подстрокой входящий в `target` (без учёта регистра), и по нему
достаёт `<PREFIX>_URL/_USERNAME/_PASSWORD`. Если передать полный URL, у
которого нет префикса в самой строке (например `https://staging.
projectsimple.ai` не содержит `board`), логин не найдёт креды и вернёт
`no env block matches`.

`keep_open: true` держит браузер живым между вызовами — без этого сессия
закрывается сразу после логина, и `map/view/touch/hidden/tc` не смогут её
переиспользовать.

После успешного логина сессия зарегистрирована сразу под двумя ключами:
префиксом (`"board"`) и реальным итоговым URL
(`"https://staging.projectsimple.ai"`). Для всех следующих вызовов в этой
сессии удобнее использовать реальный URL — тогда дампы лягут в
`QA/staging.projectsimple.ai/...`, а не в `QA/board/...` (что менее
читаемо, если попутно используете `login` под несколькими короткими
префиксами в одном проекте).

В конце сессии — обязательно:

```
close_session(target="https://staging.projectsimple.ai")
```

Иначе браузерный процесс остаётся висеть до перезапуска MCP-сервера.

---

## 4. Экономия токенов при генерации тест-кейсов

### 4.1 Не гонять `map` → `view` → `touch` → `hidden` вручную

`tc` уже делает весь пайплайн сам (`extract_routes → resolve_params →
build_map → view_pages → touch_page → probe_hidden → propose_tc →
qa_render`). Отдельные вызовы `map/view/touch/hidden` нужны только когда вы
сами разбираетесь, что происходит на странице (отладка, разведка нового
UI) — для обычной генерации тест-кейсов на фичу хватает одного вызова:

```
tc(target="https://staging.projectsimple.ai", page="/elenaproject/features/sprints")
```

### 4.2 Всегда сужайте через `page`

`page` — самый весомый рычаг экономии. Без него `map/view/touch/hidden/tc`
проходят по **всем** маршрутам SPA (у Board это 50–70+ страниц) —
это дорого и по токенам (весь вывод возвращается в контекст), и по времени
(десятки скриншотов + сканов DOM).

`page` принимает либо шаблон маршрута (`/:accountId/:projectId/sprints`),
либо конкретный путь (`/elenaproject/features/sprints`, без `https://` —
префикс origin обрезается автоматически). Указывайте путь конкретной
фичи — сканирование пойдёт только по нему (и статически связанным с ним
страницам, если они попадают в тот же `page`-фильтр).

### 4.3 Самый дешёвый вариант — A→Z runner из шелла

Если не нужен интерактивный выбор из TC-меню, а нужны сразу готовые
Playwright POM + spec файлы — это одна shell-команда, без обмена
сообщениями с моделью вообще:

```bash
python -m mcp_server.runner \
  --target https://staging.projectsimple.ai/ \
  --url /isteu/team/developers
```

Логинится, идёт по `--url`, гоняет `view → touch → hidden → tc → qa`,
пишет файлы на диск и печатает только сводку (`TC count`, `Output dir`,
топ-5 файлов). Это стоит на порядок дешевле любого пути через MCP-диалог,
если вам не нужно ничего обсуждать по ходу — просто нужен результат.

### 4.4 Никогда не запускайте `map/view/touch/hidden/tc` параллельно

Все они делят один и тот же живой браузер/страницу на `target`. Параллельные
вызовы реально ломают навигацию — на практике это выглядит как один вызов
успешно уходит на нужную страницу, а остальные попадают на `/error?code=404`
или получают чужие resolved-параметры (`accountId`, `projectId` и т.д.).
Вызывайте их строго последовательно, даже если по виду они независимы.

---

## 5. Типовой цикл: тест-кейсы для фичи

```
1. login(target="board", keep_open=true)
2. tc(target="https://staging.projectsimple.ai", page="/elenaproject/features/sprints")
   -> возвращает меню TC_NN_MM + пишет POM/spec в QA/<host>/pom и /po
3. Прочитать сгенерированные файлы, выбрать нужные TC
4. (если нужно дособрать вручную) view(target, page="...") /
   touch(target, page="...") — точечно, по одной странице
5. close_session(target="https://staging.projectsimple.ai")
```

Куда смотреть после `tc`/`map`/`view`/`touch`/`hidden`:

```
QA/<host>/dom/map/graph.json           — граф маршрутов
QA/<host>/dom/view/<page-slug>.json    — интерактивные элементы + rect
QA/<host>/dom/view/screenshots/*.png   — скриншот страницы
QA/<host>/dom/containers/<slug>.json   — элементы по контейнерам
QA/<host>/dom/functions/<slug>.json    — что открывает каждая кнопка
QA/<host>/pom/, QA/<host>/po/          — сгенерированные POM/spec
```

---

## 6. Расхождения с README (на дату написания)

- README: «Login patterns read credentials from your OS keyring». На деле
  `login` берёт креды из `env/factory.env` и вообще не трогает keyring и
  `patterns/web/<host>.yaml`. Эти паттерн-файлы используются только
  `sign_up` (и то по схеме `<host>.signup.yaml`, не `<host>.yaml`).
- `agents/qa-mapper.md`, `agents/qa-prober.md`, `agents/qa-author.md` и
  остальные в `agents/` описывают более развёрнутый саб-агентный пайплайн
  (сбор evidence → генерация POM). Это оформлено как ассеты плагина, но в
  обычной сессии Claude Code они не всегда зарегистрированы как доступные
  агенты — если `Agent`-тул их не видит, используйте `tc`/раннер напрямую,
  как описано выше.
- `map/view/touch/hidden/routes/close_session` не показаны в основном
  списке инструментов — это `kind: internal` тулы. Чтобы их вызвать,
  сначала найдите через поиск деферред-тулов (в Claude Code —
  `ToolSearch` с `select:mcp__factory__map,mcp__factory__view,...`), и
  только потом вызывайте.
