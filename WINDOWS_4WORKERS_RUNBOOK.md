# Windows 4 Workers Runbook

Цель: перенести репозиторий на Windows-машину и запускать парсинг через 4 parallel workers.

## Что использовать

- config: `config.windows.4workers.json`
- branch: `windows`
- coordinator: `run_assigned_shards.py`
- worker: `download_vimeo_seleniumbase_v3.py`

## Что уже учтено в Windows config

- credentials берутся из tracked `config.json` через `base_config`
- `config.json` хранит те же shared credentials, что и в `config.parse-1.secrets.json`:
  - `vimeo_api`
  - `vimeo_login`
  - `telegram`
  - `workers.api_pool`
- Windows-specific Linux routing отключен:
  - `telegram.source_address = ""`
  - `settings.download_interface = ""`
  - `settings.download_retry_interface = ""`
- `offload.enabled = false`
- `workers.count = 4`
- `workers.restart_after_processed = 100`
- `workers.consecutive_timeout_failures_before_restart = 2`
- `runtime.vimeo_authenticated_session = true`
- `vimeo_login` уже заполнен в tracked `config.json`
- `workers.api_pool` уже заполнен в tracked `config.json`; при `workers.count = 4` используются первые 4 slot-а
- worker-side socket budget waiting отключен, потому что Linux `/proc` metrics на Windows недоступны

## Подготовка Windows-машины

1. Установить Python 3.13.
2. Установить Google Chrome или Microsoft Edge.
3. Скопировать репозиторий целиком.
4. Открыть `cmd` или PowerShell в корне репозитория.
5. Создать venv и установить зависимости:

```powershell
py -3.13 -m venv venv
venv\Scripts\python -m pip install --upgrade pip
venv\Scripts\python -m pip install -r requirements.txt
```

## Быстрый smoke-check

```powershell
venv\Scripts\python scripts\preflight_parse.py --config config.windows.4workers.json
```

## Полный запуск на master list

```powershell
venv\Scripts\python run_assigned_shards.py --config config.windows.4workers.json --manifest data_shards\server_assignments_10000\parse-3\manifest.json --workers 4
```

## Если нужен локальный single-worker smoke

```powershell
venv\Scripts\python download_vimeo_seleniumbase_v3.py --config config.windows.4workers.json
```

## Что проверить перед production run

- Chrome/Edge реально установлен
- `need_parse_unique.json` существует в корне repo
- хватает свободного места под `output/runs/windows-4workers`
- Telegram не привязан к Linux-only source IP
- при первых 20-50 URL нет массовых зависаний браузера

## Рекомендации по Windows start profile

- начать с `workers.count = 2`
- если первые 50-100 URL стабильны, поднять до `4`
- если Chrome начинает подвисать, уменьшить `restart_after_processed` до `40-50`
- если потребуется мягче давить browser/CDP leakage, уменьшить `restart_after_processed` ещё сильнее и оставить `workers.count` не выше `2-4`
