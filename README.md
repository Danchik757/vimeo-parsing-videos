# Vimeo Parsing Pipeline

Этот репозиторий содержит production-пайплайн для массового парсинга Vimeo-страниц, извлечения ссылок на Original/media, скачивания видео, записи metadata JSON, выгрузки результатов на внешнее хранилище и контроля длительных batch-run'ов через coordinator + workers.

Текущая ветка `windows` дополнительно содержит Windows-oriented runtime adaptations:

- platform-aware process supervision для worker/process tree cleanup;
- bounded HTTP sessions для Vimeo API, direct downloads и Telegram notifier;
- worker-side controlled restart и socket-pressure recycle logic;
- tracked shared credentials в `config.json`:
  - `vimeo_api`
  - `vimeo_login`
  - `telegram`
  - `workers.api_pool`
- tracked Windows config для запуска 4 worker-ов:
  - `configs/windows/config.windows.4workers.json`
- отдельный Windows runbook:
  - `docs/windows/WINDOWS_4WORKERS_RUNBOOK.md`

На момент написания этого README основной рабочий вариант в репозитории собран вокруг:

- queue-based launcher: `run_assigned_shards.py`
- worker/downloader: `download_vimeo_seleniumbase_v3.py`
- offloader: `scripts/offload_downloads.py`
- preflight: `scripts/preflight_parse.py`
- Telegram-нотификации: `telegram_notifier.py`

Нормального общего README раньше не было. Архитектура и runbook были размазаны по нескольким отдельным документам. Этот файл теперь является основной точкой входа.

## 1. Что делает код

Пайплайн делает следующее:

1. Берет список Vimeo URL.
2. Делит его на shard/batch JSON-файлы.
3. Запускает coordinator, который раздает batch'и worker-процессам.
4. Каждый worker:
   - открывает Vimeo-страницу через SeleniumBase/Chrome;
   - логинится при необходимости;
   - пытается найти лучший downloadable media source;
   - скачивает файл;
   - сохраняет JSON metadata;
   - пишет resume state и results manifest.
5. Coordinator агрегирует результаты по всем worker'ам.
6. Offloader переносит уже скачанные видео и JSON на внешнее хранилище.
7. Telegram-уведомления сообщают о прогрессе, stall и завершении.

## 2. Текущий статус репозитория

Текущая логика уже умеет:

- queue-based выполнение shard assignment'ов;
- resume после перезапуска питания или controlled restart;
- Telegram progress/stall/start/finish уведомления;
- offload на внешнее хранилище;
- экспорт списков URL по типам результата;
- preflight-проверки перед production-запуском;
- базовую защиту от socket pressure через coordinator gate.

В ветке `windows` дополнительно:

- worker может сам пережидать socket pressure перед API/download стадиями;
- worker может уходить в controlled restart после заданного числа обработанных URL;
- в `configs/windows/config.windows.4workers.json` по умолчанию стоит `workers.restart_after_processed = 100`;
- `configs/windows/config.windows.4workers.json` наследует те же credentials, что использовались в `config.parse-1.secrets.json`, но меняет runtime-пути и Windows-specific поведение;
- stall recovery убивает весь process tree worker-а, а не только Python process;
- Windows config убирает Linux-only routing/interface assumptions.

Текущий branch исторически использовался как hybrid между parse-1 production логикой и parse-3 серверной раскладкой.

## 3. Структура папок

После cleanup ветки `windows` структура разделена по ролям:

- `configs/windows/`
  Windows runtime-конфиги и smoke/production профили.
- `configs/parse3/`
  Parse-3 профиль и example secrets.
- `docs/windows/`
  Windows runbook'и, prompts и review-материалы.
- `docs/windows/archive/`
  Исторические prompts, которые оставлены только для справки.
- `docs/parse3/`
  Parse-3 runbook и network/runtime reference.
- `docs/analysis/`
  Аналитические документы по connection leaks и критическим проблемам.
- `docs/architecture/`
  Документы по layout хранения и общей структуре артефактов.
- `scripts/`
  Операционные утилиты: preflight, offload, shard creation, metrics.
- `data_shards/`
  Разрезанные URL-batch'и и manifests.
- корень репозитория
  Только core runtime-код, базовый `config.json`, master URL lists и `README.md`.

## 4. Основная карта репозитория

### Главные entrypoint-файлы

- `run_assigned_shards.py`
  Coordinator. Берет `manifest.json`, раздает batch'и worker-слотам, следит за завершением, агрегирует результаты, экспортирует итоговые URL-списки.

- `download_vimeo_seleniumbase_v3.py`
  Основной worker/downloader. Содержит:
  - загрузку config/secrets;
  - browser flow;
  - Vimeo API/page fallback logic;
  - resume state;
  - watchdog/stall detection;
  - direct download/curl download;
  - results manifest per worker/batch.

