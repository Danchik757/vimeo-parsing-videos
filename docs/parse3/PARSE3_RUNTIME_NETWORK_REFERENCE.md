# Parse-3 Runtime And Network Reference

## Назначение

Этот документ описывает, как на сервере устроен пайплайн `parse-3`:

- какой скрипт за что отвечает
- как работает resume
- как считаются результаты
- как работают Telegram-уведомления
- как устроен offload на хранилище
- как скрипты подключаются к Vimeo
- как на этой машине работает WireGuard
- что нужно проверить перед production-запуском

Документ написан для текущей раскладки сервера, где:

- корень проекта: `/29d_kon/projects/vimeo-parsing-videos`
- локальный runtime-каталог parse-3: `output/runs/parse-3`
- корень удаленного хранилища: `/29d_kon/mount/mimas/vimeo/2025scraped/parse-3`
- WireGuard profile: `/etc/wireguard/wg-vimeo.conf`
- WireGuard IP для Telegram/Vimeo path: `10.8.0.6`

## Общая модель работы

Пайплайн `parse-3` состоит из двух долгоживущих процессов:

1. `run_assigned_shards.py`
2. `scripts/offload_downloads.py`

Первый процесс это coordinator. Он поднимает worker-шарды, следит за ними, сливает завершенные результаты и отправляет coordinator-level Telegram progress.

Второй процесс это offload watcher. Он сканирует локально скачанные видео, копирует их в storage, обновляет metadata и при необходимости удаляет локальный media file после успешной выгрузки.

Обычный production-запуск использует две tmux-сессии:

1. `parse3-parser`
2. `parse3-offload`

## Основные скрипты

### `run_assigned_shards.py`

Роль:

- читает manifest с assignment-ами
- определяет, какие shard batch-ы принадлежат этой машине
- создает per-batch config-и в `output/runs/parse-3/shards/batch_XXXX`
- запускает по одному worker process на каждый активный slot
- ведет batch state в `workers/assignment_state.json`
- сливает завершенные shard results в `workers/aggregate_results_manifest.json`
- пишет `workers/aggregate_summary.json`
- экспортирует URL lists для downloaded/skipped/failed buckets

Важное поведение:

- на старте он сбрасывает застрявшие `running` batch-ы обратно в `pending`
- сам coordinator видео не скачивает
- он только управляет subprocess-ами workers
- live progress считает как:
  - уже слитые aggregate results
  - плюс текущие live counts из активных shard `results_manifest.json`

Ключевые файлы coordinator-а:

- `output/runs/parse-3/workers/assignment_state.json`
- `output/runs/parse-3/workers/aggregate_results_manifest.json`
- `output/runs/parse-3/workers/aggregate_summary.json`
- `output/runs/parse-3/workers/coordinator.log`
- экспортированные URL lists в `output/runs/parse-3/workers/`

### `download_vimeo_seleniumbase_v3.py`

Роль:

- один процесс на один shard batch
- читает один shard JSON список
- продолжает работу из shard-local resume state
- делает API probe к Vimeo
- логинится в Vimeo в браузере, если это включено
- открывает video page и ищет download path
- скачивает media file
- сохраняет metadata и per-video results

Важное поведение:

- это основной рабочий worker
- он владеет своим shard directory, например:
  - `output/runs/parse-3/shards/batch_0045`
- он пишет собственные:
  - `download.log`
  - `summary.json`
  - `results_manifest.json`
  - `resume_state.json`
  - `failed_downloads.json`

Основные стадии внутри worker-а:

1. Загрузка config и secrets.
2. Создание `TelegramNotifier`.
3. Создание `vimeo.VimeoClient`.
4. Загрузка shard URL списка.
5. Загрузка `resume_state.json`.
6. Загрузка или пересборка `results_manifest.json`.
7. Запуск activity watchdog.
8. При необходимости логин в Vimeo через SeleniumBase.
9. Для каждого URL, начиная с `resume_state.next_index`:
   - API check
   - page/UI handling
   - modal/download link extraction
   - file download
   - metadata save
   - update `results_manifest`
   - update `resume_state`
10. Запись итогового `summary.json`.

Главные result buckets:

- `downloaded`
- `skipped`
- `failed`

Смысл:

- `downloaded`: файл успешно скачан
- `skipped`: ожидаемый skip по policy/availability, а не runtime bug
- `failed`: worker не смог корректно завершить обработку из-за технической, UI или сетевой проблемы

