# Prompt For External AI Review

Ниже готовый prompt для другой нейросети. Он ориентирован на review текущего рабочего дерева, а не только последнего commit.

---

Ты делаешь code review и runtime review Python-пайплайна для массового парсинга Vimeo, который сейчас адаптируется под Windows.

Работай как строгий senior reviewer:

- не переписывай код сразу;
- сначала найди баги, регрессии, слабые места и недоказанные предположения;
- findings выдай первыми, по severity;
- для каждого finding дай точный путь к файлу и line reference;
- отдельно перечисли open questions;
- change summary давай только после findings;
- если не видишь проблемы, напиши это явно, но укажи residual risks.

## 1. Что именно нужно проверить

Нужно заново проверить кодовую базу и текущее состояние Windows-ветки перед следующим тестовым прогоном:

- smoke run на Windows: `1 worker / 200 videos`;
- основной вопрос: почему на прошлой Windows-попытке batch'и после controlled restart переходили в `exit code 1` и coordinator помечал их как failed;
- проверить, корректен ли свежий локальный патч, который должен переводить часть таких сбоев в controlled restart вместо batch failure;
- проверить, нет ли новых рисков вокруг:
  - resume;
  - controlled restart;
  - worker/coordinator contract;
  - Windows process cleanup;
  - login/relogin path;
  - connection leaks / hanging browser processes;
  - URL result bucketing;
  - readiness для smoke run.

Важно: review должен быть именно по **текущему working tree**, а не только по HEAD commit.

## 2. Локальный путь репозитория

Репозиторий находится здесь:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix`

Текущая ветка:

- `windows`

HEAD commit на момент постановки задачи:

- `b160db1`

Но в working tree есть незакоммиченные изменения, и review нужно делать уже по ним.

## 3. Текущий статус рабочего дерева

На момент постановки задачи в `git status --short` было:

```text
 M download_vimeo_seleniumbase_v3.py
 M run_assigned_shards.py
?? docs/windows/WINDOWS_PC10_200_SMOKE_RUNBOOK.md
?? configs/windows/config.windows.pc10.1worker.200.json
?? data_shards/windows_pc10_smoke_20260518_200/
```

Ключевой незакоммиченный diff сейчас в двух файлах:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/download_vimeo_seleniumbase_v3.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/run_assigned_shards.py`

Review должен обязательно включать эти локальные правки.

## 4. Симптом последнего проблемного Windows-прогона

На Windows был запуск примерно такого вида:

```powershell
.\venv\Scripts\python.exe run_assigned_shards.py --config config.windows.pc10.first6.3workers.json --manifest data_shards\recovery_remaining_20260511_500\manifest.json --workers 3 --max-batches 6
```

Симптомы:

- worker'ы стартовали нормально;
- первые controlled restart завершались с `exit code 75`;
- coordinator корректно re-queue'ил batch;
- после этого те же batch'и начинали завершаться с `exit code 1`;
- при этом coordinator видел:
  - `summary=True`
  - `results=True`
- после исчерпания retries batch помечался как failed.

То есть проблема выглядела так:

1. worker уже успел записать `summary.json` и `results_manifest.json`;
2. но процесс всё равно возвращал ненулевой код;
3. coordinator трактовал это как batch failure.

Нужно проверить, действительно ли текущий локальный патч правильно адресует именно этот сценарий, и не вводит ли он новый класс ошибок.

## 5. Что уже было изменено локально

### 5.1 Изменение в worker

В файле:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/download_vimeo_seleniumbase_v3.py`

Добавлена логика:

- если происходит generic `Exception`;
- и при этом уже есть частичный progress по batch;
- и resume включён;
- то worker вместо `exit code 1` переводится в controlled restart (`75`);
- coordinator должен продолжить batch через `resume_state`.

Нужно оценить:

- корректен ли критерий `partial progress`;
- не маскирует ли это реальные crash'и;
- нет ли риска бесконечного recycle loop;
- не ломает ли это семантику `fatal_error`;
- достаточно ли этого для Windows.

### 5.2 Изменение в coordinator

В файле:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/run_assigned_shards.py`

Добавлена логика чтения из batch summary следующих полей:

- `summary_exit_code`
- `fatal_error`
- `restart_requested`
- `restart_reason`
- `resume_next_index`

И coordinator теперь пишет эти детали в log / Telegram path при retry/error/recycle.

Нужно оценить:

- не ломает ли это merge path;
- не может ли summary быть устаревшим или не соответствовать текущему exit path;
- достаточно ли этого для диагностики;
- нет ли побочных эффектов.

## 6. Файлы, которые нужно прочитать обязательно

### Основные runtime-файлы

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/run_assigned_shards.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/download_vimeo_seleniumbase_v3.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/platform_runtime.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/telegram_notifier.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/config_utils.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/result_exports.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/vimeo_cdp_helpers.py`

### Конфиги и smoke-артефакты

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/config.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.4workers.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.4workers.500.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.1worker.200.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/manifest.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/batch_0001.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/need_parse_unique.remaining_after_linux_progress_20260511.first200.json`

