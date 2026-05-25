# Prompt For External AI Review — 2026-05-25

Ниже готовый prompt для другой нейросети. Он ориентирован на **повторный review актуального состояния ветки `windows`** перед запуском Windows smoke run.

---

Ты делаешь строгий code review и runtime review Python-пайплайна для массового парсинга Vimeo, адаптированного под Windows.

Работай как senior reviewer:

- не переписывай код сразу;
- сначала ищи баги, регрессии, слабые места и недоказанные предположения;
- findings выдай первыми, по severity;
- для каждого finding дай точный путь к файлу и line reference;
- отдельно перечисли open questions;
- change summary давай только после findings;
- если не видишь проблемы, напиши это явно, но укажи residual risks;
- не концентрируйся на удалении credentials из репозитория.

## 1. Цель review

Нужно заново проверить кодовую базу перед следующим тестом:

- Windows smoke run
- `1 worker / 200 videos`

Нужно ответить на вопрос:

- готова ли текущая ветка `windows` к этому smoke run;
- нет ли регрессий после последних фиксов;
- не остались ли скрытые проблемы вокруг resume, controlled restart и coordinator/worker contract.

## 2. Локальный путь репозитория

Репозиторий находится здесь:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix`

Текущая ветка:

- `windows`

Это review именно **рабочего дерева**, а не только последнего коммита.

Перед чтением кода сначала проверь локальное состояние репозитория командами:

- `git -C /Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix branch --show-current`
- `git -C /Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix rev-parse --short HEAD`
- `git -C /Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix status --short`

Не опирайся на старый hash из предыдущих prompt-ов: он может уже не соответствовать текущему дереву.

## 3. Что уже было исправлено до этого review

В код уже внесены изменения, которые нужно перепроверить:

### 3.1 Worker-side fixes

Файл:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/download_vimeo_seleniumbase_v3.py`

Что уже добавлено/изменено:

- `should_recycle_after_fatal_exception(...)`
- controlled restart вместо `exit code 1` для части fatal runtime после partial progress
- `write_summary(...)` пишет summary атомарно через temp file + replace
- `total_processed` в summary
- запись watchdog summary перед `os._exit(75)`
- planned recycle больше не пишет misleading `fatal_error`
- `batches.max_controlled_restarts` поддерживается в config defaults

### 3.2 Coordinator-side fixes

Файл:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/run_assigned_shards.py`

Что уже добавлено/изменено:

- `load_batch_summary_excerpt(...)`
- `merge_batch_outputs_if_present(...)`
- coordinator вытаскивает из batch summary:
  - `summary_exit_code`
  - `fatal_error`
  - `restart_requested`
  - `restart_reason`
  - `resume_next_index`
- отдельный `controlled_restart_count` на уровне batch state
- лимит `batches.max_controlled_restarts`
- fallback чтения `total_processed` из `summary["total"]`
- partial `results_manifest.json` и `summary.json` теперь мёржатся в aggregate даже при terminal failure / restart-limit-exceeded, если файлы читаемы

### 3.3 Windows smoke configs

Ключевые файлы:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.1worker.200.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.4workers.500.json`

Что важно:

- smoke run: `max_batch_retries = 4`
- smoke run: `max_controlled_restarts = 6`
- 4-worker config: `max_controlled_restarts = 12`

## 4. Что именно нужно проверить

### A. Worker / coordinator contract

Проверь взаимодействие между:

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/download_vimeo_seleniumbase_v3.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/run_assigned_shards.py`

Нужно проверить:

- какие exit codes реально означают success / controlled restart / hard failure;
- корректно ли coordinator интерпретирует `summary.json` и `results_manifest.json`;
- нет ли рассинхрона между:
  - process exit code
  - `summary.exit_code`
  - `summary.restart_requested`
  - `summary.restart_reason`
  - `resume_state.next_index`
- нет ли риска, что batch будет wrongly failed при валидном resume path;
- нет ли риска бесконечного recycle loop несмотря на новый `max_controlled_restarts`.

### B. Resume semantics

Проверь:

- resume после controlled restart;
- resume после partial progress;
- корректность `resume_state.json`;
- корректность `results_manifest.json`;
- корректность `failed_downloads.json`;
- не ломаются ли exported URL buckets:
  - downloaded
  - skipped
  - failed
  - no_links
  - transcript_modal

### C. Windows-specific process handling

Проверь:

- `ActivityWatchdog`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/platform_runtime.py`
- `cleanup_process_tree_after_exit`
- `terminate_child_process_trees_for_restart`
- `worker_popen_kwargs`

