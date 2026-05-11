# Windows Port Review — branch `windows`, commit `495656f`

_Дата review: 2026-05-11_

---

## БЛОК 1: Findings по severity

---

### CRITICAL-1 — Watchdog stall recovery завершает процесс с неправильным exit code

**Severity:** CRITICAL  
**Файлы:** `platform_runtime.py:295-312`, `download_vimeo_seleniumbase_v3.py:415-419`

**Почему это баг:**

В `ActivityWatchdog.run` при срабатывании stall exit:

```python
# download_vimeo_seleniumbase_v3.py:415-419
terminate_current_process_tree_for_restart(
    logger=self.logger,
    label=f"worker-stall-{snapshot['worker_name'] or 'unknown'}",
)
os._exit(CONTROLLED_RESTART_EXIT_CODE)  # ← НИКОГДА не достигается
```

**На Windows:** `_taskkill_process_tree(pid, force=True)` вызывает `taskkill /F /T /PID <self>`. Windows немедленно уничтожает процесс через `TerminateProcess`. `subprocess.run` не возвращается. `os._exit(75)` недостижим.

**На Linux:** `os.killpg(os.getpid(), signal.SIGKILL)` посылает SIGKILL всей process group, включая текущий поток. SIGKILL нельзя поймать, процесс гибнет немедленно. `os._exit(75)` недостижим.

**Как проявляется:**

Координатор видит exit code не 75 (Windows: системный код через TerminateProcess; Linux: −9 / SIGKILL). Логика:

```python
# run_assigned_shards.py:876
if return_code == CONTROLLED_RESTART_EXIT_CODE:  # False!
    state_item["status"] = "pending"  # ← не выполняется
    ...
else:  # → обрабатывается как failure
    failure_action = handle_batch_failure(...)
```

Итог: stall recovery засчитывается как `failed`, не как controlled restart. Batch инкрементирует `retry_count`. После 2 retries batch помечается как permanently failed и выпадает из очереди.

**Как исправить:**

Шаг 1 — убрать `terminate_current_process_tree_for_restart` из watchdog, чтобы exit code 75 дожил до координатора:

```python
# download_vimeo_seleniumbase_v3.py — ActivityWatchdog.run
# БЫЛО:
terminate_current_process_tree_for_restart(
    logger=self.logger,
    label=f"worker-stall-{snapshot['worker_name'] or 'unknown'}",
)
os._exit(CONTROLLED_RESTART_EXIT_CODE)

# СТАЛО:
os._exit(CONTROLLED_RESTART_EXIT_CODE)
```

Шаг 2 — это необходимое, но не достаточное исправление. После этого фикса координатор будет корректно видеть exit code 75, но `cleanup_process_tree_after_exit` вызывается уже после завершения процесса, и её эффективность на Windows зависит от того, успел ли Chrome осиротеть (см. CRITICAL-2 ниже). Перед тем как принять этот патч, нужно убедиться, что стратегия очистки дочерних процессов на Windows достаточна — или явно задокументировать ограничение и компенсировать его в runbook.

---

### CRITICAL-2 — Windows child-process cleanup strategy needs redesign after fixing watchdog exit semantics

**Severity:** HIGH (design concern, а не доказанный текущий баг)  
**Файл:** `platform_runtime.py:262-292`

**Суть проблемы:**

Сейчас watchdog path в `terminate_current_process_tree_for_restart` (platform_runtime.py:295) пытается убить дерево процессов заранее, до `os._exit`. Это ломает exit code (CRITICAL-1), зато Chrome потенциально успевает умереть вместе с деревом.

После фикса CRITICAL-1 (убрать `terminate_current_process_tree_for_restart`, оставить только `os._exit(75)`), coordinator будет видеть правильный exit code, но cleanup дочерних процессов перекладывается на `cleanup_process_tree_after_exit`, которая вызывается уже после смерти worker-а:

```python
# run_assigned_shards.py:869
cleanup_process_tree_after_exit(process, logger, slot_name)
```

```python
# platform_runtime.py:266-267
if is_windows():
    _taskkill_process_tree(int(process.pid), force=True, ...)  # PID уже мёртвого процесса
```

Taskkill `/T` строит дерево по цепочке parent-child. Насколько надёжно это работает на Windows после гибели родительского процесса — зависит от времени: если Chrome успел стать orphan до момента вызова taskkill, он может не попасть в дерево. Точное поведение зависит от версии Windows и скорости reparenting.

