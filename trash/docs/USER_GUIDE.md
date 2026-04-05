# Vimeo Video Downloader - Руководство пользователя

## Быстрый старт

### 1. Установка зависимостей

```bash
# Активировать виртуальное окружение
source venv/bin/activate

# Установить пакеты (если еще не установлены)
pip install -r requirements.txt
```

### 2. Настройка конфигурации

Отредактируйте `config.json`:

```json
{
  "vimeo_api": {
    "token": "ваш_токен",
    "client_id": "ваш_client_id",
    "secret": "ваш_secret"
  },
  "proxy": {
    "enabled": true,        // false = без прокси
    "host": "185.88.101.106",
    "port": "3128",
    "username": "subject",
    "password": "ваш_пароль"
  },
  "telegram": {
    "enabled": true,        // Включить Telegram уведомления
    "bot_token": "ваш_токен",
    "chat_id": "ваш_chat_id",
    "notify_every_n_videos": 1
  },
  "settings": {
    "test_mode": true,      // true = только первые 50 видео
    "test_limit": 50
  }
}
```

### 3. Запуск скачивания

```bash
python download_vimeo.py
```

Или через скрипт:

```bash
./run.sh
```

---

## Структура проекта

```
.
├── download_vimeo.py           # Основной скрипт скачивания
├── telegram_notifier.py        # Модуль Telegram уведомлений
├── config.json                 # Конфигурация
├── need_parse_unique.json      # Список 694,343 URL для скачивания
├── requirements.txt            # Зависимости Python
│
├── chrome_proxy_extension/     # Расширение Chrome для прокси
│   ├── manifest.json
│   └── background.js
│
├── output/                     # Все результаты работы
│   ├── videos/                 # Скачанные видео (.mp4, .mov)
│   ├── jsons/                  # Метаданные (только для скачанных)
│   └── logs/                   # Логи и ошибки
│       ├── download.log
│       └── failed_downloads.json
│
├── docs/                       # Документация
│   ├── README.md              # Быстрый старт
│   ├── USER_GUIDE.md          # Руководство пользователя (этот файл)
│   └── TECHNICAL_DETAILS.md   # Техническая документация
│
└── venv/                       # Виртуальное окружение Python
```

---

## Настройки в config.json

### Vimeo API

```json
"vimeo_api": {
  "token": "...",      // OAuth токен
  "client_id": "...",  // ID приложения
  "secret": "..."      // Секретный ключ
}
```

Получить на: https://developer.vimeo.com/apps

### Прокси

```json
"proxy": {
  "enabled": true,           // Включить/выключить прокси
  "host": "185.88.101.106",  // IP прокси
  "port": "3128",            // Порт
  "username": "subject",     // Логин
  "password": "..."          // Пароль
}
```

**Важно:**
- `enabled: false` - работа БЕЗ прокси (может быть блокировка при большом количестве запросов)
- `enabled: true` - работа ЧЕРЕЗ прокси (используется Chrome extension)

### Файлы и папки

```json
"files": {
  "source_json": "need_parse_unique.json",    // Список URL
  "videos_dir": "output/videos",              // Куда сохранять видео
  "jsons_dir": "output/jsons",                // Куда сохранять метаданные
  "logs_dir": "output/logs",                  // Куда сохранять логи
  "failed_downloads": "output/logs/failed_downloads.json"  // Ошибки
}
```

### Настройки скачивания

```json
"settings": {
  "test_mode": true,              // true = тестовый режим
  "test_limit": 50,               // Сколько видео обработать в test_mode
  "retry_attempts": 3,            // Сколько раз повторять при ошибке
  "retry_delay": 5,               // Пауза между попытками (секунды)
  "javascript_wait_time": 15      // Ожидание загрузки страницы (секунды)
}
```

---

## Режимы работы

### Тестовый режим (первые 50 видео)

```json
{
  "settings": {
    "test_mode": true,
    "test_limit": 50
  }
}
```

Используйте для проверки работоспособности.