### Документация и предыдущий review

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/README.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/windows/WINDOWS_PORT_REVIEW.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/windows/WINDOWS_PC10_200_SMOKE_RUNBOOK.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/windows/WINDOWS_PC10_500_RUNBOOK.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/analysis/CONNECTION_LEAK_ANALYSIS.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/analysis/CRITICAL_ISSUES_REPORT.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/architecture/VIDEO_JSON_STORAGE_LAYOUT.txt`

## 7. Что именно нужно проверить по существу

### A. Worker/coordinator contract

Проверь, корректен ли контракт между:

- `download_vimeo_seleniumbase_v3.py`
- `run_assigned_shards.py`

Особенно:

- какие exit codes означают:
  - success;
  - controlled restart;
  - recoverable failure;
  - fatal batch failure;
- когда наличие `summary.json` и `results_manifest.json` должно считаться достаточным для requeue / success;
- не возникает ли рассинхрон между:
  - `process exit code`
  - `summary.exit_code`
  - `summary.restart_requested`
  - `resume_state.next_index`

### B. Resume semantics

Проверь, не ломает ли новый локальный патч:

- resume после partial progress;
- корректность `failed_downloads.json`;
- корректность `results_manifest.json`;
- корректность итоговых bucket'ов:
  - downloaded
  - skipped
  - failed
  - no_links
  - transcript_modal

### C. Windows-specific process handling

Проверь:

- `ActivityWatchdog`
- `platform_runtime.py`
- `cleanup_process_tree_after_exit`
- `terminate_child_process_trees_for_restart`
- `worker_popen_kwargs`

Нужно понять:

- нет ли утечки Chrome/chromedriver процессов после controlled restart и stall path;
- не осталась ли проблема, уже описанная в `docs/windows/WINDOWS_PORT_REVIEW.md`;
- не ухудшает ли новый патч cleanup semantics;
- насколько надёжен текущий Windows path при серии controlled restart'ов.

### D. Vimeo login / relogin path

Проверь:

- `runtime.vimeo_authenticated_session`
- `login_to_vimeo`
- `is_login_page`
- relogin внутри `download_video`

Нужно оценить:

- нет ли race/loop после fresh worker restart;
- нет ли ошибок, которые легко приводят к `exit code 1` уже после resume;
- достаточны ли текущие таймауты и retry settings для smoke run на 1 worker.

### E. API/page fallback

Проверь:

- `api_401_fallback_to_page`
- path для 401 / 403 / 404 / 429
- worker API disable logic

Нужно понять:

- правильно ли сейчас реализован fallback;
- нет ли batch-kill path там, где должен быть per-video failure;
- не осталось ли dead config keys.

### F. Windows smoke readiness

Проверь готовность именно следующего теста:

- `1 worker`
- `200 videos`
- config: `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.1worker.200.json`
- manifest: `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/manifest.json`

Нужно ответить:

1. Есть ли blockers перед этим smoke run.
2. Есть ли recommended config tweaks перед первым запуском.
3. Что наиболее вероятно сломается первым.
4. Достаточно ли текущего runbook.

## 8. Что не нужно делать

- не предлагай удалять credentials из репозитория;
- не концентрируй review на security hygiene credentials;
- не уходи в общие советы уровня “надо больше логов” без привязки к конкретным местам;
- не ограничивайся уже существующим `docs/windows/WINDOWS_PORT_REVIEW.md` — проверь, что изменилось после него;
- не делай вид, что проблема уже решена, если она только замаскирована.

## 9. Ожидаемый формат ответа

Ответ должен быть в таком порядке:

1. **Findings**
   - только реальные проблемы или сомнительные места;
   - по severity;
   - с точными file/line references;
   - с объяснением последствий.

2. **Open Questions**
   - что нельзя доказать без запуска;
   - какие runtime-факты нужно снять на Windows-машине.

3. **Assessment Of The New Local Patch**
   - что в нём правильно;
   - что рискованно;
   - что осталось непокрытым.

4. **Smoke Run Go/No-Go**
   - можно ли уже запускать `1 worker / 200 videos`;
   - если нет, какие правки нужны до запуска.

Если findings нет, напиши это явно, но всё равно перечисли residual risks.

## 10. Дополнительно: что полезно посмотреть в diff

Если у тебя есть доступ к git diff, обязательно проверь именно рабочее дерево по этим файлам:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/download_vimeo_seleniumbase_v3.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/run_assigned_shards.py`

И отдельно оцени новые Windows smoke-файлы:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.1worker.200.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/windows/WINDOWS_PC10_200_SMOKE_RUNBOOK.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/`

Главная цель review:

- не просто “прочитать код”,
- а понять, действительно ли текущая ветка готова к Windows smoke run на одном worker,
- и не спрятали ли последние правки реальную причину прежних batch failure.

