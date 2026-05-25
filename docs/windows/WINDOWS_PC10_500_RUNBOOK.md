# Windows PC10 500-URL Runbook

Целевой host:
- код лежит в `C:\Users\msu_cc\Desktop\work\29d_kon\parsing`
- runtime config: `configs/windows/config.windows.pc10.4workers.500.json`
- manifest на `500` URL/batch: `data_shards\recovery_remaining_20260511_500\manifest.json`

Storage layout:
- локальный runtime пишет временные результаты в `output\runs\pc10\windows-4workers-500`
- готовые `video + json` offloader складывает на:
  - `\\sciencestorage\cc\vimeo_Datasets\downloaded\<video_id>\`
- внутри папки `<video_id>` будут:
  - медиафайл
  - `<video_id>.json`

Почему не пишем сразу на UNC path:
- downloader и resume-логика безопаснее работают на локальном диске
- текущая архитектура уже умеет переносить готовые `video + json` на storage через `scripts/offload_downloads.py`
- так меньше риск повредить сетевой файл при обрыве браузера/процесса

## Перенос через GitHub

### На текущем компьютере

1. Убедиться, что в ветке `windows` есть все изменения, которые должны попасть на `pc10`.
2. Если есть незакоммиченные изменения, которые нужны на `pc10`, сначала закоммитить их.
3. Запушить ветку `windows` на GitHub.
4. Временно открыть репозиторий.
5. Дождаться, пока `pc10` завершит `git clone`.
6. Сразу после этого снова закрыть репозиторий.

Команды:
```bash
cd /Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix
git status
git push origin windows
```

### На `pc10`

```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon
git clone https://github.com/Danchik757/vimeo-parsing-videos.git parsing
cd parsing
git checkout windows
py -3.11 -m venv venv
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

После успешного `git clone` репозиторий уже можно снова закрывать на GitHub.

## Что проверить на `pc10` до старта

Проверка Python:
```powershell
py -3.11 --version
```

Проверка браузера:
```powershell
Get-Command chrome, msedge -ErrorAction SilentlyContinue
```

Проверка сетевого storage path:
```powershell
Test-Path \\sciencestorage\cc\vimeo_Datasets
New-Item -ItemType Directory -Path \\sciencestorage\cc\vimeo_Datasets\_write_test -Force
Remove-Item \\sciencestorage\cc\vimeo_Datasets\_write_test -Force
```

Проверка config и manifest:
```powershell
Test-Path .\configs\windows\config.windows.pc10.4workers.500.json
Test-Path .\data_shards\recovery_remaining_20260511_500\manifest.json
```

Обязательный preflight:
```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\preflight_parse.py --config configs\windows\config.windows.pc10.4workers.500.json --manifest data_shards\recovery_remaining_20260511_500\manifest.json
```

Быстрый smoke offload без watch:
```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\offload_downloads.py --config configs\windows\config.windows.pc10.4workers.500.json --limit 1
```

## Что запускать

Parser:
```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe run_assigned_shards.py --config configs\windows\config.windows.pc10.4workers.500.json --manifest data_shards\recovery_remaining_20260511_500\manifest.json --workers 4
```

Offloader:
```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\offload_downloads.py --config configs\windows\config.windows.pc10.4workers.500.json --watch
```

Preflight:
```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\preflight_parse.py --config configs\windows\config.windows.pc10.4workers.500.json --manifest data_shards\recovery_remaining_20260511_500\manifest.json
```

## Как запускать парсинг

Сначала сделать короткий smoke:
```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe run_assigned_shards.py --config configs\windows\config.windows.pc10.4workers.500.json --manifest data_shards\recovery_remaining_20260511_500\manifest.json --workers 1 --max-batches 1
```

Если smoke проходит, запускать основной run:
```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe run_assigned_shards.py --config configs\windows\config.windows.pc10.4workers.500.json --manifest data_shards\recovery_remaining_20260511_500\manifest.json --workers 4
```

Рекомендуемый порядок:
1. `preflight_parse.py`
2. короткий `run_assigned_shards.py --workers 1 --max-batches 1`
3. `offload_downloads.py --limit 1`
4. основной `run_assigned_shards.py --workers 4`
5. `offload_downloads.py --watch`

## Как работает restart after 100

- `workers.restart_after_processed = 100` не убивает скачивание посередине файла в нормальном path
- worker дорабатывает текущий URL, сохраняет `results_manifest.json` и `resume_state.json`, затем запрашивает controlled restart
- coordinator видит `exit code 75`, re-queue'ит тот же batch и новый worker продолжает с `resume_state.next_index`
- это restart worker process, а не “обнуление всего job”
- при batch size `500` один batch может пройти через несколько worker generations, это ожидаемо

Практический эффект:
- browser/CDP state очищается чаще
- но растёт накладной расход на перезапуск Chrome и повторный login
- из-за `stagger_start_seconds = 20` все 4 worker-а не должны синхронно перезапускаться

## Что важно проверить во время первых запусков

- `\\sciencestorage\cc\vimeo_Datasets` реально открывается на запись из PowerShell
- хватает места на `C:` под локальный `output\runs\pc10\windows-4workers-500`
- Chrome или Edge установлен
- первые `20-50` URL проходят без long-hang browser state
- offloader реально создаёт remote path вида `downloaded\<video_id>\`
- при остановке coordinator через `Ctrl+C` batch-и возвращаются в `pending`, но список процессов `chrome.exe` и `chromedriver.exe` всё равно стоит быстро проверить вручную