**На Linux** это не проблема: process group существует пока жив хотя бы один её член (Chrome), поэтому `os.killpg` по process group ID всегда достигает Chrome независимо от статуса родителя.

**Что это означает практически:**

Это не «гарантированная утечка сейчас», а «слабое место, которое раскрывается после исправления CRITICAL-1». Перед тем как принять патч CRITICAL-1, нужно принять одно из решений:

1. **Принять ограничение и компенсировать в runbook** — добавить инструкцию по ручной очистке Chrome после аномальных завершений (`taskkill /F /IM chrome.exe`)
2. **Делать cleanup до `os._exit`** — перед `os._exit(75)` явно вызвать `terminate_process_tree` для Chrome PID (требует сохранения PID из `sb.driver.service.process.pid` при старте)
3. **Улучшить `cleanup_process_tree_after_exit` на Windows** — добавить fallback через `taskkill /F /IM chrome.exe` если дерево не найдено по PID

---

### HIGH-1 — `stagger_start_seconds` объявлен в конфиге, но не применяется: 4 worker-а логинятся одновременно

**Severity:** HIGH  
**Файлы:** `run_assigned_shards.py:763-839`, `download_vimeo_seleniumbase_v3.py:187`

**Почему это баг:**

`stagger_start_seconds: 20` в config нигде не читается в `run_assigned_shards.py`. Все 4 worker-а стартуют в одном цикле без задержки:

```python
# run_assigned_shards.py:765-839 — нет sleep между worker starters
for slot_index in range(1, worker_count + 1):
    ...
    process = subprocess.Popen(...)  # worker 1, 2, 3, 4 — без пауз
```

При `runtime.vimeo_authenticated_session=true` каждый worker сразу после запуска Chrome идёт в `login_to_vimeo`. Четыре браузера одновременно открывают `https://vimeo.com/log_in` под одними credentials.

**Последствия:**
- Vimeo может сработать rate limit на login endpoint
- Concurrent session конфликты
- Vimeo может показать CAPTCHA или заблокировать IP при подозрении на bot

**Как исправить:**

```python
# run_assigned_shards.py — после записи в active_processes, перед следующей итерацией:
stagger = max(0, int(master_config.get("workers", {}).get("stagger_start_seconds", 0) or 0))
if stagger > 0 and slot_index < worker_count:
    logger.info("Staggering worker start: sleeping %ds before next slot", stagger)
    time.sleep(stagger)
```

---

### HIGH-2 — `api_401_fallback_to_page: true` в конфиге — мёртвый ключ, 401 всегда останавливает batch

**Severity:** HIGH  
**Файлы:** `download_vimeo_seleniumbase_v3.py:2927-2952`, `config.windows.4workers.json:46`

**Почему это баг:**

```json
// config.windows.4workers.json
"api_401_fallback_to_page": true
```

```python
# download_vimeo_seleniumbase_v3.py:2927
if response.status_code == 401:
    fatal_error = "API Authentication error (401) - invalid token"
    # api_401_fallback_to_page нигде не читается
    exit_code = 2
    break  # ← batch умирает безусловно
```

`api_401_fallback_to_page` нигде не используется в `.py` файлах (grep подтверждает).

**Воспроизводится:** Если один из 14 API токенов в `api_pool` истёк или был отозван. Worker получает 401 на первом же видео и роняет batch (exit_code=2). Координатор делает 2 retry, оба падают → batch permanently failed.

**Замечание:** При `stop_on_batch_error=false` остальные 3 worker-а продолжают работу. Но один плохой токен делает весь соответствующий batch невосстанавливаемым.

**Вариант A — убрать мёртвый ключ из конфига:**

```json
// config.windows.4workers.json — убрать строку:
// "api_401_fallback_to_page": true,
```

**Вариант B — реализовать:**

```python
# download_vimeo_seleniumbase_v3.py:2927
if response.status_code == 401:
    if bool(config["settings"].get("api_401_fallback_to_page", False)):
        logger.warning(
            "API returned 401 for %s; falling back to page probe (api_401_fallback_to_page=true)",
            video_id,
        )
        json_data = {}
        # не break — продолжаем к download_video с пустым json_data
    else:
        fatal_error = "API Authentication error (401) - invalid token"
        logger.error(fatal_error)
        # ... (существующий код без изменений)
        exit_code = 2
        break
```

