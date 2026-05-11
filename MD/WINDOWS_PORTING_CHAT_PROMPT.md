# Windows Porting Chat Prompt

Ниже готовый подробный handoff-текст для нового чата, в котором нужно будет переносить этот код на Windows.

Скопируй текст ниже целиком в новый чат.

---

Мне нужно перенести репозиторий `codex_vimeo_fix` с Linux-oriented production runtime на Windows, при этом нельзя потерять понимание текущей архитектуры и логики пайплайна.

## Контекст репозитория

Это пайплайн для массового парсинга Vimeo URL:

1. есть master URL list `need_parse_unique.json`;
2. он режется на server assignments (`parse-1`, `parse-2`, `parse-3`) через `scripts/create_server_assignments.py`;
3. coordinator `run_assigned_shards.py` запускает worker'ы по manifest'у shard batch'ей;
4. worker `download_vimeo_seleniumbase_v3.py` открывает Vimeo через SeleniumBase/Chrome, ищет downloadable media, скачивает файл, пишет metadata JSON, сохраняет resume state;
5. offloader `scripts/offload_downloads.py` переносит completed video+json на storage;
6. `result_exports.py` строит URL-списки по результатам:
   - downloaded_original_urls.txt
   - not_downloaded_downloadable_urls.txt
   - no_links_urls.txt
   - transcript_modal_urls.txt

## Главные файлы

- `README.md`
- `run_assigned_shards.py`
- `download_vimeo_seleniumbase_v3.py`
- `result_exports.py`
- `scripts/preflight_parse.py`
- `scripts/offload_downloads.py`
- `scripts/create_server_assignments.py`
- `config.parse-3.profile.json`
- `VIDEO_JSON_STORAGE_LAYOUT.txt`
- `PARSE3_RUNTIME_NETWORK_REFERENCE.md`

## Что уже известно про текущую логику

### Resume

Resume уже есть и его нельзя сломать:

- coordinator state: `workers/assignment_state.json`
- per-batch worker state: `shards/batch_XXXX/resume_state.json`
- results manifests и скачанные файлы используются для продолжения

То есть после reboot/power loss этот пайплайн обычно можно продолжать тем же запуском coordinator.

### URL result buckets

Важно не перепутать terminal и retryable buckets:

- `downloaded_original` = terminal success
- `no_links` = terminal no-usable-link
- `transcript_modal` = terminal/special-case
- `not_downloaded_downloadable` = retryable, не удалять автоматически

### Проблема с количеством соединений

Остается production-проблема с большим количеством сетевых соединений. В коде уже есть:

- socket pressure gate;
- thresholds:
  - `max_tcp_inuse_to_start_worker`
  - `max_tcp_timewait_to_start_worker`
  - `max_tcp_orphan_to_start_worker`
- worker controlled restart after N processed items;
- restart on repeated timeout sequences;
- metrics tooling.

Но проблема не закрыта полностью: особенно опасны browser/page connections и hanging download processes.

## Linux-specific вещи, которые нужно убрать или абстрагировать для Windows

Текущий код и operational tooling завязаны на Linux:

- `/proc/net/sockstat` и `/proc/net/sockstat6`
- `curl --interface ...`
- `ip`, `ip route`
- `wg-quick`, WireGuard routing
- `systemd`, `resolvectl`
- `tmux`
- `xvfb-run`
- mounted storage paths вида `/29d_kon/...`
- shell scripts `.sh`
- Chrome path / Linux process names

Нужно не просто "запустить", а аккуратно отделить:

1. core parsing logic;
2. coordinator logic;
3. OS-specific runtime/network/instrumentation;
4. offload/storage transport;
5. launch/ops tooling.

## Что нужно сделать в этом новом чате

1. Построить план Windows-porting без потери текущего поведения.
2. Сказать, какие части кода:
   - можно оставить почти без изменений;
   - нужно абстрагировать;
   - нужно заменить Windows-реализациями.
3. Предложить новую структуру модулей, где Linux-specific и Windows-specific поведение будет отделено.
4. Сохранить совместимость логики:
   - input URL lists;
   - result bucket classification;
   - resume;
   - coordinator queue semantics;
   - offload metadata semantics.
5. Отдельно продумать, как на Windows заменить:
   - route/interface-bound download logic;
   - socket pressure metrics;
   - background process supervision;
   - headful/headless browser runtime;
   - mounted storage/offload path handling.
6. Продумать, как уменьшить риск connection explosion и hanging connections уже в процессе Windows-refactor.

## Как отвечать

Мне нужен не общий обзор, а практический engineering plan:

- карта текущей архитектуры;
- список Linux-specific узких мест;
- proposed target architecture for Windows;
- phased migration plan;
- минимально рискованный порядок переписывания;
- список тестов/smoke-checks после каждого этапа;
- список мест, где high risk regression.

## Дополнительные важные замечания

- Source of truth по статусам URL — это не storage, а results manifest/exported URL lists.
- Master list `need_parse_unique.json` и per-server shard assignment'ы — это разные уровни данных, их нельзя смешивать.
- Offload/storage не должен определять retry policy.
- Нельзя поломать resume после reboot.
- Telegram и Vimeo connectivity historically зависели от Linux routing/VPN; на Windows это нужно переосмыслить архитектурно, а не просто искать аналогичные команды.

---

Тебе нужно сначала сделать глубокую карту текущего кода и только потом предлагать переписывание под Windows. Считай, что цель — production-grade migration, а не локальный эксперимент.

