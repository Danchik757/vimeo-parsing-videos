# Vimeo Video Downloader

Автоматическое скачивание видео с Vimeo через API.

## Быстрый старт

### 1. Установка (только первый раз)
```bash
# Виртуальное окружение уже создано и зависимости установлены
source venv/bin/activate
```

### 2. Тестовый запуск (первые 100 видео)
```bash
./run.sh
```

или напрямую:
```bash
source venv/bin/activate
python download_vimeo.py
```

### 3. Мониторинг
В отдельном терминале:
```bash
tail -f download.log
```

## Настройки

Файл `config.json`:
- `test_mode: true` - тестирование на первых 100 видео
- `test_mode: false` - обработка всех 694,343 видео
- `test_limit` - количество видео в тестовом режиме

## Структура
- `need_parse_unique.json` - 694,343 URL для скачивания
- `videos/` - скачанные видео
- `jsons/` - метаданные каждого видео
- `download.log` - логи работы
- `failed_downloads.json` - ошибки скачивания

## Troubleshooting

**Ошибка 401/403:**
- Токен Vimeo API истёк
- Обновите `config.json` → `vimeo_api.token`

**"Captcha/verification detected":**
- Vimeo показывает капчу
- Скрипт автоматически делает retry
- Видео пропускается после 3 попыток

**ModuleNotFoundError:**
```bash
source venv/bin/activate
pip install -r requirements.txt
```

## Подробная документация

Смотри [PROJECT_MEMO.md](PROJECT_MEMO.md) для полной информации о проекте.
