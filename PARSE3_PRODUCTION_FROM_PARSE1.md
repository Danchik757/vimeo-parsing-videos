# Parse-3 Production From Parse-1

Эта ветка основана на `codex/parse-3-production`, но ее runtime приведен к рабочей схеме `codex/parse-1-production`.

Что сделано:

- взят production runtime из `parse-1`: launcher, offload watcher, preflight, socket/runtime metrics
- оставлены `parse-3` Vimeo helpers
- добавлен `parse-3`-специфичный fallback через player iframe
- добавлен экспорт `transcript_modal_urls.txt`
- добавлен `config.parse-3.profile.json` под сервер `parse-3`

## Профиль parse-3

- workers: `10`
- `settings.download_interface = wg-vimeo`
- `telegram.source_address = 10.8.0.6`
- storage root: `/29d_kon/mount/mimas/vimeo/2025scraped/parse-3`
- local output root: `output/runs/parse-3`

## Secrets

В git лежит только шаблон:

- `config.parse-3.secrets.example.json`

На сервере рядом с профилем должен лежать:

- `config.parse-3.secrets.local.json`

## WG для parse-3

На сервере после reboot:

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

## Запуск

```bash
tmux new -s parse3-parser
cd /29d_kon/projects/vimeo-parsing-videos
source venv/bin/activate
python run_assigned_shards.py \
  --config config.parse-3.profile.json \
  --manifest data_shards/server_assignments_10000/parse-3/manifest.json \
  --workers 10
```

```bash
tmux new -s parse3-offload
cd /29d_kon/projects/vimeo-parsing-videos
source venv/bin/activate
python scripts/offload_downloads.py --config config.parse-3.profile.json --watch --delete-local-video
```