### Полный режим (все 694,343 видео)

```json
{
  "settings": {
    "test_mode": false
  }
}
```

**⚠️ Внимание:**
- Для полного режима ОБЯЗАТЕЛЬНО нужен ротирующий прокси
- Один статический прокси будет заблокирован
- Рекомендуется: Smartproxy, Bright Data, IPRoyal

---

## Мониторинг процесса

### 1. Смотреть логи в реальном времени

```bash
tail -f output/logs/download.log
```

### 2. Проверить сколько видео скачано

```bash
ls output/videos/ | wc -l
```

### 3. Проверить сколько JSON метаданных

```bash
ls output/jsons/ | wc -l
```

### 4. Посмотреть ошибки

```bash
cat output/logs/failed_downloads.json
```

---

## Что делать при ошибках

### Ошибка: "IP BLOCKED"

```
⚠️ IP BLOCKED: Rate limit exceeded (HTTP 429)
Your IP has been blocked by Vimeo due to too many requests
```

**Решения:**
1. Подождите 1-2 часа
2. Включите прокси в config.json: `"enabled": true`
3. Смените IP (VPN, перезагрузка роутера)
4. Используйте ротирующий прокси для больших объемов

### Ошибка: "Authentication error (401/403)"

```
Authentication error (status 401). Token may be expired.
```

**Решение:**
1. Перейдите на https://developer.vimeo.com/apps
2. Получите новый токен
3. Обновите в config.json: `"token": "новый_токен"`

### Ошибка: "API RATE LIMIT EXCEEDED (429)"

```
⚠️ API RATE LIMIT EXCEEDED
Your API token has exceeded rate limits
```

**Решение:**
- Подождите 1 час
- Или используйте другой API токен

### Ошибка: "Download button not found"

```
no such element: Unable to locate element: button[aria-label='Download button']
```

**Решение:**
- Увеличьте `javascript_wait_time` в config.json до 20-30 секунд
- Проверьте что `test_mode: true` для проверки

### Браузер не открывается

**Решение:**
1. Проверьте что Chrome установлен
2. Удалите кеш ChromeDriver:
```bash
rm -rf ~/Library/Application\ Support/undetected_chromedriver/
```
3. Перезапустите скрипт

---

## Telegram уведомления

### Настройка Telegram бота

**Шаг 1: Создайте бота**