---

### MEDIUM-1 — Несколько batch-ей завершаются в одном polling цикле при stop_requested — результаты могут теряться

**Severity:** MEDIUM  
**Файл:** `run_assigned_shards.py:1009-1029`

**Почему это баг:**

Если batch B и batch C оба завершились в одном 5-секундном цикле, и batch B первым обработан с ошибкой, triggering `stop_requested`:

```python
for slot_name, item in list(active_processes.items()):
    return_code = process.poll()
    if return_code is None:
        continue

    # обработка завершения batch B → failure → stop_requested = True
    del active_processes[slot_name]

    if stop_requested:
        for other_slot_name, other_item in list(active_processes.items()):
            terminate_process_tree(...)   # batch C "убивается" хотя уже завершился нормально
            del active_processes[other_slot_name]
        break
```

Batch C числится в `active_processes` как active → будет "убит" → результаты не смёрджены в `global_results` → batch C re-queued как pending → переобработан.

**Resume consistency:** Не нарушается — batch C будет переобработан с нуля (resume state на уровне видео сохранён). Данные не теряются, но появляется лишняя работа.

**Примечание:** В Windows-профиле `stop_on_batch_error=false`, поэтому этот сценарий вообще не срабатывает в штатном режиме. MEDIUM-1 актуален только если `stop_on_batch_error` будет включён вручную.

---

### MEDIUM-2 — `call_with_stage_timeout` no-op на Windows: browser-зависание до `stall_exit_after_seconds`

**Severity:** MEDIUM (операционный риск)  
**Файл:** `download_vimeo_seleniumbase_v3.py:430-439`

**Почему риск:**

```python
def call_with_stage_timeout(timeout_seconds, description, func, *args, **kwargs):
    if (
        ...
        or not hasattr(signal, "SIGALRM")   # Windows: нет SIGALRM
        or not hasattr(signal, "setitimer")  # Windows: нет setitimer
        or is_windows()                      # явная проверка
    ):
        return func(*args, **kwargs)  # ← timeout вообще не применяется
```

Вызовы `sb.open(video_url)` на Windows не имеют hard timeout кроме Selenium page load timeout (120 секунд). Если страница зависла на CDP/javascript уровне — Selenium timeout может не срабатывать.

Watchdog поймает stall только через `stall_exit_after_seconds=3600`. Один hung video page может заморозить worker на до 1 часа.

**Рекомендация:** Уменьшить `watchdog.stall_exit_after_seconds` до 600-900 для Windows.

```json
// config.windows.4workers.json
"watchdog": {
    "enabled": true,
    "stall_alert_after_seconds": 600,
    "repeat_alert_every_seconds": 600,
    "heartbeat_every_seconds": 900,
    "exit_on_stall": true,
    "stall_exit_after_seconds": 900
}
```

---

### LOW-1 — Preflight `check_processes` использует `pgrep` — отсутствует на Windows

**Severity:** LOW  
**Файл:** `scripts/preflight_parse.py:624-637`

```python
def check_processes(reporter):
    pgrep_binary = shutil.which("pgrep")
    if not pgrep_binary:
        reporter.warn("pgrep", "not found")  # ← на Windows всегда WARN
        return
```

Preflight на Windows выдаст `WARN pgrep: not found` и не проверит наличие запущенных worker-ов. Не блокер, но неинформативно.

**Как улучшить:**

```python
def check_processes(reporter):
    if is_windows():
        rc, stdout, _ = run_command(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"], timeout=10
        )
        # парсить stdout на предмет download_vimeo_seleniumbase_v3.py
        ...
        return
    # существующий pgrep код
```

---

### LOW-2 — `suppress(Exception)` в vimeo_cdp_helpers может скрывать реальные Selenium errors

**Severity:** LOW  
**Файл:** `vimeo_cdp_helpers.py:88-132`

```python
with suppress(Exception):
    rows = scope_element.query_selector_all("[id^='download-file']")
```

Если Selenium выбрасывает `WebDriverException` с сетевой ошибкой (например, Chrome CDP соединение разорвано), это будет проглочено. Видео попадёт в `no_links` вместо `failed`, без сигнала для диагностики.

---

## БЛОК 2: Connection leak / socket pressure / browser leak risks

### HTTP Sessions — нет утечек