- `scripts/offload_downloads.py`
  Watcher/utility для выгрузки готовых локальных видео+JSON на storage root и, при включенной опции, удаления локального media файла после верификации.

- `scripts/preflight_parse.py`
  Preflight перед запуском. Проверяет конфиг, директории, зависимости, DNS/TCP доступность, Telegram/Vimeo endpoints, дисковое пространство, память, сокеты и наличие уже работающих процессов.

- `result_exports.py`
  Собирает финальные URL-списки:
  - `downloaded_original_urls.txt`
  - `not_downloaded_downloadable_urls.txt`
  - `no_links_urls.txt`
  - `transcript_modal_urls.txt`

- `scripts/create_server_assignments.py`
  Делит master URL list на shard-файлы для `parse-1`, `parse-2`, `parse-3`.

### Вспомогательные файлы

- `config_utils.py`
  Загрузка profile config + optional secrets file, path resolution.

- `telegram_notifier.py`
  Отправка уведомлений в Telegram с retry logic и bound source address support.

- `vimeo_cdp_helpers.py`
  Вспомогательная логика для извлечения download option'ов и работы с player iframe.

### Специализированная документация

- `docs/parse3/PARSE3_RUNTIME_NETWORK_REFERENCE.md`
- `docs/parse3/PARSE3_HYBRID_FROM_PARSE1_RUNBOOK.md`
- `docs/architecture/VIDEO_JSON_STORAGE_LAYOUT.txt`
- `docs/analysis/CONNECTION_LEAK_ANALYSIS.md`
- `docs/analysis/CRITICAL_ISSUES_REPORT.md`

## 5. Как устроен поток выполнения

### 4.1 Исходный список URL

Полный master list хранится в:

- `need_parse_unique.json`

Это обычный JSON list URL-строк. Локально в этой копии там 694343 уникальных Vimeo URL.

### 4.2 Разбиение на серверы и batch'и

Скрипт `scripts/create_server_assignments.py` режет общий список на shard'ы размером по 10000 URL и раскладывает их по серверам:

- `parse-1`: 24 shard'а
- `parse-2`: 18 shard'ов
- `parse-3`: 28 shard'ов

Файлы лежат в:

- `data_shards/server_assignments_10000/parse-1/`
- `data_shards/server_assignments_10000/parse-2/`
- `data_shards/server_assignments_10000/parse-3/`

В каждой папке есть свой `manifest.json`.

Важно:

- `need_parse_unique.json` = глобальный список
- `data_shards/server_assignments_10000/parse-1/*.json` = фактический subset для сервера parse-1

### 4.3 Coordinator

`run_assigned_shards.py`:

1. Загружает profile config.
2. Загружает assignment manifest.
3. Создает `workers/assignment_state.json`.
4. Поднимает worker-процессы.
5. Следит за кодами завершения worker'ов.
6. Реqueue'ит controlled restart batch'и.
7. Собирает `aggregate_results_manifest.json` и `aggregate_summary.json`.
8. Экспортирует итоговые URL-списки.

### 4.4 Worker

`download_vimeo_seleniumbase_v3.py`:

1. Загружает config и secrets.
2. Разрешает абсолютные пути.
3. Загружает source JSON batch.
4. Загружает `resume_state.json`.
5. Запускает browser session.
6. Обходит URL по порядку.
7. Для каждого URL:
   - пытается извлечь downloadable media;
   - пишет metadata JSON;
   - скачивает media;
   - обновляет resume state;
   - обновляет results manifest.

### 4.5 Offload

`scripts/offload_downloads.py` смотрит на `videos/downloaded/<video_id>/` и ищет завершенные пары:

- media file
- `<video_id>.json`

После проверки:

1. копирует их на `offload.storage_root`;
2. обновляет `_offload.status` в metadata;
3. записывает `offload_registry.json`;
4. при включенной опции удаляет локальный media-файл.

## 6. Ключевые runtime-артефакты

Типовой run root:

- `output/runs/<job_name>/`

Внутри наиболее важны:

- `download.log`
- `summary.json`
- `results_manifest.json`
- `failed_downloads.json`
- `resume_state.json`
- `offload.log`
- `offload_registry.json`
- `workers/assignment_state.json`
- `workers/aggregate_results_manifest.json`
- `workers/aggregate_summary.json`
- `shards/batch_XXXX/`

## 7. Resume и восстановление после падения/отключения питания

Это одна из самых важных частей кода.

### Что именно сохраняется

- coordinator state: `workers/assignment_state.json`
- per-batch resume: `shards/batch_XXXX/resume_state.json`
- per-batch results: `results_manifest.json`
- already downloaded files
- offload registry

### Что это означает practically

Если питание пропало, для этого пайплайна обычно не надо начинать заново.

Нужно:

1. не удалять `output/runs/...`;
2. снова запустить тот же coordinator command;
3. позволить worker'ам продолжить из `resume_state.json`.

