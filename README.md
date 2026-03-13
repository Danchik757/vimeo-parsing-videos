# Vimeo Video Downloader

> Актуальная инструкция для текущей рабочей версии: `SERVER_WORKERS_GUIDE.md`
>
> Snapshot старой single-worker версии до server/workers лежит в `WORKING_VERSION_GUIDE.md`
>
> Для full-run по `need_parse_unique.json` используй `run_batches.py` или `run_server.sh`.

Автоматизированный инструмент для массового скачивания видео с Vimeo с поддержкой Telegram уведомлений, прокси и работы на серверах.

## Возможности

- ✅ **Массовое скачивание:** Обработка списка из 694,343 видео
- ✅ **Максимальное качество:** Автоматический выбор Original/4K
- ✅ **Метаданные:** Сохранение JSON с информацией о каждом видео
- ✅ **Telegram уведомления:** Мониторинг прогресса в реальном времени
- ✅ **Обход блокировок:** undetected_chromedriver + поддержка прокси
- ✅ **Headless режим:** Работа на серверах без GUI
- ✅ **Умный retry:** Автоматические повторы при ошибках
- ✅ **IP blocking detection:** Детект блокировки и автоматическая остановка
- ✅ **Batch mode:** Разбиение master list на батчи по 10,000 URL с автоматическим переходом
- ✅ **Global registry:** Общий log уже обработанных и скачанных URL across all batches

## Быстрый старт

### 1. Установка

```bash
# Клонировать репозиторий (или распаковать архив)
cd vimeo-downloader

# Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt
```

### 2. Настройка

Отредактируйте `config.json`:

```json
{
  "vimeo_api": {
    "token": "ваш_токен_vimeo",
    "client_id": "ваш_client_id",
    "secret": "ваш_secret"
  },
  "telegram": {
    "enabled": true,
    "bot_token": "токен_от_@BotFather",
    "chat_id": "ваш_telegram_id",
    "notify_every_n_videos": 1
  },
  "proxy": {
    "enabled": false,  // true для использования прокси
    "host": "proxy.example.com",
    "port": "3128",
    "username": "user",
    "password": "pass"
  },
  "settings": {
    "test_mode": true,  // false для скачивания всех видео
    "test_limit": 50
  }
}
```

### 3. Запуск

```bash
source venv/bin/activate
python download_vimeo.py
```

## Получение Vimeo API токена

1. Перейдите на https://developer.vimeo.com/apps
2. Нажмите "Create App"
3. Заполните форму и создайте приложение
4. В разделе "Authentication" скопируйте:
   - **Personal Access Token** → `token`
   - **Client Identifier** → `client_id`
   - **Client Secret** → `secret`

## Настройка Telegram уведомлений

### Шаг 1: Создать бота