Все три сессии имеют строгий lifecycle:
- `api_session` — создаётся в `main()`, закрывается в `finally` (строка 3618)
- `download_session` — аналогично (строка 3619)
- `temporary_download_session` в `download_file()` — создаётся и закрывается в `finally` внутри функции (строка 1323)
- Telegram `_session` — закрывается в `shutdown()`, который вызывается до `os._exit` в watchdog (строки 413-414) и в `finally` блоке main

Все `HTTPAdapter` используют `pool_block=True` — bounded connection pool. Concurrent workers — separate processes, shared state отсутствует.

### Chrome / SeleniumBase

**Нормальный controlled restart path:** `ControlledWorkerRestart` exception всплывает через `with SB(...) as sb:` → `SB.__exit__` вызывается → Chrome закрывается чисто. Leak отсутствует.

**Stall watchdog / force-kill path:** Chrome НЕ закрывается через `SB.__exit__`. Leak есть (CRITICAL-2). При controlled restart после 100 URL — Chrome закрывается нормально. При stall watchdog — leak.

**CDP/iframe frame switching:** `driver.switch_to.default_content()` всегда вызывается в `finally` блоках в `vimeo_cdp_helpers.py`. Frame context корректно сбрасывается после каждой операции. Нет риска "застрять" в frame контексте.

### TCP / Socket pressure на Windows

Socket pressure gate корректно отключён: все лимиты = 0, `load_socket_usage` возвращает `None` на не-Linux. `wait_for_socket_budget_before_network: false`. Нет попыток читать `/proc/net/sockstat`. Чисто.

---

## БЛОК 3: Windows-specific blockers

### Уже устранены (верно)

| Что | Как проверено |
|-----|--------------|
| SIGALRM / setitimer | `call_with_stage_timeout` явно проверяет `is_windows()` и отключается |
| `start_new_session` | `worker_popen_kwargs()` → `CREATE_NEW_PROCESS_GROUP` |
| xvfb | `supports_xvfb()` → False; `browser.xvfb=false` в config |
| Interface-bound downloads | `download_interface=""` в Windows config; `supports_interface_bound_downloads()` → False |
| `/proc/net/sockstat` | `load_socket_usage()` → `None` если `not is_linux()` |
| Linux-only signals | Нет использования SIGUSR1, SIGHUP и т.д. |
| `ip` / `tmux` в preflight | `check_tools` вызывает их только при `is_linux()` |
| `telegram.source_address` | Пустая строка в Windows config |

### Реальные Windows blockers в runtime path

1. **Watchdog stall exit code** (CRITICAL-1) — работает, но exit code неправильный
2. **Chrome orphan cleanup** (CRITICAL-2) — taskkill на мёртвый PID не достигает Chrome
3. **Нет stagger** (HIGH-1) — 4 concurrent login под одним аккаунтом
4. **`api_401_fallback_to_page` мёртвый ключ** (HIGH-2) — 401 роняет batch безусловно

### Нет blockers в нормальном пути

При работе без stall watchdog и без аварийных завершений — полный runtime path работает на Windows. `CREATE_NEW_PROCESS_GROUP` правильно изолирует worker-ов. Taskkill корректно используется в `terminate_process_tree`.

---

## БЛОК 4: Что сделано правильно — не трогать

### Config inheritance через base_config

`config.windows.4workers.json` → `base_config: config.json` через `load_json_with_base_config` с cycle detection. Deep merge корректен. Windows overrides точечны. Credentials из `config.json` доступны всем workers. **Не трогать.**

### API pool credential distribution

```python
# run_assigned_shards.py:562-579
if worker_index <= len(api_pool):
    api_creds = api_pool[worker_index - 1] or {}
    if all(api_creds.get(key) for key in ("client_id", "client_secret", "token")):
        batch_config["vimeo_api"]["client_id"] = api_creds["client_id"]
        ...
```

Worker-1 → pool[0], Worker-2 → pool[1], ..., Worker-4 → pool[3]. Индексация правильная. При restart batch получает тот же slot_index → тот же токен. Для 4 workers из 14 pool entries — всем назначаются уникальные токены. **Не трогать.**

### Controlled restart семантика (нормальный путь)

`maybe_raise_controlled_restart` → `ControlledWorkerRestart` → exception через `with SB() as sb` (Chrome закрывается) → `except ControlledWorkerRestart:` → exit code 75 → coordinator re-queues без инкремента `retry_count` → resume state корректен. Sequence правильный. **Не трогать.**

