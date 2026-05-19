# Windows PC10 200-URL Smoke Runbook

Цель:

- проверить, что на Windows-машине все подключено и доступно;
- прогнать реальный queue-based smoke на `1 worker` и `200 URL`;
- получить понятные результаты без лишней параллельности;
- не повторять уже учтенные Linux-progress URL.

Использовать:

- branch: `windows`
- config: `config.windows.pc10.1worker.200.json`
- manifest: `data_shards\windows_pc10_smoke_20260518_200\manifest.json`

Тестовый набор:

- `200` URL взяты из `remaining_after_linux_progress` списка
- source file:
  - `data_shards\windows_pc10_smoke_20260518_200\need_parse_unique.remaining_after_linux_progress_20260511.first200.json`

## 1. Что проверяем этим smoke

Этим прогоном проверяем сразу:

- Python/venv/зависимости
- Chrome или Edge
- DNS и TCP до `api.vimeo.com`
- DNS и TCP до `api.telegram.org`
- загрузку config через `base_config`
- доступ к `config.json` credentials
- запись в локальный runtime path
- доступ к storage UNC path
- coordinator path
- worker path
- resume artifacts
- offload one-shot path

## 2. Где должен лежать репозиторий на Windows

Рекомендуемый путь:

- `C:\Users\msu_cc\Desktop\work\29d_kon\parsing`

Дальше в командах я использую именно его.

## 3. Подготовка машины

Проверить:

1. Установлен Python 3.11
2. Установлен Google Chrome или Microsoft Edge
3. Репозиторий уже склонирован
4. Ветка `windows` checkout'нута

Команды:

```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
git status
git branch --show-current
py -3.11 --version
```

Создать venv и поставить зависимости:

```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 4. Ручные проверки перед preflight

### Браузер

```powershell
Get-Command chrome, chrome.exe, msedge, msedge.exe -ErrorAction SilentlyContinue
```

### DNS

```powershell
Resolve-DnsName api.vimeo.com
Resolve-DnsName api.telegram.org
Resolve-DnsName github.com
```

### TCP

```powershell
Test-NetConnection api.vimeo.com -Port 443
Test-NetConnection api.telegram.org -Port 443
Test-NetConnection github.com -Port 443
```

### Storage path

```powershell
Test-Path \\sciencestorage\cc\vimeo_Datasets
New-Item -ItemType Directory -Path \\sciencestorage\cc\vimeo_Datasets\_write_test -Force
Remove-Item \\sciencestorage\cc\vimeo_Datasets\_write_test -Force
```

### Файлы smoke-run

```powershell
Test-Path .\config.windows.pc10.1worker.200.json
Test-Path .\data_shards\windows_pc10_smoke_20260518_200\manifest.json
Test-Path .\data_shards\windows_pc10_smoke_20260518_200\batch_0001.json
```

## 5. Обязательный preflight

```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\preflight_parse.py --config config.windows.pc10.1worker.200.json --manifest data_shards\windows_pc10_smoke_20260518_200\manifest.json
```

Что должно быть по смыслу:

- `Ready to start: YES`
- `vimeo_api.*` заполнены
- `telegram.bot_token` и `telegram.chat_id` заполнены
- `vimeo_login.email/password` заполнены
- `source_json` найден
- `manifest` найден
- `offload.storage_root` доступен
- `browser` найден

Если здесь есть `FAIL`, parser не запускать, пока не исправлена причина.

## 6. Быстрый offload smoke до основного запуска

Нужен, чтобы заранее проверить, что UNC path и сам offloader живы.

```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\offload_downloads.py --config config.windows.pc10.1worker.200.json --limit 1
```

Если локальных файлов еще нет, это нормально: важно, чтобы скрипт стартовал без path/permission crash.

## 7. Запуск smoke-run

Запуск coordinator + 1 worker + 1 batch of 200:

```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe run_assigned_shards.py --config config.windows.pc10.1worker.200.json --manifest data_shards\windows_pc10_smoke_20260518_200\manifest.json --workers 1
```

## 8. Как смотреть лог во время работы

Coordinator:

```powershell
Get-Content .\output\runs\pc10\windows-1worker-200\workers\coordinator.log -Wait
```

Worker download log:

```powershell
Get-Content .\output\runs\pc10\windows-1worker-200\shards\batch_0001\download.log -Wait
```

Offload log:

```powershell
Get-Content .\output\runs\pc10\windows-1worker-200\offload.log -Wait
```

## 9. Что проверить после завершения

### Итоговые summary/results

```powershell
Get-Content .\output\runs\pc10\windows-1worker-200\workers\aggregate_summary.json
Get-Content .\output\runs\pc10\windows-1worker-200\workers\aggregate_results_manifest.json
```

### URL buckets

```powershell
Get-Content .\output\runs\pc10\windows-1worker-200\workers\downloaded_original_urls.txt
Get-Content .\output\runs\pc10\windows-1worker-200\workers\not_downloaded_downloadable_urls.txt
Get-Content .\output\runs\pc10\windows-1worker-200\workers\no_links_urls.txt
Get-Content .\output\runs\pc10\windows-1worker-200\workers\transcript_modal_urls.txt
```

### Проверка локальных скачанных файлов

```powershell
Get-ChildItem .\output\runs\pc10\windows-1worker-200\videos\downloaded -Recurse | Select-Object FullName, Length | Select-Object -First 50
```

### Проверка storage после one-shot offload

После появления локальных video+json:

```powershell
cd C:\Users\msu_cc\Desktop\work\29d_kon\parsing
$env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\offload_downloads.py --config config.windows.pc10.1worker.200.json --limit 5
```

Потом проверить:

```powershell
Get-ChildItem \\sciencestorage\cc\vimeo_Datasets\downloaded | Select-Object -First 20
```

## 10. Почему в smoke стоит 1 worker

Это сделано специально:

- легче понять, что именно ломается;
- если есть browser hang, он не маскируется другим worker'ом;
- проще понять качество login path;
- проще оценить connection behavior;
- проще проверить offload и result buckets.

## 11. Что делать, если smoke проходит

Если этот прогон успешен, следующий шаг:

1. оставить тот же Windows host;
2. перейти на `config.windows.pc10.4workers.500.json`;
3. сначала снова сделать `preflight_parse.py`;
4. потом `run_assigned_shards.py --workers 1 --max-batches 1`;
5. и только потом полный `--workers 4`.

## 12. Что считать успешным результатом smoke

Smoke считаем успешным, если:

- preflight проходит без `FAIL`
- worker реально обрабатывает URL, а не падает на старте
- появляются `results_manifest.json` и `summary.json`
- есть хотя бы несколько `downloaded` или осмысленные `no_links/not_downloaded_downloadable`
- offloader может без crash увидеть локальные completed files
- storage path доступен на запись
- после завершения нет зависшего леса `chrome.exe` / `chromedriver.exe`

Проверка зависших процессов:

```powershell
Get-Process chrome, chromedriver, msedge -ErrorAction SilentlyContinue
```