1. Найдите [@BotFather](https://t.me/botfather) в Telegram
2. Отправьте `/newbot`
3. Следуйте инструкциям
4. Скопируйте **bot token** (например: `1234567890:ABCdef...`)

### Шаг 2: Получить chat_id

1. Найдите [@userinfobot](https://t.me/userinfobot)
2. Отправьте `/start`
3. Скопируйте ваш **ID** (например: `123456789`)

### Шаг 3: Протестировать

```bash
source venv/bin/activate
python telegram_notifier.py
```

Вы получите тестовое сообщение в Telegram!

## Структура проекта

```
vimeo-downloader/
├── download_vimeo.py        # Главный скрипт
├── telegram_notifier.py     # Модуль уведомлений
├── config.json              # Конфигурация
├── need_parse_unique.json   # 694,343 URL
├── requirements.txt         # Зависимости
│
├── output/
│   ├── videos/             # Скачанные видео (.mp4/.mov)
│   ├── jsons/              # Метаданные (только для скачанных)
│   └── logs/               # Логи и ошибки
│
└── docs/
    ├── README.md           # Этот файл
    ├── USER_GUIDE.md       # Подробное руководство
    └── TECHNICAL_DETAILS.md # Техническая документация
```

## Режимы работы

### Тестовый режим (рекомендуется для начала)

```json
{
  "settings": {
    "test_mode": true,
    "test_limit": 50
  }
}
```

Скачает только первые 50 видео для проверки работоспособности.

### Полный режим

```json
{
  "settings": {
    "test_mode": false
  }
}
```

⚠️ **Важно:** Для скачивания всех 694,343 видео нужен ротирующий прокси!

## Работа на сервере

Скрипт работает в **headless режиме** (без GUI) и готов для серверного deployment.

### Требования

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install -y google-chrome-stable

# Или Chromium
sudo apt install -y chromium-browser
```

### Запуск в фоне

**screen:**
```bash
screen -S vimeo
source venv/bin/activate
python download_vimeo.py

# Отключиться: Ctrl+A, затем D
# Вернуться: screen -r vimeo
```

**tmux:**
```bash
tmux new -s vimeo
source venv/bin/activate
python download_vimeo.py

# Отключиться: Ctrl+B, затем D
# Вернуться: tmux attach -t vimeo
```

**nohup:**
```bash
nohup python download_vimeo.py > output.log 2>&1 &
tail -f output.log
```

## Мониторинг

### Telegram (рекомендуется)

Настройте Telegram бота в `config.json` и получайте уведомления на телефон:

- 🚀 Старт скачивания
- ✅ Каждое скачанное видео (настраиваемо)
- ❌ Ошибки
- 🚫 Блокировка IP
- 📊 Финальная статистика

### Логи

```bash
# Следить за логами в реальном времени
tail -f output/logs/download.log

# Посмотреть ошибки
cat output/logs/failed_downloads.json
```

### Статистика

```bash
# Сколько видео скачано
ls output/videos/ | wc -l

# Сколько JSON сохранено
ls output/jsons/ | wc -l

# Общий размер
du -sh output/videos/
```

## Частые проблемы

### IP BLOCKED

```
⚠️ IP BLOCKED: Rate limit exceeded (HTTP 429)
```

**Решения:**
1. Подождите 1-2 часа
2. Смените IP (VPN, другой Wi-Fi)
3. Включите прокси: `"enabled": true`
4. Используйте ротирующий прокси для больших объемов

### Authentication error (401/403)

```
Authentication error (status 401). Token may be expired.
```

**Решение:**
Получите новый токен на https://developer.vimeo.com/apps

### Download button not found

```
no such element: Unable to locate element: button[aria-label='Download button']
```

**Решение:**
Увеличьте `javascript_wait_time` до 20-30 секунд в `config.json`

### Chrome не запускается на сервере

**Решение:**
```bash
# Установите зависимости
sudo apt install -y \
    xvfb \
    libxi6 \
    libgconf-2-4 \
    libxss1 \
    libappindicator1

# Проверьте версию
google-chrome --version

# Должно быть: Google Chrome 145.x
```

## Работа с прокси

### Проверка прокси

```bash
# Через curl
curl --proxy http://user:pass@host:port https://vimeo.com/1071902981

# Проверить что IP изменился
curl --proxy http://user:pass@host:port https://api.ipify.org
```

### Рекомендации по прокси

Для скачивания больших объемов нужен **ротирующий прокси**:

| Провайдер | Тип | Стоимость/месяц |
|-----------|-----|-----------------|
| **Smartproxy** | Резидентные | $75+ |
| **Bright Data** | Резидентные | $500+ |
| **IPRoyal** | Резидентные | $50+ |

⚠️ **Важно:** Один статический прокси будет заблокирован так же быстро как ваш обычный IP!

## Полезные команды

```bash
# Остановить скрипт
pkill -f download_vimeo.py

# Очистить output
rm -rf output/videos/* output/jsons/* output/logs/*

# Найти видео по ID
ls output/videos/ | grep 1071902981

# Статистика
echo "Видео: $(ls output/videos/ | wc -l)"
echo "JSON: $(ls output/jsons/ | wc -l)"
echo "Размер: $(du -sh output/videos/)"
```

## Производительность

**Среднее время на 1 видео:** ~60 секунд
- API запрос: ~0.3s
- Загрузка страницы: ~15s
- JavaScript wait: ~15s
- Клик + download URL: ~5s
- Скачивание видео: ~25s (зависит от размера и скорости интернета)

**50 видео:** ~50 минут
**694,343 видео:** ~12-24 месяца (с ротирующим прокси: 2-4 месяца)

## FAQ

**Q: Можно ли скачивать параллельно на нескольких серверах?**

A: Да! Разделите `need_parse_unique.json` на части и запустите на разных серверах.

**Q: Что если скрипт упал посередине?**

A: Просто запустите снова - уже скачанные видео автоматически пропускаются.

**Q: Можно ли скачивать только метаданные без видео?**

A: Да, но потребуется модификация кода. Закомментируйте секцию скачивания видео (строки 299-302 в `download_vimeo.py`).

**Q: Какое разрешение скачивается?**

A: Максимальное доступное (Original → 4K → 1080p → 720p → ...).

## Документация

- [USER_GUIDE.md](docs/USER_GUIDE.md) - Подробное руководство пользователя
- [TECHNICAL_DETAILS.md](docs/TECHNICAL_DETAILS.md) - Техническая документация

## Требования

- **Python:** 3.10+
- **Chrome/Chromium:** 145.x
- **ОС:** macOS, Linux (Ubuntu/Debian/CentOS), Windows
- **RAM:** 2 GB минимум, 4 GB рекомендуется
- **Диск:** ~700 GB для всех видео + 10 GB для JSON

## Зависимости

- `selenium` - WebDriver automation
- `undetected-chromedriver` - Обход детекта ботов
- `PyVimeo` - Vimeo API client
- `requests` - HTTP library
- `tqdm` - Progress bars

## Лицензия

Для личного использования. Соблюдайте Terms of Service Vimeo.

---

**Успешной работы! 🚀**

Если возникли проблемы, проверьте [USER_GUIDE.md](docs/USER_GUIDE.md) или логи в `output/logs/download.log`.
