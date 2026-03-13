# Server And Workers Guide

Этот файл описывает актуальную версию после добавления:

- server-like запуска без видимого окна
- coordinator с workers
- batch coordinator для списков по 10,000 URL
- Telegram-уведомлений для worker-ов и coordinator
- watchdog/heartbeat уведомлений
- resume after stop/crash
- глобального реестра уже обработанных URL

## Основные файлы

- `download_vimeo_seleniumbase_v3.py` — один worker
- `run_workers.py` — coordinator, который режет список и запускает N worker-ов
- `run_batches.py` — coordinator верхнего уровня, который режет master list на батчи и последовательно запускает `run_workers.py`
- `run.sh` — локальный запуск одного worker-а
- `run_server.sh` — Linux server wrapper через `xvfb-run` для batch-mode
- `scripts/server_preflight.sh` — проверка сервера до установки зависимостей
- `config.json` — основной конфиг

## Режимы запуска

### 1. Локальный single-worker

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
./run.sh --config config.json
```

### 2. Локальный headless smoke

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
./venv/bin/python download_vimeo_seleniumbase_v3.py --config test_configs/server_smoke.json
```

### 3. Workers на локальной машине

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
./venv/bin/python run_workers.py --config config.json --workers 2
```

### 4. Batch mode на локальной машине

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
./venv/bin/python run_batches.py --config config.json --workers 5
```

### 5. Linux server без монитора

```bash
cd /path/to/codex_vimeo_fix
./run_server.sh --config config.json --workers 2
```

`run_server.sh` рассчитан именно на Linux и запускает `run_batches.py` через `xvfb-run`.

## Что настраивать в config.json

### files

- `source_json` — входной JSON со списком Vimeo URL
- `videos_dir` — куда складывать видео
- `jsons_dir` — куда складывать JSON метаданные
- `logs_dir` — корневая директория логов
- `failed_downloads` — JSON с ошибками
- `log_file` — основной лог одного worker-а
- `summary_file` — summary одного worker-а
- `results_file` — общий manifest по всем URL со статусом и причиной

### browser

- `headless` — запуск без видимого окна
- `xvfb` — использовать виртуальный дисплей; нужно для Linux server, когда окно реально нельзя открыть
- `uc` — SeleniumBase undetected mode

Рекомендации:

- локально: `headless=false`, `xvfb=false`
- server-like на той же машине: `headless=true`, `xvfb=false`
- Linux server: `headless=false`, `xvfb=true`

### workers

- `count` — число worker-ов по умолчанию
- `stagger_start_seconds` — пауза между стартами worker-ов
- `shared_media_dirs` — все worker-ы пишут видео/JSON в общие `files.videos_dir` и `files.jsons_dir`

CLI-переопределение:

```bash
./venv/bin/python run_workers.py --config config.json --workers 4
```

### batches

- `enabled` — включить режим батчей
- `batch_size` — размер батча, например `10000`
- `auto_advance` — после завершения батча сразу переходить к следующему
- `reuse_existing_shards` — переиспользовать уже созданные `batch_0001.json`, `batch_0002.json`, ...
- `max_batches` — ограничить число батчей на один запуск; `0` = без лимита
- `shards_dir` — где лежат shard-файлы батчей
- `runs_dir` — где создаются логи и summary каждого батча
- `state_file` — глобальный checkpoint batch coordinator
- `manifest_file` — список всех батчей и их статусов
- `global_results_file` — общий manifest по уже обработанным URL across all batches
- `global_downloaded_file` — отдельный список только скачанных видео
- `global_summary_file` — агрегированная summary по всей кампании

Пример:

```bash
./venv/bin/python run_batches.py --config config.json --workers 5 --batch-size 10000
```

Полезные режимы:

```bash
./venv/bin/python run_batches.py --config config.json --prepare-only
./venv/bin/python run_batches.py --config config.json --workers 5 --max-batches 1
./venv/bin/python run_batches.py --config config.json --workers 5 --batch-start 1 --batch-end 35
./venv/bin/python run_batches.py --config config.json --workers 5 --batch-start 36 --batch-end 70
```

Распределение по нескольким машинам:

- машина A: `--batch-start 1 --batch-end 35`
- машина B: `--batch-start 36 --batch-end 70`
- shard-файлы уже не пересекаются, поэтому worker-ы с разных машин не делают одну и ту же работу

### resume

- `enabled` — включить resume state
- `state_file` — JSON checkpoint с `next_index`, counters и last status
- `skip_completed_files` — пропускать уже существующие готовые файлы
- `resume_partial_downloads` — продолжать `.part` файл через HTTP Range

Как это работает:

- после каждого обработанного URL worker пишет checkpoint в `resume_state.json`
- при новом запуске worker продолжает с `next_index`, а не с нуля
- если во время скачивания остался `.part`, worker пытается продолжить файл, а не качать заново
- если входной список URL изменился, старый checkpoint автоматически игнорируется
- в multi-worker режиме у каждого worker-а свой `resume_state.json`

Как сбросить прогресс и начать заново:

- удалить `resume_state.json`
- удалить `.part` файлы, если они остались
- при необходимости удалить уже скачанные `videos/*`

### telegram

Сейчас в основном `config.json` Telegram включён.

Важные поля:

- `enabled`
- `bot_token`
- `chat_id`
- `notify_download_every_n_successes`
- `notify_skip_every_n_processed`
- `notify_progress_every_n_processed`
- `notify_progress_min_interval_seconds`
- `notify_on_start`
- `notify_on_finish`
- `notify_on_error`
- `notify_on_stall`
- `notify_on_heartbeat`