## 8. Как устроены итоговые статусы URL

В коде важна не только категория "скачано / не скачано", а более точная классификация.

### `downloaded_original_urls.txt`

URL, для которых файл реально скачан.

### `not_downloaded_downloadable_urls.txt`

URL, для которых downloadable/link был найден, но итоговое скачивание не завершилось успешно.

Это retryable bucket.

### `no_links_urls.txt`

URL, где usable media link вообще не был найден.

Это обычно terminal bucket для cleanup исходного списка, если не планируется повторная ручная проверка.

### `transcript_modal_urls.txt`

URL, где открылся transcript/modal path вместо нормального video download path.

Это отдельный terminal/inspection bucket.

## 9. Как правильно чистить исходный список URL

Это критически важный operational вопрос.

Неправильно:

- вычитать URL только по папке storage `downloaded/`

Почему это неправильно:

1. storage покрывает только успешные downloads;
2. storage не знает про `no_links` и `transcript_modal`;
3. storage хранит video-id oriented layout, а source of truth по статусам лежит в result manifests.

Правильная схема:

- удалить из следующего списка:
  - `downloaded_original_urls.txt`
  - `no_links_urls.txt`
  - `transcript_modal_urls.txt`
- не удалять автоматически:
  - `not_downloaded_downloadable_urls.txt`

Иными словами:

- новый retry/remainder список = исходный input minus downloaded minus no_links minus transcript_modal

## 10. Проблема с большим количеством подключений

Это одна из главных оставшихся production-проблем.

### Источник проблемы

Даже если одно видео кажется "одной операцией", фактически на один URL может открываться несколько сетевых путей:

- browser page load
- Vimeo API request'ы
- iframe/player fallback
- media HEAD/GET
- curl fallback
- Telegram notifications

На больших worker-count это давало высокое число:

- `tcp_inuse`
- `tcp_timewait`
- `tcp_orphan`

Что в прошлом приводило к сетевой деградации и блокировкам со стороны провайдера.

### Что уже есть в коде

В коде уже добавлены/есть:

- socket pressure gate в coordinator;
- пороги:
  - `max_tcp_inuse_to_start_worker`
  - `max_tcp_timewait_to_start_worker`
  - `max_tcp_orphan_to_start_worker`
- controlled worker restart after N processed URLs;
- restart on repeated timeout patterns;
- runtime metrics collection и projection tooling.

### Что остается нерешенным полностью

Основной риск остается вокруг:

- browser/page connections;
- hanging/stuck download processes;
- non-linear рост сокетов при увеличении количества worker'ов;
- Linux-specific route/VPN failures, влияющих на повторные попытки.

То есть проблема не "исчезла", а частично сдерживается текущими ограничителями.

## 11. Linux-specific зависимости

Текущий production runtime сильно ориентирован на Linux.

Критические Linux-specific части:

- `/proc/net/sockstat` и `/proc/net/sockstat6`
- `curl --interface ...`
- `ip`, `ip route`
- WireGuard routing (`wg-quick`, `wg-*`)
- `systemd`, `resolvectl`
- `tmux`
- `xvfb-run`
- mounted storage paths
- Chrome/Chromedriver layout
- shell scripts в `scripts/*.sh`

Main runtime path в этой ветке уже адаптирован под Windows, но tooling вокруг него все еще частично Linux-oriented.

Это означает:

- `run_assigned_shards.py` + `download_vimeo_seleniumbase_v3.py` + `scripts/preflight_parse.py` уже можно переносить на Windows;
- `scripts/*.sh`, Linux socket metrics и interface-bound routing на Windows по-прежнему не являются рабочим runtime path;
- offload по умолчанию в Windows profile выключен, пока не будет подтвержден storage path/layout на новой машине.

## 12. Что смотреть в первую очередь новому разработчику

Если нужно быстро понять код, читать в таком порядке:

1. этот `README.md`
2. `configs/windows/config.windows.4workers.json`
3. `run_assigned_shards.py`
4. `download_vimeo_seleniumbase_v3.py`
5. `result_exports.py`
6. `scripts/preflight_parse.py`
7. `scripts/offload_downloads.py`
8. `docs/architecture/VIDEO_JSON_STORAGE_LAYOUT.txt`
9. `docs/parse3/PARSE3_RUNTIME_NETWORK_REFERENCE.md`
10. `docs/windows/WINDOWS_PORTING_CHAT_PROMPT.md`

## 13. Что нужно помнить при переносе или рефакторинге

- Не ломать resume state.
- Не ломать classification URL results.
- Не превращать `not_downloaded_downloadable` в terminal bucket.
- Не смешивать "global master list" и "per-server assignment subset".
- Не завязывать бизнес-логику напрямую на Linux absolute paths.
- Не считать storage единственным source of truth.