### Resume state и result manifest — атомарная запись

```python
temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
with open(temp_path, "w", ...) as f:
    json.dump(payload, f, ...)
os.replace(temp_path, state_path)  # atomic on Windows (NTFS)
```

`os.replace` атомарен на Windows. Нет риска corrupt state file при crash посреди записи. **Не трогать.**

### Re-login detection в `download_video`

Проверка `is_login_page()` после открытия каждой страницы при `vimeo_authenticated_session=true`. Если сессия expired mid-run — автоматический повторный логин и повторное открытие страницы. **Не трогать.**

### `stop_on_batch_error=false` + `max_batch_retries=2` в Windows конфиге

Если один worker роняет batch, остальные 3 продолжают. 2 retry для восстановления. Правильная политика для Windows. **Не трогать.**

### Socket pressure gate на Windows

`socket_pressure_gate_enabled=true` но все лимиты = 0 → gate всегда открыт. `load_socket_usage()` → `None` → `evaluate_socket_pressure` → `None`. Нет spurious delays или ложных блокировок. **Не трогать.**

### Result buckets classification

| Bucket | Логика | Статус |
|--------|--------|--------|
| `transcript_modal` | Строковый матч `"Transcript download modal..."` через `item.get("reason")` — строка правильно пишется в manifest и читается в `result_exports.py` | Корректно |
| `downloaded_original` | При `download_only_original=true` все downloaded = original | Корректно |
| `not_downloaded_downloadable` | `downloadable=True` или `download_link` в manifest item | Корректно |
| `no_links` | Fallback: 404, privacy, no button | Корректно |

**Не трогать.**

### Download-only-original policy path

Корректно обрабатывает случай когда API вернул non-original, page probe не нашёл original — видео идёт в `not_downloaded_downloadable` с policy reason. Не считается failed, не ломает resume. **Не трогать.**

### Telegram session lifecycle

`TelegramNotifier.shutdown(timeout=5)` вызывается до `os._exit` в watchdog и в `finally` main. `atexit.register(self.shutdown)` как fallback. Daemon thread гарантирует что sender не заблокирует shutdown. **Не трогать.**

---

## БЛОК 5: Итоговая таблица и приоритеты

| # | Severity | Тема | Файл | Fix |
|---|----------|------|------|-----|
| 1 | **CRITICAL** | Watchdog exit code всегда не 75 | `download_vimeo_seleniumbase_v3.py:415` | Убрать 2 строки + решить C-2 |
| 2 | **HIGH** | Windows child-process cleanup — design concern после фикса C-1 | `platform_runtime.py:262` | Решение выбирается отдельно |
| 3 | **HIGH** | 4 concurrent Vimeo logins (нет stagger) | `run_assigned_shards.py:816` | 5 строк кода |
| 4 | **HIGH** | `api_401_fallback_to_page` мёртвый ключ | `download_vimeo_seleniumbase_v3.py:2927` | Убрать из конфига или реализовать |
| 5 | **MEDIUM** | stop_requested теряет last-cycle results | `run_assigned_shards.py:1011` | Неактуально при `stop_on_batch_error=false` |
| 6 | **MEDIUM** | `call_with_stage_timeout` no-op на Windows | `download_vimeo_seleniumbase_v3.py:430` | Config fix (stall_exit_after) |
| 7 | **LOW** | pgrep в preflight не работает на Windows | `scripts/preflight_parse.py:624` | Мелкий |
| 8 | **LOW** | `suppress(Exception)` маскирует CDP errors | `vimeo_cdp_helpers.py` | Риск, не блокер |

### Обязательно перед production run на Windows

1. **CRITICAL-1** — убрать `terminate_current_process_tree_for_restart` из watchdog, оставить только `os._exit(75)` — но принять это как patch только в связке с решением по **HIGH-2 (child cleanup)**
2. **HIGH-2 (child cleanup)** — выбрать стратегию: runbook + ручная очистка, или явный Chrome kill до `os._exit`, или fallback в `cleanup_process_tree_after_exit`
3. **HIGH-1** — добавить stagger между worker starters в координатор
4. **HIGH-2 (api_401)** — убрать `api_401_fallback_to_page` из `config.windows.4workers.json` или реализовать логику в коде
5. **MEDIUM-2** — уменьшить `watchdog.stall_exit_after_seconds` до 900 в `config.windows.4workers.json`
