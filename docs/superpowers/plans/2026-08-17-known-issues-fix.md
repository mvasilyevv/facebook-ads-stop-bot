# План: закрыть известные проблемы (17.08.2026)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Довести удалённый рабочий стол до состояния «нажал — подключился», вернуть
постбекам трекера способность пережить перезапуск воркера и подготовить включение
сканирования.

**Architecture:** Три независимых слоя. Контейнер стола (`deploy/vision-webtop/entrypoint.sh`)
перестаёт молча проглатывать отказ установки пароля канала. Операторский API и оба фронта
приводят обещание кнопки в соответствие с тем, что схема `rustdesk://` реально умеет.
Очередь задач получает срок жизни постбека, соразмерный окну деплоя, вместо двух минут.

**Tech Stack:** bash + RustDesk OSS (hbbs/hbbr), FastAPI + SQLAlchemy Core + PostgreSQL,
React + TanStack Router + Tailwind, pytest, vitest, fbctl + Docker Compose.

## Global Constraints

- Код, тесты и комментарии — по-русски; имена типов, API-полей и технических
  идентификаторов остаются английскими.
- Money-путь: сначала инвариант и regression test, потом правка.
- Один архитектурный слой или один вертикальный slice за PR.
- Секреты не попадают в operator UI, Telegram, URL, логи и breadcrumbs. Единственное
  действующее исключение — ссылка запуска стола, разрешённая владельцем явно.
- `pytest` только на изолированной БД. Прогон на боевой `:5433` сносит `offers`/`offer_rules`.
- Прод-фаервол правит владелец. Агент отдаёт команду, но не исполняет её сам.
- Перед работой: `git fetch` и сверка с `origin/main` — владелец параллельно работает через Codex.
- Коммит по завершении задачи делает исполнитель; push и релиз — по явному «да» владельца.

## Что проверено на живом проде 17.08, 09:30 UTC

Эти факты установлены командами на хосте `62.60.150.133`, а не выведены из кода.

1. `gosu vision … rustdesk --password …` внутри `fb_agent_desktop-vision-webtop-1` отвечает
   `Installation and administrative privileges required!`. В `entrypoint.sh:192-193` вызов
   прикрыт `>/dev/null 2>&1 || true`, поэтому отказ невидим.
2. Тот же вызов **от root** с чистым `HOME` (сервис для этого конфига не запущен) отвечает
   `Done!`, код 0, и пишет `RustDesk.toml` с ключом `password`. `timeout` в образе есть.
3. RustDesk при старте генерирует **свой случайный** постоянный пароль, если ключа нет.
   Именно он и стоял на столе — отсюда «Wrong password» на верном пароле.
4. Текущий живой стол работает **только потому**, что пароль поставлен руками от root в
   09:15 UTC. Следующее пересоздание контейнера это потеряет.
5. Схема `rustdesk://<id>?password=<pw>` применяет пароль **только при холодном старте**
   клиента. На уже запущенном приложении она лишь подставляет ID в поле.
6. Очередь задач: единственные не-успешные строки — 7 × `tracker_event_process`, все из
   одного пакета `2026-08-16 09:17:51`, `attempt_count 4/10080`,
   `last_error = absolute task deadline exceeded before external call`. Внешнего вызова не
   было, побочных эффектов нет.
7. `_TRACKER_ATTEMPT_DEADLINE = timedelta(seconds=120)` при `max_attempts=10080`. Гейт
   claim'а — `deadline_at > clock_timestamp()`, поэтому простой воркера дольше пары минут
   убивает постбек навсегда. Один заход при этом и так ограничен лизом (30 минут).
8. `observer_config`: `is_scanning_enabled = false`, `owner_campaign_tag = 'MV'`,
   `campaign_ids` — 4 штуки. `health_watchdog` поднят и ведёт инциденты в durable-план.

## Структура файлов