Нужно понять:

- не осталось ли утечки `chrome.exe` / `chromedriver.exe`;
- достаточно ли надёжен stall path на Windows;
- не ухудшили ли свежие fixes cleanup behavior;
- не использует ли coordinator stale summary в каком-либо ещё path.

### D. Vimeo login / relogin path

Проверь:

- `runtime.vimeo_authenticated_session`
- `login_to_vimeo`
- `is_login_page`
- relogin внутри `download_video`

Нужно понять:

- нет ли race/loop после controlled restart;
- нет ли сценария, где resumed worker падает до первого нового URL;
- достаточны ли текущие timeout/retry settings для smoke run на 1 worker.

### E. API / page fallback

Проверь:

- `api_401_fallback_to_page`
- handling для 401 / 403 / 404 / 429
- worker API disable logic

Нужно понять:

- нет ли batch-kill path там, где должен быть per-video outcome;
- не осталось ли dead config keys;
- нет ли логических веток, которые на Windows дадут false failure.

### F. Smoke-run readiness

Оцени готовность именно этого запуска:

- config:
  - `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.1worker.200.json`
- manifest:
  - `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/manifest.json`
- batch:
  - `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/batch_0001.json`
- source list:
  - `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/windows_pc10_smoke_20260518_200/need_parse_unique.remaining_after_linux_progress_20260511.first200.json`

Нужно ответить:

1. Есть ли blockers перед smoke run.
2. Есть ли recommended config tweaks до первого запуска.
3. Что скорее всего сломается первым.
4. Достаточен ли runbook.

## 5. Какие файлы нужно прочитать обязательно

### Runtime / orchestration

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/download_vimeo_seleniumbase_v3.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/run_assigned_shards.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/platform_runtime.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/telegram_notifier.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/config_utils.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/result_exports.py`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/vimeo_cdp_helpers.py`

### Config / data / runbooks

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/config.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.4workers.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.4workers.500.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/configs/windows/config.windows.pc10.1worker.200.json`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/windows/WINDOWS_PC10_200_SMOKE_RUNBOOK.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/windows/WINDOWS_PC10_500_RUNBOOK.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/README.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/windows/WINDOWS_PORT_REVIEW.md`

### Additional docs

- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/analysis/CONNECTION_LEAK_ANALYSIS.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/analysis/CRITICAL_ISSUES_REPORT.md`
- `/Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/docs/architecture/VIDEO_JSON_STORAGE_LAYOUT.txt`

## 6. Что не нужно делать

- не уходи в общие советы без привязки к коду;
- не делай упор на hygiene credentials;
- не игнорируй Windows-specific behavior;
- не опирайся только на старый `docs/windows/WINDOWS_PORT_REVIEW.md` — смотри текущий код;
- не предполагай, что свежие fixes уже автоматически правильные: их надо заново доказать.

## 7. Требуемый формат ответа

Ответ должен быть в таком порядке:

1. **Findings**
   - только реальные проблемы или сомнительные места;
   - по severity;
   - с file/line references;
   - с объяснением последствий.

2. **Open Questions**
   - что нельзя доказать без запуска;
   - какие runtime-факты нужно снять на Windows-машине.

3. **Assessment Of Current Branch**
   - что в текущем состоянии выглядит правильно;
   - что осталось рискованным;
   - что ещё не доказано.

4. **Smoke Run Go/No-Go**
   - можно ли уже запускать `1 worker / 200 videos`;
   - если нет, какие правки нужны до запуска.

Если findings нет, напиши это явно, но всё равно перечисли residual risks.

## 8. Главная цель review

Главная цель не просто “прочитать код”, а ответить:

- готова ли ветка `windows` на commit `2854b40` к Windows smoke run на одном worker;
- не осталось ли ошибок в логике resume / restart / coordinator;
- и не спрятали ли последние fixes реальную проблему прошлых batch failures.
