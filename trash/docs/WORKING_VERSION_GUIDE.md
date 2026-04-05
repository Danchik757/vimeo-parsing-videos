# Working Snapshot Guide

Этот файл описывает именно текущую рабочую версию до серверного режима, workers и расширенного Telegram-логирования.

## Что входит в snapshot

- Основной скрипт: `download_vimeo_seleniumbase_v3.py`
- Исправленный поиск кнопки Download через CDP helper: `vimeo_cdp_helpers.py`
- Точка входа: `run.sh`
- Конфиг: `config.json`

## Что уже работает

- Скрипт умеет обрабатывать список Vimeo URL из JSON.
- Для downloadable-видео он может получить ссылку либо через API, либо через кнопку Download на странице Vimeo.
- Исправлен кейс, когда SeleniumBase в UC/CDP режиме не находил кнопку Download.
- Логи пишутся в файл, указанный в `config.json`.

## Текущее ограничение snapshot

- Это desktop/single-worker версия.
- В `download_vimeo_seleniumbase_v3.py` браузер запускается с `headless=False`.
- Серверный запуск без монитора, workers и продвинутое Telegram-логирование в этот snapshot еще не входят.

## Как запускать

### 1. Перейти в директорию

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
```

### 2. Проверить конфиг

Открой `config.json` и проверь блок `files`.

Важно:

- `source_json` сейчас указывает на `/Users/admin/Documents/LAB/CODECS/4k/Parse/original/need_parse_unique.json`
- `videos_dir`, `jsons_dir`, `logs_dir`, `failed_downloads`, `log_file` сейчас настроены на:
  - `output/runs/first50_original_20260313/videos`
  - `output/runs/first50_original_20260313/jsons`
  - `output/runs/first50_original_20260313`
  - `output/runs/first50_original_20260313/failed_downloads.json`
  - `output/runs/first50_original_20260313/download.log`

### 3. Подготовить окружение

Если `venv` уже есть:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

Если `venv` нет:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Запуск

Через shell wrapper:

```bash
./run.sh
```

Или напрямую:

```bash
source venv/bin/activate
python download_vimeo_seleniumbase_v3.py
```

## Как смотреть лог

```bash
tail -f /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix/output/runs/first50_original_20260313/download.log
```

## Где лежат скачанные видео

```bash
/Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix/output/runs/first50_original_20260313/videos
```

## Быстрая проверка

Для тестового прогона в `config.json` уже включено:

```json
"test_mode": true,
"test_limit": 50
```

Для полного прогона нужно поменять:

```json
"test_mode": false
```

## Что проверять после запуска

- Создается ли `download.log`
- Появляются ли файлы в `videos`
- Появляются ли JSON метаданные в `jsons`
- Нет ли новых `no_button_*.html` или `cloudflare_fail_*.html` в директории логов

## Что будет следующим этапом

После сохранения этого snapshot можно отдельно делать:

- серверную версию без монитора
- многоворкерный запуск
- расширенное Telegram-логирование