| Файл | Ответственность | Задача |
|---|---|---|
| `deploy/vision-webtop/entrypoint.sh` | Установка пароля канала от root с громким отказом | 1 |
| `tests/unit/test_vision_webtop.py` | Поведенческие тесты установки пароля | 1 |
| `apps/api/routers/v1/desktop.py` | Форма ссылки запуска | 2 |
| `tests/unit/test_desktop_native_api.py` | Контракт ссылки запуска | 2 |
| `frontend/src/routes/remote-desktop/index.tsx` | Честный текст шага 2 | 2 |
| `frontend-mini/src/routes/desktop/index.tsx` | То же для мини-аппа | 2 |
| `frontend/src/tests/pages/RemoteDesktop.test.tsx` | Регресс текста и поведения | 2 |
| `frontend-mini/src/tests/Desktop.test.tsx` | То же для мини-аппа | 2 |
| `core/adset_pro/ingest.py` | Срок жизни постбека | 3 |
| `tests/unit/test_adset_pro_ingest_dedup.py` | Регресс на переживание простоя | 3 |

---

### Task 1: Пароль канала стола устанавливается на самом деле

Блокирует всё остальное в столе. Пока пароль не наш, ни ссылка, ни ручной ввод не сработают.

**Files:**
- Modify: `deploy/vision-webtop/entrypoint.sh:192-193`
- Test: `tests/unit/test_vision_webtop.py`

**Interfaces:**
- Produces: функция `set_rustdesk_password` в области видимости блока канала. Использует уже
  существующие `${rustdesk_config_dir}`, `${config_home}`, `${rustdesk_password}`,
  `${requested_uid}`, `${requested_gid}`. Ничего не возвращает; код 1 = пароль не установлен.

- [ ] **Step 1: Написать падающие тесты**

Добавь в конец `tests/unit/test_vision_webtop.py`. Наверху файла к существующим импортам
добавь `import shlex` (`subprocess` и `Path` там уже есть).

