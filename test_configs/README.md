# Test Configs

Файлы в этой директории обезличены и безопасны для Git.

Что важно:

- реальные Vimeo API credentials в них не хранятся
- реальные Telegram credentials в них не хранятся
- реальные proxy credentials в них не хранятся

Перед локальным запуском:

1. Скопируй нужный файл в `*.local.json`
2. Подставь реальные токены и chat_id
3. Запускай скрипт с `--config path/to/file.local.json`

Пример:

```bash
cp test_configs/server_smoke.json test_configs/server_smoke.local.json
```