### `scripts/offload_downloads.py`

Роль:

- сканирует локально скачанные media files в videos root
- находит полностью готовые downloads
- копирует media и metadata в remote storage
- верифицирует uploads
- обновляет metadata через `_offload` block
- записывает completion в `offload_registry.json`
- по настройке удаляет локальный video file после upload

Важное поведение:

- offload независим от parser-а
- его можно запускать, останавливать и возобновлять отдельно
- он требует, чтобы storage path уже существовал и был writable
- он ничего не скачивает из Vimeo
- он только переносит уже готовые локальные файлы в storage

Ключевые файлы:

- `output/runs/parse-3/offload.log`
- `output/runs/parse-3/offload_registry.json`

### `scripts/preflight_parse.py`

Роль:

- выполняет readiness checks перед production-run
- валидирует config paths
- проверяет нужные tools
- проверяет interfaces и source IP bindings
- проверяет storage root
- проверяет, доступны ли Telegram/Vimeo через нужный interface
- проверяет process conflicts

Используй этот скрипт перед restart-ом после reboot или после сетевых изменений.

### `telegram_notifier.py`

Роль:

- асинхронный Telegram sender
- биндит HTTP session к `telegram.source_address`, если он задан
- отправляет start/progress/finish/stall/error сообщения

Важное поведение:

- он не роняет parser, если Telegram delivery не удалась
- он делает retry, пишет ошибку в лог и возвращает failure
- внутри используется sender thread и queue

Практический смысл:

- Telegram failure не равен parser failure
- но если `source_address` смотрит в сломанный WG path, лог быстро засоряется Telegram error-ами

### `result_exports.py`

Роль:

- превращает aggregate item results в плоские URL lists
- пишет экспортированные списки:
  - downloaded original URLs
  - not downloaded downloadable URLs
  - no-links URLs
  - transcript-modal URLs

### `config_utils.py`

Роль:

- загружает JSON config
- при необходимости мержит secrets file в profile
- резолвит относительные пути относительно config directory

## Структура runtime для parse-3

Типичное локальное дерево runtime:

```text
output/runs/parse-3/
  workers/
    assignment_state.json
    aggregate_results_manifest.json
    aggregate_summary.json
    coordinator.log
    worker_01/slot_summary.json
    ...
  shards/
    batch_0043/
      config.json
      download.log
      failed_downloads.json
      results_manifest.json
      resume_state.json
      summary.json
    ...
  videos/
  jsons/
  offload.log
  offload_registry.json
```

## Как работает resume

Resume состоит из двух уровней.

### Resume coordinator-а

Обрабатывается в `run_assigned_shards.py`.

Файл состояния:

- `output/runs/parse-3/workers/assignment_state.json`

Что происходит при restart:

- batch-ы со статусом `running` переводятся обратно в `pending`
- completed batch-ы остаются completed
- pending batch-ы остаются pending
- failed batch-ы зависят от retry policy

Смысл:

- restart coordinator-а не теряет очередь
- он только возвращает незавершенную работу обратно в pending

### Resume shard-а

Обрабатывается в `download_vimeo_seleniumbase_v3.py`.

Файл состояния:

- `output/runs/parse-3/shards/batch_XXXX/resume_state.json`

Ключевые поля:

- `next_index`
- `successful_downloads`
- `skipped_videos`
- `failed_videos`
- `last_video_id`
- `last_status`
- `completed`

Смысл:

- shard после restart продолжает с `next_index`
- он не начинает весь shard заново, если состояние не удалять и не откатывать вручную

### Results manifest

У каждого shard-а также есть:

- `results_manifest.json`

Он хранит по одному item на каждый входной URL со статусом:

- `pending`
- `downloaded`
- `skipped`
- `failed`

Важное различие:

- `resume_state.json` определяет, откуда продолжать обработку
- `results_manifest.json` определяет, как считаются счетчики и что попадет в exports

## Как считаются результаты

### `downloaded`

Количество item-ов с финальным статусом `downloaded`.

### `skipped`

Количество item-ов с финальным статусом `skipped`.

Типичные причины:

- `privacy.download=false and no API download links`
- `best available option is not original`
- `404 not found`
- `On Demand`

### `failed_logged`

Это не отдельный log-файл.

Он считается по item-ам со статусом `failed` в shard `results_manifest.json` плюс уже слитым aggregate item-ам.

Практическое следствие:

- если shard был restart-нут без очистки старых `failed` statuses, `failed_logged` продолжит их учитывать
- косметический reset `failed_logged` требует правки `results_manifest.json`
- повторная обработка старых failed URL требует отката `resume_state.next_index`

## Как происходит подключение к Vimeo

Есть три отдельных Vimeo-path.

### 1. Vimeo API metadata path

Используется через:

- `vimeo.VimeoClient`

Назначение:

- читать video metadata
- анализировать API response codes
- понимать, downloadable ли видео

Нормальные ответы:

- `200` для доступной video metadata
- `401` если токен не подходит для endpoint-а
- `403` для blocked/on-demand/private cases
- `404` для отсутствующего видео

### 2. Vimeo web page path

Используется через:

- SeleniumBase browser session

Назначение:

- логин в Vimeo
- открытие video page
- поиск download button
- обработка modal / iframe / fallback selectors

Нормальный ответ страницы:

- `200` на `https://vimeo.com`

### 3. Media file download path

Используется через:

- direct HTTP session, если `download_interface` пустой
- `curl --interface <download_interface>`, если `settings.download_interface` задан

Назначение:

- скачать сам video file после того, как найден финальный media URL

## Как устроена сеть на этой машине

### Базовые интерфейсы

Обычный outbound traffic сервера идет через:

- `eno1`

WireGuard profile для parse-3:

- `/etc/wireguard/wg-vimeo.conf`

Адрес в этом конфиге:

- `10.8.0.6/24`

### Критичная деталь WG config

В текущем конфиге есть:

```ini
Table = off
```

Это означает:

- `wg-quick` поднимает интерфейс
- но **не** ставит маршруты автоматически
- и **не** ставит policy routing автоматически

Поэтому после reboot:

- `wg-vimeo` может существовать
- handshake может быть
- но трафик с `10.8.0.6` все равно не работает, пока routing не добавлен вручную

### Почему после reboot ломался Telegram

Telegram notifier биндит запросы к:

- `telegram.source_address`

Для parse-3 это:

- `10.8.0.6`

Если `wg-vimeo` отсутствует или для него нет route table, Telegram падает с:

- `Cannot assign requested address`
- или `connection timed out`

### Какой ручной routing нужен на этом сервере

Чтобы трафик, созданный с `10.8.0.6`, шел через WG, машине сейчас нужно:

```bash
sudo systemctl start wg-quick@wg-vimeo
sudo ip route replace default dev wg-vimeo table 51820
sudo ip rule add pref 100 from 10.8.0.6/32 lookup 51820
```

Проверка:

```bash
ip rule show
ip route show table 51820
curl --interface 10.8.0.6 -4 -m 10 -I https://api.telegram.org
curl --interface 10.8.0.6 -4 -m 10 -I https://api.vimeo.com
curl --interface 10.8.0.6 -4 -m 10 -I https://vimeo.com
```

Нормальные ответы для этой проверки:

- Telegram: `302`
- Vimeo API: `401` без token
- Vimeo web: `200`

Это означает:

- route работает
- TCP/443 работает
- DNS работает
- сервис реально reachable

### Почему `401` от `api.vimeo.com` это хороший знак

Для обычного `curl -I https://api.vimeo.com` без bearer token:

- `401 Unauthorized` означает, что сетевой path отработал
- запрос дошел до Vimeo API
- сервис корректно ответил

Это не сетевая ошибка.

### Почему `302` от `api.telegram.org` это хороший знак

Для обычного `curl -I https://api.telegram.org`:

- `302` redirect на Telegram docs это нормальное поведение
- значит endpoint reachable

## Как загрузки используют сеть

Есть два отдельных механизма.

### Telegram-уведомления

Биндятся по source IP:

- `telegram.source_address`

Внутри это реализовано в `telegram_notifier.py` через custom `requests` adapter.

### Media download

Управляется через:

- `settings.download_interface`

Если поле задано, media files скачиваются через:

```text
curl --interface <download_interface>
```

Если поле пустое, загрузка идет обычным `requests` без interface binding.

Практический смысл:

- Telegram и media download настраиваются отдельно
- Telegram может ходить через WG, а media скачиваться по обычному route
- или WG можно полностью отключить и запускать parser по plain network

## Как работает storage

Текущий storage root для parse-3:

- `/29d_kon/mount/mimas/vimeo/2025scraped/parse-3`

Offload script ожидает, что этот путь существует и writable.

Локальный download path parser-а отдельный:

- локальные файлы падают в `output/runs/parse-3/videos`
- потом offload копирует их в storage root

Практический смысл:

- parser можно запускать без offload
- offload нельзя запускать нормально, если storage mount не поднят

## Типовые классы ошибок

### `Download button not found`

Смысл:

- API говорит, что видео может быть downloadable
- но UI selector path не нашел usable download trigger

Типичные причины:

- несовпадение Vimeo UI variant
- delayed page hydration
- gap в page/iframe selectors

### `Transcript download modal opened instead of video download`

Смысл:

- click path открыл transcript/captions modal вместо video download modal

Это UI problem, а не проблема прав аккаунта на видео.

### `stall`

Смысл:

- worker process жив
- но давно не было новой activity

Watchdog сообщает:

- current video
- current stage
- idle seconds
- processed/downloaded/skipped/failed counters

Типичная причина:

- Selenium/CDP/browser hang на page interaction step

## Рекомендуемый preflight перед production-запуском

### Сеть

```bash
sudo systemctl start wg-quick@wg-vimeo
sudo ip route replace default dev wg-vimeo table 51820
sudo ip rule add pref 100 from 10.8.0.6/32 lookup 51820 2>/dev/null || true

curl --interface 10.8.0.6 -4 -m 10 -I https://api.telegram.org
curl --interface 10.8.0.6 -4 -m 10 -I https://api.vimeo.com
curl --interface 10.8.0.6 -4 -m 10 -I https://vimeo.com
```

### Storage

```bash
ls -ld /29d_kon/mount/mimas/vimeo/2025scraped/parse-3
touch /29d_kon/mount/mimas/vimeo/2025scraped/parse-3/.write_test
rm -f /29d_kon/mount/mimas/vimeo/2025scraped/parse-3/.write_test
```

### Preflight приложения

```bash
cd /29d_kon/projects/vimeo-parsing-videos
source venv/bin/activate
python3 scripts/preflight_parse.py \
  --config configs/parse3/config.parse-3.profile.json \
  --manifest data_shards/server_assignments_10000/parse-3/manifest.json
```

## Порядок production-запуска

### Parser

```bash
tmux new -s parse3-parser
cd /29d_kon/projects/vimeo-parsing-videos
source venv/bin/activate
python run_assigned_shards.py \
  --config configs/parse3/config.parse-3.profile.json \
  --manifest data_shards/server_assignments_10000/parse-3/manifest.json \
  --workers 10
```

### Offload

```bash
tmux new -s parse3-offload
cd /29d_kon/projects/vimeo-parsing-videos
source venv/bin/activate
python scripts/offload_downloads.py --config configs/parse3/config.parse-3.profile.json --watch --delete-local-video
```

## Безопасная остановка

```bash
tmux send-keys -t parse3-parser C-c
tmux send-keys -t parse3-offload C-c
sleep 15
pkill -f run_assigned_shards.py
pkill -f download_vimeo_seleniumbase_v3.py
pkill -f offload_downloads.py
```

Для продолжения позже используй те же команды запуска. Resume state хранится в:

- `workers/assignment_state.json`
- `shards/batch_*/resume_state.json`
- `offload_registry.json`

## Что делать дальше

Для текущего состояния сервера практический порядок такой:

1. Убедиться, что `wg-vimeo` поднят.
2. Вернуть ручной routing для `10.8.0.6`.
3. Проверить:
   - Telegram через `10.8.0.6`
   - Vimeo API через `10.8.0.6`
   - Vimeo web через `10.8.0.6`
4. Проверить, что storage root существует и writable.
5. Запустить `scripts/preflight_parse.py`.
6. Запустить `parse3-parser`.
7. Запустить `parse3-offload`.

## Технический долг, который надо закрыть позже

В текущей серверной схеме есть одна хрупкая точка:

- `wg-vimeo.conf` использует `Table = off`
- ручные `ip rule` и `table 51820` не переживают reboot

Поэтому после каждого reboot Telegram/Vimeo WG path приходится восстанавливать руками.

Долгосрочные варианты починки:

1. Добавить `PostUp` и `PostDown` в `/etc/wireguard/wg-vimeo.conf`
2. Добавить отдельный systemd oneshot service, который восстанавливает:
   - `ip route replace default dev wg-vimeo table 51820`
   - `ip rule add from 10.8.0.6/32 lookup 51820`

Пока это не автоматизировано, post-reboot networking нельзя считать production-safe.