```python
def _run_set_rustdesk_password(
    tmp_path: Path,
    *,
    rustdesk_output: str,
    rustdesk_exit: int,
    writes_config: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Исполняет функцию установки пароля прямо из entrypoint.

    Проверять наличие строк тут бессмысленно: болезнь была ровно в том, что
    вызов молча проглатывал отказ. Значение имеет поведение.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    start = entrypoint.index("  set_rustdesk_password() {")
    end = entrypoint.index("\n  }\n", start) + len("\n  }\n")
    function = entrypoint[start:end]

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    rustdesk_stub = stub_dir / "rustdesk"
    rustdesk_stub.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' {shlex.quote(rustdesk_output)}\n"
        f"exit {rustdesk_exit}\n",
        encoding="utf-8",
    )
    rustdesk_stub.chmod(0o755)
    # chown в тесте невозможен без root, а проверяем мы не его.
    chown_stub = stub_dir / "chown"
    chown_stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    chown_stub.chmod(0o755)
    # В образе стола `timeout` есть (проверено), а на macOS его нет — без
    # двойника тест падал бы с «command not found» на любом сценарии и
    # выглядел бы зелёным по неверной причине в одном из них.
    timeout_stub = stub_dir / "timeout"
    timeout_stub.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n', encoding="utf-8")
    timeout_stub.chmod(0o755)

    config_home = tmp_path / "config"
    config_dir = config_home / ".config" / "rustdesk"
    config_dir.mkdir(parents=True)
    if writes_config:
        (config_dir / "RustDesk.toml").write_text(
            "password = '00NDO1a/FOg8'\n", encoding="utf-8"
        )

    script = (
        "set -Eeuo pipefail\n"
        f'PATH={shlex.quote(str(stub_dir))}:"$PATH"\n'
        f"config_home={shlex.quote(str(config_home))}\n"
        f"rustdesk_config_dir={shlex.quote(str(config_dir))}\n"
        "rustdesk_password=ChannelPassword123456\n"
        "requested_uid=1000\n"
        "requested_gid=1000\n"
        f"{function}\n"
        "set_rustdesk_password\n"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_channel_password_refusal_is_loud_not_swallowed(tmp_path: Path) -> None:
    """Стол месяцами жил со СВОИМ случайным паролем.

    `rustdesk --password` от пользователя vision отвечает «Installation and
    administrative privileges required!» и выходит с НУЛЕВЫМ кодом. Вызов был
    прикрыт `|| true`, поэтому отказ не видел никто: оператор получал «Wrong
    password» на верном пароле, и это выглядело его ошибкой.
    """
    result = _run_set_rustdesk_password(
        tmp_path,
        rustdesk_output="Installation and administrative privileges required!",
        rustdesk_exit=0,
    )

    assert result.returncode != 0
    assert "Installation and administrative privileges required!" in result.stderr


def test_channel_password_accepted_on_confirmation(tmp_path: Path) -> None:
    result = _run_set_rustdesk_password(tmp_path, rustdesk_output="Done!", rustdesk_exit=0)

    assert result.returncode == 0, result.stderr


def test_channel_password_survives_a_client_that_hangs_after_writing(tmp_path: Path) -> None:
    """Клиент умеет записать пароль и остаться висеть: timeout прибьёт уже
    сделанную работу и вернёт 124. Судим по подтверждению, а не по коду."""
    result = _run_set_rustdesk_password(tmp_path, rustdesk_output="Done!", rustdesk_exit=124)

    assert result.returncode == 0, result.stderr


def test_channel_password_rejects_a_confirmation_without_a_file(tmp_path: Path) -> None:
    """«Done!» без ключа в файле — не установка, а её видимость."""
    result = _run_set_rustdesk_password(
        tmp_path, rustdesk_output="Done!", rustdesk_exit=0, writes_config=False
    )

    assert result.returncode != 0
    assert "RustDesk.toml остался без пароля канала" in result.stderr


def test_channel_password_is_set_by_root_not_by_the_desktop_user() -> None:
    """RustDesk считает постоянный пароль административным действием.

    Entrypoint и так работает от root — проверка проходит. Файлы после
    root-вызова возвращаем владельцу стола, иначе клиент под vision их не
    перечитает.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")
    body = entrypoint.split("set_rustdesk_password() {")[1].split("\n  }\n")[0]

    assert "rustdesk --password" in body
    assert "gosu" not in body
    assert 'chown -R "${requested_uid}:${requested_gid}" "${rustdesk_config_dir}"' in body


def test_channel_password_failure_stops_the_start() -> None:
    """Стол с чужим паролем — недостижимая машина, как и стол без ключа брокера.

    Отказ на старте не даёт ей стать такой молча; SSH при этом остаётся всегда.
    """
    entrypoint = (WEBTOP / "entrypoint.sh").read_text(encoding="utf-8")

    assert "if ! set_rustdesk_password; then" in entrypoint
    assert "Канал стола остался бы с чужим паролем" in entrypoint
    # Прежняя болезнь дословно.
    assert ">/dev/null 2>&1 || true" not in entrypoint
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_vision_webtop.py -q`
Expected: FAIL — `ValueError: substring not found` на `entrypoint.index("  set_rustdesk_password() {")`.

- [ ] **Step 3: Заменить проглатывающий вызов**

В `deploy/vision-webtop/entrypoint.sh` замени целиком:

```bash
  gosu "${runtime_user}" env "${rustdesk_env[@]}" rustdesk --password "${rustdesk_password}" \
    >/dev/null 2>&1 || true
```

на:

```bash
  # Постоянный пароль RustDesk считает административным действием: от
  # пользователя vision `rustdesk --password` отвечает «Installation and
  # administrative privileges required!» и выходит с НУЛЕВЫМ кодом. Вызов был
  # прикрыт `|| true`, поэтому отказ не видел никто — стол поднимал СВОЙ
  # случайный пароль, а оператор получал «Wrong password» на верном. Entrypoint
  # и так работает от root, поэтому проверка проходит; файлы после root-вызова
  # возвращаем владельцу стола.
  set_rustdesk_password() {
    local config_file="${rustdesk_config_dir}/RustDesk.toml"
    local output=""
    local status=0
    # Судим по подтверждению, а не по коду возврата: клиент умеет записать
    # пароль и остаться висеть, и timeout прибил бы уже сделанную работу.
    output="$(timeout 30 env HOME="${config_home}" \
      XDG_CONFIG_HOME="${config_home}/.config" \
      rustdesk --password "${rustdesk_password}" 2>&1)" || status=$?
    if [[ "${output}" != *'Done!'* ]]; then
      printf 'rustdesk --password не подтвердил установку (код %s): %s\n' \
        "${status}" "${output}" >&2
      return 1
    fi
    if [[ ! -s "${config_file}" ]] || ! grep -q '^password = ' "${config_file}"; then
      printf 'RustDesk.toml остался без пароля канала\n' >&2
      return 1
    fi
    chown -R "${requested_uid}:${requested_gid}" "${rustdesk_config_dir}"
  }

  if ! set_rustdesk_password; then
    printf 'Канал стола остался бы с чужим паролем — отказываюсь стартовать\n' >&2
    exit 1
  fi
```

Порядок важен: вызов остаётся **до** `rustdesk_supervisor`, иначе сервис успеет сгенерировать
свой пароль. Проверено на живом контейнере: от root с чистым `HOME` вызов работает и без
запущенного сервиса.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_vision_webtop.py -q`
Expected: PASS, все тесты файла зелёные.

- [ ] **Step 5: Проверить, что скрипт синтаксически валиден**

Run: `bash -n deploy/vision-webtop/entrypoint.sh`
Expected: пустой вывод, код 0.

- [ ] **Step 6: Коммит**

```bash
git add deploy/vision-webtop/entrypoint.sh tests/unit/test_vision_webtop.py
git commit -m "fix(desktop): пароль канала действительно ставится, отказ больше не молчит"
```

- [ ] **Step 7: Релиз и проверка на живом столе**

Только после «да» владельца на push. После завершения релизного workflow:

```bash
ssh root@62.60.150.133 'docker exec fb_agent_desktop-vision-webtop-1 sh -c "grep -c \"^password = \" /config/.config/rustdesk/RustDesk.toml"'
```
Expected: `1`.

```bash
ssh root@62.60.150.133 'docker logs fb_agent_desktop-vision-webtop-1 2>&1 | grep -i "password\|Канал стола" | head'
```
Expected: ни одной строки про `Installation and administrative privileges required!`.

Итоговая проверка — подключение владельца (см. блок «Что требуется от тебя», шаг 2).

---

### Task 2: Кнопка «Открыть в приложении» обещает ровно то, что делает

Зависит от Task 1: до него любая ссылка упирается в чужой пароль, и проверять нечего.

**Files:**
- Modify: `apps/api/routers/v1/desktop.py:168-172`
- Modify: `frontend/src/routes/remote-desktop/index.tsx:140-160`
- Modify: `frontend-mini/src/routes/desktop/index.tsx:116-134`
- Test: `tests/unit/test_desktop_native_api.py`, `frontend/src/tests/pages/RemoteDesktop.test.tsx`,
  `frontend-mini/src/tests/Desktop.test.tsx`

**Interfaces:**
- Consumes: `DesktopLaunchLinkResponse.url: str` — уже существует, поле одно.
- Produces: изменений в схеме ответа нет. Меняются форма URI и текст под кнопкой.

- [ ] **Step 1: Проверить каноничную форму URI на живом клиенте**

Гипотеза: наблюдавшееся «на запущенном приложении подставляется только ID» вызвано формой
`rustdesk://<id>?…`, а не принципиальным ограничением. У RustDesk есть форма
`rustdesk://connection/new/<id>`.

Команды выполняет агент на машине владельца; владелец только смотрит на окно RustDesk и
называет результат — «подключилось» или «спросило пароль» в выводе команды не видно.
Порядок обязателен, значения — настоящие ID и пароль канала:

```bash
pkill -x RustDesk; sleep 2; open "rustdesk://connection/new/<ID>?password=<PW>"
```
Запиши: открылась ли сессия без запроса пароля.

```bash
open "rustdesk://connection/new/<ID>?password=<PW>"
```
(приложение уже запущено после предыдущего шага) — запиши то же самое.

Перед вторым замером удали сохранённый пароль пира, иначе замер будет нечестным:

```bash
mv ~/Library/Preferences/com.carriez.RustDesk/peers/<ID>.toml /tmp/peer-backup.toml
```

и восстанови его после замеров:

```bash
mv /tmp/peer-backup.toml ~/Library/Preferences/com.carriez.RustDesk/peers/<ID>.toml
```

- [ ] **Step 2: Написать падающий тест формы ссылки**

**Ветка A** — каноничная форма понесла пароль на запущенном приложении.

В `tests/unit/test_desktop_native_api.py` в тесте
`test_launch_link_carries_the_password_so_the_client_asks_nothing` замени утверждение

```python
    assert payload.url == "rustdesk://253474910?password=s3cret-channel-pass"
```

на

```python
    assert payload.url == "rustdesk://connection/new/253474910?password=s3cret-channel-pass"
```

и допиши рядом отдельный тест:

```python
@pytest.mark.asyncio
async def test_launch_link_uses_the_form_a_running_client_honours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Короткую форму уже запущенный клиент разбирает наполовину.

    `rustdesk://<id>?password=<pw>` на работающем приложении подставляет ID и
    молча теряет пароль — проверено на живом клиенте. Пароль доезжал только на
    холодном старте, то есть кнопка работала через раз и по причине, которую
    оператор увидеть не мог.
    """

    async def fake_gate(_request, _engine, _settings):
        return None

    monkeypatch.setattr(m, "_resolve_owner_identity", fake_gate)

    payload = await m.get_native_launch_link(
        SimpleNamespace(headers={}),
        SimpleNamespace(headers={}),
        object(),
        _launch_settings(_published(tmp_path)),
    )

    assert payload.url.startswith("rustdesk://connection/new/")
```

В том же файле проверь `test_launch_link_percent_encodes_a_hostile_password`: если он
сравнивает URL целиком, префикс в нём тоже нужно поменять.

**Ветка B** — пароль теряется при любой форме. Форму URI не трогаем, Step 3 пропускается,
контрактных тестов не добавляем. Единственная правка ветки — честный текст (Step 4), и
дополнительно спроси владельца про буфер обмена (см. блок «Что требуется от тебя», пункт 3):
класть пароль канала в буфер по нажатию — расширение того исключения из инварианта секретов,
которое он уже разрешил для ссылки, и решать это ему.

- [ ] **Step 3: Поменять форму URI (только ветка A)**

В `apps/api/routers/v1/desktop.py` замени:

```python
    return DesktopLaunchLinkResponse(
        url=f"rustdesk://{quote(device_id, safe='')}?password={quote(password, safe='')}"
    )
```

на:

```python
    # Короткую форму `rustdesk://<id>?password=` уже запущенный клиент
    # разбирает наполовину: ID подставляет, пароль теряет. Каноничную форму
    # обработчик схемы принимает целиком — проверено на живом приложении.
    return DesktopLaunchLinkResponse(
        url=(
            f"rustdesk://connection/new/{quote(device_id, safe='')}"
            f"?password={quote(password, safe='')}"
        )
    )
```

- [ ] **Step 4: Привести текст под кнопкой к правде**

Сейчас в обоих фронтах написано: «Кнопка подставляет ID и пароль — вводить ничего не нужно.»
Это верно не всегда, и именно на этом обещании владелец и споткнулся.

В `frontend/src/routes/remote-desktop/index.tsx` замени текст абзаца под кнопкой на:

```tsx
                  <p className="mt-2 text-center text-[12px] leading-5 text-bg-8">
                    Кнопка открывает приложение с ID и паролем канала. Адрес и ключ схема
                    передать не может, поэтому до шага 1 приложение ответит «устройство не
                    найдено». Пароль клиент запомнит после первого удачного входа.
                  </p>
```

В `frontend-mini/src/routes/desktop/index.tsx` — тот же текст:

```tsx
                <p className="mt-2 text-[12px] leading-5 text-bg-8">
                  Кнопка открывает приложение с ID и паролем канала. Адрес и ключ схема
                  передать не может, поэтому до шага 1 приложение ответит «устройство не
                  найдено». Пароль клиент запомнит после первого удачного входа.
                </p>
```

- [ ] **Step 5: Обновить регресс-тесты обоих фронтов**

В `frontend/src/tests/pages/RemoteDesktop.test.tsx` и `frontend-mini/src/tests/Desktop.test.tsx`
существующая проверка `screen.getByText(/до шага 1 приложение ответит/)` продолжает работать.
Добавь в оба файла:

```tsx
  it("не обещает, что вводить не придётся никогда", () => {
    const { container } = render(<RemoteDesktopPage />);

    // Обещание «вводить ничего не нужно» верно не в каждом состоянии клиента,
    // и владелец споткнулся ровно об него.
    expect(container.textContent).not.toContain("вводить ничего не нужно");
    expect(screen.getByText(/запомнит после первого удачного входа/)).toBeInTheDocument();
  });
```

- [ ] **Step 6: Прогнать тесты**

```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_desktop_native_api.py -q
```
Expected: PASS.

```bash
pnpm --filter fb-stop-bot-frontend test && pnpm --filter fb-agent-mini test
```
Expected: PASS в обоих пакетах.

- [ ] **Step 7: Коммит**

```bash
git add apps/api/routers/v1/desktop.py tests/unit/test_desktop_native_api.py \
  frontend/src/routes/remote-desktop/index.tsx frontend-mini/src/routes/desktop/index.tsx \
  frontend/src/tests/pages/RemoteDesktop.test.tsx frontend-mini/src/tests/Desktop.test.tsx
git commit -m "fix(desktop): ссылка запуска и текст под кнопкой описывают реальное поведение"
```

---

### Task 3: Постбек трекера переживает перезапуск воркера

7 упавших задач — не сбой обработки, а срок жизни короче окна деплоя.

**Files:**
- Modify: `core/adset_pro/ingest.py:25,313`
- Test: `tests/unit/test_adset_pro_ingest.py`

**Interfaces:**
- Consumes: `create_task(..., deadline_at=...)` из `core/tasks/queue.py` — сигнатура не меняется.
- Produces: константа `_TRACKER_DELIVERY_DEADLINE: timedelta` вместо `_TRACKER_ATTEMPT_DEADLINE`.

- [ ] **Step 1: Написать падающий тест**

Файл `tests/unit/test_adset_pro_ingest.py` не существует — юнит-двойник движка живёт в
`tests/unit/test_adset_pro_ingest_dedup.py`, туда и добавляй. В его импортах есть
`from datetime import UTC, datetime`; допиши в тот же импорт `timedelta`.

Проверяем не константу, а то, что реально уезжает в `INSERT INTO task_queue`: тест на
константу пережил бы её переименование и ничего не поймал.

```python
@pytest.mark.asyncio
async def test_postback_outlives_a_deploy_window() -> None:
    """Постбек — это конверсия, а не запрос пользователя.

    Гейт claim'а очереди — `deadline_at > clock_timestamp()`, поэтому 120 секунд
    означали «переживи деплой или умри». 16.08 так умерли 7 конверсий одним
    пакетом, не дойдя до внешнего вызова. Длительность одного захода
    ограничивает лиз очереди (30 минут), а не этот срок.
    """
    received_at = datetime.now(UTC)
    engine = _Engine(
        [
            _Result(),  # advisory lock
            _Result(),  # exact one-shot dedupe lookup
            _Result([(11, received_at)]),  # event insert
            _Result([(received_at,)]),  # PostgreSQL scheduler clock
            _Result([(91,)]),  # durable task insert
            _Result(),  # transactional pg_notify wakeup hint
        ]
    )

    await ingest_postback(engine, _event(received_at=received_at))

    task_params = engine.conn.executed[4][1]
    assert task_params["tt"] == "tracker_event_process"
    assert task_params["deadline_at"] - received_at >= timedelta(hours=6)
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_adset_pro_ingest_dedup.py::test_postback_outlives_a_deploy_window -q`
Expected: FAIL — разница составляет 120 секунд вместо шести часов.

- [ ] **Step 3: Поднять срок жизни и переименовать константу**

В `core/adset_pro/ingest.py:25` замени:

```python
_TRACKER_ATTEMPT_DEADLINE = timedelta(seconds=120)
```

на:

```python
# Срок жизни постбека, а не одного захода: гейт claim'а отбрасывает задачу с
# истёкшим deadline_at навсегда, и 120 секунд означали «переживи деплой или
# умри». 16.08 так умерли 7 конверсий одним пакетом, не дойдя до внешнего
# вызова. Длительность одного захода ограничивает лиз очереди (30 минут).
_TRACKER_DELIVERY_DEADLINE = timedelta(hours=24)
```

В строке 313 замени `deadline_at=queue_now + _TRACKER_ATTEMPT_DEADLINE` на
`deadline_at=queue_now + _TRACKER_DELIVERY_DEADLINE`.

- [ ] **Step 4: Проверить, что старое имя нигде не осталось**

Run: `grep -rn "_TRACKER_ATTEMPT_DEADLINE" .`
Expected: пустой вывод.

- [ ] **Step 5: Прогнать тесты**

```bash
PYTHONDONTWRITEBYTECODE=1 pytest tests/unit/test_adset_pro_ingest_dedup.py tests/unit/test_adset_pro_processing.py -q
```
Expected: PASS. Интеграционный `tests/integration/test_adset_pro_ingest.py` тоже трогает
этот путь — прогнать только на изолированной БД, никогда на боевой.

- [ ] **Step 6: Коммит**

```bash
git add core/adset_pro/ingest.py tests/unit/test_adset_pro_ingest.py
git commit -m "fix(tracker): постбек переживает перезапуск воркера, а не умирает за две минуты"
```

- [ ] **Step 7: Убрать 7 мёртвых строк с прода**

Только после релиза Task 3. Побочных эффектов у них нет — все умерли до внешнего вызова.

```bash
ssh root@62.60.150.133 'docker exec fb_agent_infra-postgres-1 psql -U fb_stop_bot -d fb_stop_bot -c "DELETE FROM task_queue WHERE status = '"'"'failed'"'"' AND task_type = '"'"'tracker_event_process'"'"'"'
```
Expected: `DELETE 7`.

---

### Task 4: Включение сканирования

Money-путь. **Не начинать без явного «да» владельца в этом чате.** Кода не требует —
это операционный шаг, но с необратимыми последствиями: сканирование умеет останавливать рекламу.

- [ ] **Step 1: Сверить allowlist с намерением владельца**

```bash
ssh root@62.60.150.133 'docker exec fb_agent_infra-postgres-1 psql -U fb_stop_bot -d fb_stop_bot -c "SELECT campaign_ids, owner_campaign_tag, interval_seconds FROM observer_config"'
```
Expected: 4 кампании и тег `MV`. Показать список владельцу и получить подтверждение, что
это ровно те кампании, которые можно останавливать автоматически.

Пустой `campaign_ids` при включённом сканировании = не мониторится ничего, при этом всё
выглядит зелёным. Заполненность проверять до включения, а не после.

- [ ] **Step 2: Убедиться, что канал Vision жив**

```bash
ssh root@62.60.150.133 'docker ps --filter name=browser-agent --format "{{.Names}} {{.Status}}"'
```
Expected: `fb_agent_desktop-browser-agent-1 Up … (healthy)`.

- [ ] **Step 3: Включить сканирование тумблером в UI**

`app.adpulse.su` → Настройки → сканирование. Тумблером, не SQL: тумблер проходит через
`CommandService` и оставляет след в ленте действий.

- [ ] **Step 4: Смотреть первый цикл вживую**

```bash
ssh root@62.60.150.133 'docker logs --since 5m fb_agent_app-autopause_worker-1 2>&1 | tail -40'
```
Expected: строки `observer: режим=… период=…`, ни одного `degraded`.

- [ ] **Step 5: Убедиться, что ложных остановок не было**

```bash
ssh root@62.60.150.133 'docker exec fb_agent_infra-postgres-1 psql -U fb_stop_bot -d fb_stop_bot -c "SELECT task_type, status, count(*) FROM task_queue WHERE lane = '"'"'money'"'"' GROUP BY 1,2"'
```
Expected: пусто либо только успешные строки. Любая `failed` в money-полосе — стоп и разбор.

---

## Что требуется от тебя

Ниже — только то, что я не могу сделать сам. Остальное закрою без тебя.

### 1. Дать добро на push и релиз

Изменения Task 1-3 живут в коммитах локально. Прод обновляется только через релизный
workflow после push в `main`.

- Напиши «пушь» — и я отправлю коммиты и дождусь релиза.
- Или скажи, какие из трёх задач пускать, а какие подождут.

### 2. Проверить вход на стол после релиза Task 1

Это единственная проверка, которую нельзя сделать за тебя: подтвердить пароль может только
настоящее подключение.

1. Полностью закрой RustDesk: `⌘Q` в приложении (не просто окно).
2. Открой `app.adpulse.su` → «Рабочий стол».
3. Нажми «Открыть в приложении».
4. Скажи мне, что произошло, одной из формулировок:
   - «подключился сразу» — готово, задача закрыта;
   - «спросил пароль» — скажи, и я доведу Task 2 до конца;
   - «неверный пароль» — Task 1 не сработал, вернусь к нему.

### 3. Посмотреть на два замера для Task 2 (минута твоего внимания)

Замер выбирает ветку Task 2 вместо догадки. Команды выполню я сам — от тебя нужен только
взгляд на окно RustDesk, потому что «подключилось или спросило пароль» видно на экране, а не
в выводе команды. Делать **после** шага 2.

1. Скажи «давай замер» — я закрою RustDesk и открою ссылку (холодный старт).
2. Скажи, что показало окно.
3. Я открою ту же ссылку при уже запущенном приложении — скажи, что показало теперь.

**Отдельный вопрос, если замер покажет, что пароль теряется при любой форме:** класть ли
пароль канала в буфер обмена по нажатию кнопки, чтобы его можно было вставить одним движением.
Это шире того исключения из правила «секреты не попадают в UI», которое ты уже разрешил для
ссылки: буфер обмена читают и другие приложения. Без твоего «да» делать не буду — тогда
останется honest-вариант: первый вход на новом устройстве вводится руками, дальше клиент
помнит пароль сам.

### 4. Решение по сканированию (money)

Task 4 не начнётся без твоего явного «да». До того как сказать, посмотри на список из
4 кампаний — я покажу его по первой просьбе. Включение означает, что система получает право
останавливать рекламу в этих кампаниях без твоего участия.

Напиши «покажи кампании» — и я выведу список с названиями и текущими статусами.

### 5. Что решать не нужно

Не жду от тебя ничего по этим пунктам, называю их, чтобы ты знал, что они не забыты:

- Бэкапы PostgreSQL — ты отказался 15.08, не предлагаю заново.
- Алерт по токену Vision — закрыт: `health_watchdog` поднят и ведёт инциденты в durable-плане.
- Ретенция очереди, свёртка повторов в ленте, шум выключенного сканирования — смержено и в проде.