1. Найдите [@BotFather](https://t.me/botfather) в Telegram
2. Отправьте `/newbot`
3. Введите имя бота (например: "Vimeo Downloader Bot")
4. Введите username бота (например: "vimeo_dl_bot")
5. Скопируйте **bot token** (например: `1234567890:ABCdef...`)

**Шаг 2: Получите chat_id**

Вариант 1 - Личный чат:
1. Найдите [@userinfobot](https://t.me/userinfobot)
2. Отправьте `/start`
3. Скопируйте ваш **ID** (например: `123456789`)

Вариант 2 - Канал/группа:
1. Создайте приватный канал
2. Добавьте бота в администраторы
3. Используйте [@RawDataBot](https://t.me/rawdatabot) для получения ID канала

**Шаг 3: Настройте config.json**

```json
{
  "telegram": {
    "enabled": true,                  // Включить уведомления
    "bot_token": "1234567890:ABC...", // Токен из BotFather
    "chat_id": "123456789",           // Ваш ID или ID канала
    "notify_every_n_videos": 1,       // Уведомлять каждые N видео
    "notify_on_start": true,          // Уведомление при запуске
    "notify_on_finish": true,         // Уведомление при завершении
    "notify_on_error": true,          // Уведомление при ошибках
    "notify_on_ip_block": true        // Уведомление при блокировке IP
  }
}
```

### Примеры конфигураций

**Минимум уведомлений (только критичное):**
```json
"telegram": {
  "enabled": true,
  "notify_every_n_videos": 100,  // Каждые 100 видео
  "notify_on_start": false,
  "notify_on_finish": true,
  "notify_on_error": true,
  "notify_on_ip_block": true
}
```

**Максимум уведомлений:**
```json
"telegram": {
  "enabled": true,
  "notify_every_n_videos": 1,    // Каждое видео
  "notify_on_start": true,
  "notify_on_finish": true,
  "notify_on_error": true,
  "notify_on_ip_block": true
}
```

### Что вы получите в Telegram

**При запуске:**
```
🚀 Vimeo Downloader ЗАПУЩЕН
📊 Режим: ТЕСТОВЫЙ РЕЖИМ
📹 Всего видео: 50
🕐 Время старта: 2026-03-11 06:15:00
```

**После скачивания:**
```
✅ Видео скачано #15
🎬 ID: 1071902981
📁 Файл: 1071902981.mp4
💾 Размер: 1024.5 MB
📊 Прогресс: 15/50 (30.0%)
⏰ 06:25:30
```

**При блокировке IP:**
```
🚫 IP ЗАБЛОКИРОВАН
🌐 IP: 103.249.134.114
⚠️ Причина: Rate limit exceeded
⏰ 2026-03-11 06:35:00
```

**При завершении:**
```
✅ Vimeo Downloader ЗАВЕРШЕН
📊 Статистика:
📹 Всего обработано: 50
✅ Скачано: 12
⏭ Пропущено: 35
❌ Ошибок: 3
⏰ Время завершения: 2026-03-11 07:10:00
```

### Тестирование уведомлений

```bash
# Проверить работу Telegram бота
source venv/bin/activate
python telegram_notifier.py

# Вы должны получить тестовое сообщение в Telegram
```

---

## Работа на сервере (headless режим)

### Зачем headless режим?

- **Без GUI:** Chrome работает без графического интерфейса
- **Меньше ресурсов:** ~200 MB RAM экономии
- **Для серверов:** Работает на серверах без X11/дисплея
- **Быстрее:** На 10-15% быстрее загрузка страниц

### Текущая настройка

Скрипт **УЖЕ настроен** для работы в headless режиме:

```python
# download_vimeo.py, строка 44
chrome_options.headless = True  # Headless режим включен

# Дополнительные оптимизации:
chrome_options.add_argument('--disable-extensions')
chrome_options.add_argument('--disable-logging')
chrome_options.add_argument('--disable-dev-shm-usage')  # Для Docker
chrome_options.add_argument('--no-sandbox')  # Для серверов
```

### Требования для сервера

**Ubuntu/Debian:**
```bash
# Установить Chrome/Chromium
sudo apt update
sudo apt install -y chromium-browser chromium-chromedriver

# Или Google Chrome
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb

# Зависимости для headless режима
sudo apt install -y \
    xvfb \
    libxi6 \
    libgconf-2-4 \
    libxss1 \
    libappindicator1 \
    libindicator7
```

**CentOS/RHEL:**
```bash
# Установить Chrome
sudo yum install -y google-chrome-stable

# Зависимости
sudo yum install -y \
    xorg-x11-server-Xvfb \
    gtk3 \
    libXScrnSaver
```

### Проверка работы на сервере

```bash
# 1. Проверить версию Chrome
google-chrome --version
# Ожидается: Google Chrome 145.x

# 2. Проверить что Chrome может запуститься
google-chrome --headless --no-sandbox --disable-dev-shm-usage --dump-dom https://google.com

# 3. Запустить тест
source venv/bin/activate
python download_vimeo.py
```

### Запуск в фоне (screen/tmux)

**Вариант 1: screen**
```bash
# Создать сессию
screen -S vimeo

# Запустить скрипт
source venv/bin/activate
python download_vimeo.py

# Отключиться: Ctrl+A, затем D

# Вернуться к сессии
screen -r vimeo
```

**Вариант 2: tmux**
```bash
# Создать сессию
tmux new -s vimeo

# Запустить скрипт
source venv/bin/activate
python download_vimeo.py

# Отключиться: Ctrl+B, затем D

# Вернуться
tmux attach -t vimeo
```

**Вариант 3: nohup**
```bash
# Запустить в фоне
source venv/bin/activate
nohup python download_vimeo.py > output.log 2>&1 &

# Посмотреть лог
tail -f output.log

# Остановить
pkill -f download_vimeo.py
```

### Мониторинг на сервере

```bash
# Telegram уведомления (основной способ)
# Настройте в config.json и получайте уведомления на телефон

# Проверить логи
tail -f output/logs/download.log

# Проверить прогресс
watch -n 60 'echo "Видео: $(ls output/videos/ | wc -l)"'

# Проверить использование ресурсов
top -p $(pgrep -f download_vimeo.py)
```

---

## Работа с прокси

### Проверка прокси перед запуском

```bash
# Простая проверка
python tests/check_proxy_simple.py

# Проверка через curl
bash tests/test_proxy_manual.sh
```

### Если прокси не работает

1. Проверьте креденшелы в config.json
2. Убедитесь что порт и IP правильные
3. Проверьте что ваш IP в whitelist у провайдера прокси
4. Попробуйте работать без прокси: `"enabled": false`

### Рекомендации по прокси

Для скачивания всех 694,343 видео:

| Провайдер | Тип | IP в пуле | Стоимость/месяц |
|-----------|-----|-----------|-----------------|
| **Smartproxy** | Резидентные | 40M+ | $75+ |
| **Bright Data** | Резидентные | 72M+ | $500+ |
| **IPRoyal** | Резидентные | 2M+ | $50+ |
| **Oxylabs** | Резидентные | 100M+ | $300+ |

**Важно:** Нужен РОТИРУЮЩИЙ прокси, а не один статический IP!

---

## Полезные команды

### Остановить работающий скрипт

```bash
pkill -f download_vimeo.py
```

### Очистить вывод для нового запуска

```bash
rm -rf output/videos/*
rm -rf output/jsons/*
rm -rf output/logs/*
```

### Посмотреть статистику

```bash
echo "Видео: $(ls output/videos/ | wc -l)"
echo "JSON: $(ls output/jsons/ | wc -l)"
echo "Размер: $(du -sh output/videos/)"
```

### Найти видео определенного ID

```bash
# Найти видео 1071902981
ls output/videos/ | grep 1071902981
ls output/jsons/ | grep 1071902981
```

---

## FAQ

### Q: Сколько времени займет скачивание всех видео?

**A:** Зависит от:
- Скорости интернета
- Размера видео (среднее ~500 MB)
- Использования прокси
- Настройки `javascript_wait_time`

Примерно:
- БЕЗ прокси: 1-2 года (риск блокировки)
- С ротирующим прокси: 2-4 месяца

### Q: Можно ли скачивать параллельно на нескольких компьютерах?

**A:** Да! Разделите need_parse_unique.json на части:

```bash
# Первый компьютер: 0-100,000
# Второй компьютер: 100,000-200,000
# и т.д.
```

### Q: Что если скрипт упал посередине?

**A:** Скрипт автоматически пропускает уже скачанные видео. Просто запустите снова:

```bash
python download_vimeo.py
```

### Q: Как узнать какие видео не удалось скачать?

**A:** Смотрите `output/logs/failed_downloads.json`:

```bash
cat output/logs/failed_downloads.json | jq
```

### Q: Можно ли скачивать только метаданные без видео?

**A:** Да, закомментируйте в коде строки скачивания видео (216-290). Метаданные всегда сохраняются автоматически.

### Q: Какое разрешение видео скачивается?

**A:** Скрипт выбирает максимальное доступное качество:
1. Original (если доступен)
2. Наивысшее разрешение из доступных

---

## Поддержка

Если возникли проблемы:

1. Проверьте логи: `output/logs/download.log`
2. Посмотрите документацию в `docs/`
3. Проверьте настройки в `config.json`

Техническая документация: [TECHNICAL_DETAILS.md](TECHNICAL_DETAILS.md)