Пример более спокойного режима:

```json
"telegram": {
  "enabled": true,
  "notify_download_every_n_successes": 5,
  "notify_skip_every_n_processed": 0,
  "notify_progress_every_n_processed": 25,
  "notify_progress_min_interval_seconds": 300,
  "notify_on_start": true,
  "notify_on_finish": true,
  "notify_on_error": true,
  "notify_on_stall": true,
  "notify_on_heartbeat": true
}
```

Как это читать:

- `notify_download_every_n_successes: 5` — сообщение только после каждого 5-го успешно скачанного видео
- `notify_skip_every_n_processed: 0` — skip-сообщения выключены
- `notify_progress_every_n_processed: 25` — progress-сообщение после каждых 25 обработанных URL
- `notify_progress_min_interval_seconds: 300` — даже если счётчик совпал, чаще чем раз в 5 минут progress не уйдёт

### watchdog

- `stall_alert_after_seconds` — через сколько секунд без активности слать alert
- `repeat_alert_every_seconds` — как часто повторять alert
- `heartbeat_every_seconds` — период heartbeat

## Что пишет coordinator

При `run_workers.py` создаётся:

- `output/.../workers/coordinator.log`
- `output/.../workers/aggregate_summary.json`
- `output/.../workers/aggregate_results_manifest.json`

Для каждого worker-а создаются:

- `output/.../workers/worker_01/download.log`
- `output/.../workers/worker_01/summary.json`
- `output/.../workers/worker_01/resume_state.json`
- `output/.../workers/worker_01/results_manifest.json`
- `output/.../workers/worker_01/videos`
- `output/.../workers/worker_01/jsons`

При `run_batches.py` дополнительно создаются:

- `data_shards/.../batch_0001.json`
- `output/batches/runs/batch_0001/batch_summary.json`
- `output/batches/runs/batch_coordinator.log`
- `output/batches/batch_state.json`
- `output/batches/batches_manifest.json`
- `output/batches/global_results_manifest.json`
- `output/batches/global_downloaded_videos.json`
- `output/batches/global_summary.json`

Именно `global_results_manifest.json` и `global_downloaded_videos.json` решают задачу общего лога: уже не нужно ходить по каждой папке батча.

В `jsons/VIDEO_ID.json` лежит:

- полный raw ответ Vimeo API по видео
- `video_id` и исходный URL
- информация о факте скачивания: filename, path, size, source, selected_quality

## Что уходит в Telegram

### Happy path

- старт coordinator
- старт worker-а
- скачивание видео
- прогресс
- завершение worker-а
- завершение coordinator

### Аварийные сценарии

- API error 401/429
- обычные ошибки скачивания
- аварийное завершение процесса
- stall alert при долгом отсутствии активности
- heartbeat

## Что уже было проверено локально

- `test_configs/server_smoke.json` — headless single-worker, успешно скачал `1070654646`
- `test_configs/workers_smoke.json` — 2 worker-а, успешно скачали 4 из 4 маленьких видео
- `test_configs/workers_smoke_telegram.json` — тот же сценарий с Telegram-уведомлениями
- `test_configs/api_error_telegram.json` — контролируемый 401/API error с Telegram-alert
- `test_configs/watchdog_telegram.json` — искусственный watchdog/stall smoke

## Практический совет для реального прод-запуска

- Начни с `--workers 2`
- Если работа идёт с одного IP без ротирующего прокси, не поднимай сразу много worker-ов
- Для Linux server лучше использовать `run_server.sh`
- На сервере обязательно проверь путь `source_json`, потому что в текущем `config.json` он указывает на локальный macOS путь
- Для master list на 694,343 URL используй `batches.enabled=true` и `batch_size=10000`

## Что проверить на сервере

Выполни на сервере:

```bash
cd /path/to/codex_vimeo_fix
chmod +x scripts/server_preflight.sh
./scripts/server_preflight.sh
```

Дополнительно проверь:

- хватает ли места под видео
- есть ли исходный JSON со списком URL
- есть ли доступ наружу к `vimeo.com`, `api.vimeo.com`, `api.telegram.org`
- можно ли писать в директории, указанные в `files.*`

## GitHub перенос

### Перед первым push

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
cp config.example.json config.local.json
```

Заполни `config.local.json` своими реальными токенами и путями. Этот файл в Git не уйдёт.

### Инициализация репозитория

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
git init
git branch -M main
git add .
git status
git commit -m "Initial import: Vimeo downloader with server and workers support"
```

### Если используешь GitHub CLI

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
gh repo create YOUR_GITHUB_USERNAME/codex-vimeo-fix --private --source=. --remote=origin --push
```

### Если создаёшь репозиторий через веб-интерфейс GitHub

После создания пустого репозитория выполни:

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
git remote add origin git@github.com:YOUR_GITHUB_USERNAME/codex-vimeo-fix.git
git push -u origin main
```

### На сервере после clone

```bash
git clone git@github.com:YOUR_GITHUB_USERNAME/codex-vimeo-fix.git
cd codex-vimeo-fix
cp config.example.json config.local.json
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Потом уже на сервере настраивай `config.local.json` и запускай:

```bash
./run_server.sh --config config.local.json --workers 2
```

## GitHub и секреты

- В Git нельзя отправлять реальный `config.json`
- Для этого в репозитории есть `config.example.json`
- Локальный `config.json` и `config.local.json` игнорируются через `.gitignore`
- Тестовые конфиги в `test_configs/` уже обезличены и пригодны для публикации
