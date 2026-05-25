# Parse-3 Hybrid From Parse-1

Эта ветка собрана от `codex/parse-1-production` и адаптирована под сервер `parse-3`.

Что сохранено из `parse-1-production`:
- queue-based launcher `run_assigned_shards.py`
- runtime/preflight/socket-pressure инструменты
- production-подход `parser + offload watcher`
- retry и resume логика batch-ов

Что добавлено из `parse-3`:
- fallback на Vimeo player iframe
- детекция transcript-only modal
- экспорт `transcript_modal_urls.txt`

## Локальные пути на сервере

- repo: `/29d_kon/projects/vimeo-parsing-videos`
- run root: `/29d_kon/projects/vimeo-parsing-videos/output/runs/parse-3`
- storage root: `/29d_kon/mount/mimas/vimeo/2025scraped/parse-3`

## Профиль

Основной профиль в репозитории:

- `configs/parse3/config.parse-3.profile.json`

Локальный secrets-файл должен лежать рядом и называться:

- `config.parse-3.secrets.local.json`

Его можно создать по шаблону:

- `configs/parse3/config.parse-3.secrets.example.json`

## Сеть

Эта конфигурация использует:

- `telegram.source_address = 10.8.0.6`
- `settings.download_interface = wg-vimeo`

На сервере после reboot нужно вручную вернуть routing для `wg-vimeo`, потому что в WG config стоит `Table = off`.

Минимальный рабочий набор:

```bash
sudo systemctl start wg-quick@wg-vimeo
sudo ip route replace default dev wg-vimeo table 51820
sudo ip rule add pref 100 from 10.8.0.6/32 lookup 51820 2>/dev/null || true
```

Проверка:

```bash
curl --interface 10.8.0.6 -4 -m 10 -I https://api.telegram.org
curl --interface 10.8.0.6 -4 -m 10 -I https://api.vimeo.com
curl --interface 10.8.0.6 -4 -m 10 -I https://vimeo.com
```

Нормальные ответы:

- Telegram: `302`
- `api.vimeo.com`: `401`
- `vimeo.com`: `200`

## Запуск на сервере

Parser:

```bash
tmux new -s parse3-parser
cd /29d_kon/projects/vimeo-parsing-videos
source venv/bin/activate
python run_assigned_shards.py \
  --config configs/parse3/config.parse-3.profile.json \
  --manifest data_shards/server_assignments_10000/parse-3/manifest.json \
  --workers 10
```

Offloader:

```bash
tmux new -s parse3-offload
cd /29d_kon/projects/vimeo-parsing-videos
source venv/bin/activate
python scripts/offload_downloads.py --config configs/parse3/config.parse-3.profile.json --watch --delete-local-video
```

## Что появится в `workers/`

- `downloaded_original_urls.txt`
- `not_downloaded_downloadable_urls.txt`
- `no_links_urls.txt`
- `transcript_modal_urls.txt`

## Что важно не забыть

- secrets в git не коммитятся
- `config.parse-3.secrets.local.json` нужно держать только на сервере
- если server reboot-нулся, сначала подними `wg-vimeo` и routing, потом уже parser/offloader
