# Развертывание на сервере vg-intellect

Пошаговая инструкция по переносу и запуску Vimeo Downloader на удаленном сервере.

## Подготовка локально

### 1. Упаковка проекта

```bash
# Перейти в директорию проекта
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/original

# Создать архив (без venv и output)
tar -czf vimeo-downloader.tar.gz \
    --exclude='venv' \
    --exclude='output' \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.DS_Store' \
    download_vimeo.py \
    telegram_notifier.py \
    config.json \
    need_parse_unique.json \
    requirements.txt \
    chrome_proxy_extension/ \
    docs/

# Проверить архив
ls -lh vimeo-downloader.tar.gz
```

**Размер архива:** ~35-40 MB

### 2. Перенос на сервер

```bash
# Вариант 1: SCP
scp vimeo-downloader.tar.gz user@vg-intellect:/path/to/destination/

# Вариант 2: rsync (быстрее для повторных переносов)
rsync -avz vimeo-downloader.tar.gz user@vg-intellect:/path/to/destination/

# Вариант 3: Если сервер требует ключ
scp -i ~/.ssh/id_rsa vimeo-downloader.tar.gz user@vg-intellect:/path/to/destination/
```

---

## Настройка на сервере

### 1. Подключение к серверу

```bash
ssh user@vg-intellect

# Или с ключом
ssh -i ~/.ssh/id_rsa user@vg-intellect
```

### 2. Распаковка проекта

```bash
# Перейти в нужную директорию
cd /path/to/destination/

# Создать директорию для проекта
mkdir -p vimeo-downloader
cd vimeo-downloader

# Распаковать архив
tar -xzf ../vimeo-downloader.tar.gz

# Проверить содержимое
ls -la
```

### 3. Установка зависимостей системы

**Ubuntu/Debian:**

```bash
# Обновить систему
sudo apt update
sudo apt upgrade -y

# Установить Python 3.10+
sudo apt install -y python3 python3-pip python3-venv

# Установить Google Chrome
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb

# Проверить версию Chrome
google-chrome --version
# Ожидается: Google Chrome 145.x или новее

# Установить зависимости для headless Chrome
sudo apt install -y \
    xvfb \
    libxi6 \
    libgconf-2-4 \
    libxss1 \
    libappindicator1 \
    libindicator7 \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils

# Установить screen или tmux для фоновой работы
sudo apt install -y screen tmux
```

**CentOS/RHEL:**

```bash
# Обновить систему
sudo yum update -y

# Установить Python 3.10+
sudo yum install -y python3 python3-pip python3-virtualenv

# Установить Google Chrome
sudo yum install -y https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm

# Зависимости
sudo yum install -y \
    xorg-x11-server-Xvfb \
    gtk3 \
    libXScrnSaver \
    alsa-lib \
    nss \
    cups-libs

# Установить screen
sudo yum install -y screen tmux
```

### 4. Создание виртуального окружения

```bash
cd /path/to/destination/vimeo-downloader

# Создать виртуальное окружение
python3 -m venv venv

# Активировать
source venv/bin/activate

# Обновить pip
pip install --upgrade pip

# Установить зависимости
pip install -r requirements.txt
```

### 5. Проверка установки

```bash
# Проверить Python пакеты
pip list

# Должны быть:
# selenium==4.15.2
# undetected-chromedriver==3.5.4
# PyVimeo==1.1.0
# requests==2.31.0
# tqdm==4.66.1

# Проверить импорты
python3 -c "import vimeo; import undetected_chromedriver as uc; import selenium; print('✓ All modules imported successfully')"
```

### 6. Настройка config.json

```bash
# Отредактировать конфигурацию
nano config.json
# или
vim config.json
```

**Проверьте:**
- Vimeo API токен (token, client_id, secret)
- Telegram бот (bot_token, chat_id)
- Прокси (если используется)
- `test_mode: true` для первого запуска

### 7. Создание output директорий

```bash
mkdir -p output/videos output/jsons output/logs
chmod 755 output output/videos output/jsons output/logs
```

---

## Первый запуск (тестовый)

### 1. Тест Telegram уведомлений

```bash
source venv/bin/activate
python telegram_notifier.py
```

Вы должны получить тестовое сообщение в Telegram!

### 2. Проверка IP сервера

```bash
# Узнать IP сервера
curl -s https://api.ipify.org
echo ""

# Проверить блокировку Vimeo
curl -s https://vimeo.com/1071902981 | wc -c

# Если > 80,000 байт → IP чистый ✓
# Если < 15,000 байт → IP заблокирован ❌
```

### 3. Тестовый запуск (50 видео)

```bash
source venv/bin/activate

# Запустить в test режиме
python download_vimeo.py
```

**Что проверять:**
- ✅ Chrome запускается в headless режиме
- ✅ API запросы проходят успешно
- ✅ Telegram уведомления приходят
- ✅ Видео скачиваются в `output/videos/`
- ✅ JSON создаются в `output/jsons/`

---

## Запуск в фоне (production)

### Вариант 1: screen (рекомендуется)

```bash
# Создать сессию
screen -S vimeo

# Активировать окружение и запустить
source venv/bin/activate
python download_vimeo.py

# Отключиться от сессии
# Нажать: Ctrl+A, затем D

# Вернуться к сессии
screen -r vimeo

# Список всех сессий
screen -ls

# Убить сессию (если нужно)
screen -S vimeo -X quit
```

### Вариант 2: tmux

```bash
# Создать сессию
tmux new -s vimeo

# Активировать окружение и запустить
source venv/bin/activate
python download_vimeo.py

# Отключиться
# Нажать: Ctrl+B, затем D

# Вернуться
tmux attach -t vimeo

# Список сессий
tmux ls

# Убить сессию
tmux kill-session -t vimeo
```

### Вариант 3: nohup

```bash
source venv/bin/activate

# Запустить в фоне
nohup python download_vimeo.py > output.log 2>&1 &

# Узнать PID
echo $!
# Или
ps aux | grep download_vimeo.py

# Смотреть логи
tail -f output.log

# Остановить
pkill -f download_vimeo.py
```

### Вариант 4: systemd service (продвинутый)

Создать файл `/etc/systemd/system/vimeo-downloader.service`:

```ini
[Unit]
Description=Vimeo Video Downloader
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/vimeo-downloader
Environment="PATH=/path/to/vimeo-downloader/venv/bin"
ExecStart=/path/to/vimeo-downloader/venv/bin/python download_vimeo.py
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
```

Запуск:

```bash
# Перезагрузить systemd
sudo systemctl daemon-reload

# Запустить сервис
sudo systemctl start vimeo-downloader

# Автозапуск при загрузке
sudo systemctl enable vimeo-downloader

# Проверить статус
sudo systemctl status vimeo-downloader

# Смотреть логи
sudo journalctl -u vimeo-downloader -f
```

---

## Мониторинг

### 1. Telegram (основной способ)

Настройте в `config.json`:
```json
{
  "telegram": {
    "enabled": true,
    "notify_every_n_videos": 10  // Каждые 10 видео
  }
}
```

Вы будете получать уведомления на телефон автоматически!

### 2. Логи

```bash
# Следить в реальном времени
tail -f output/logs/download.log

# Последние 100 строк
tail -100 output/logs/download.log

# Фильтр по ошибкам
grep "ERROR" output/logs/download.log

# Фильтр по блокировкам IP
grep "IP BLOCKED" output/logs/download.log
```

### 3. Статистика

```bash
# Скачано видео
ls output/videos/ | wc -l

# Сохранено JSON
ls output/jsons/ | wc -l

# Общий размер
du -sh output/videos/

# Прогресс в реальном времени
watch -n 60 'echo "Видео: $(ls output/videos/ | wc -l)"'
```

### 4. Использование ресурсов

```bash
# CPU и RAM
top -p $(pgrep -f download_vimeo.py)

# Или htop
htop -p $(pgrep -f download_vimeo.py)

# Дисковое пространство
df -h
```

---

## Полный режим (все 694,343 видео)

После успешного тестирования:

### 1. Изменить config.json

```json
{
  "settings": {
    "test_mode": false  // Отключить тестовый режим
  },
  "telegram": {
    "notify_every_n_videos": 50  // Уведомлять каждые 50 видео
  }
}
```

### 2. Запустить в screen

```bash
screen -S vimeo-production
source venv/bin/activate
python download_vimeo.py

# Ctrl+A, D для отключения
```

### 3. Мониторинг

- Telegram уведомления каждые 50 видео
- Проверка логов раз в сутки
- Проверка места на диске раз в неделю

---

## Обслуживание

### Остановка скрипта

```bash
# Если в screen
screen -r vimeo
# Ctrl+C для остановки
# exit для выхода из screen

# Если в nohup
pkill -f download_vimeo.py

# Если systemd
sudo systemctl stop vimeo-downloader
```

### Перезапуск после ошибки

```bash
# Скрипт автоматически пропускает скачанные видео
# Просто запустите снова:

screen -S vimeo
source venv/bin/activate
python download_vimeo.py
```

### Очистка места на диске

```bash
# Удалить старые логи (если нужно)
rm output/logs/download.log.old

# Проверить failed_downloads
cat output/logs/failed_downloads.json

# Архивировать скачанные видео (опционально)
tar -czf videos_backup_$(date +%Y%m%d).tar.gz output/videos/
# Переместить архив на другой диск
mv videos_backup_*.tar.gz /mnt/storage/
```

### Обновление кода

```bash
# На локальной машине:
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/original
tar -czf vimeo-downloader-update.tar.gz download_vimeo.py telegram_notifier.py
scp vimeo-downloader-update.tar.gz user@vg-intellect:/path/to/vimeo-downloader/

# На сервере:
cd /path/to/vimeo-downloader
tar -xzf vimeo-downloader-update.tar.gz
# Перезапустить скрипт
```

---

## Решение проблем

### Chrome не запускается

```bash
# Проверить версию
google-chrome --version

# Протестировать headless режим
google-chrome --headless --no-sandbox --disable-dev-shm-usage --dump-dom https://google.com

# Если не работает, переустановить
sudo apt remove -y google-chrome-stable
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
```

### IP заблокирован

```bash
# Проверить IP
curl -s https://api.ipify.org

# Проверить блокировку
curl -s https://vimeo.com/1071902981 | wc -c

# Если заблокирован:
# 1. Подождать 12-24 часа
# 2. Включить прокси в config.json
# 3. Или сменить IP сервера (если возможно)
```

### Недостаточно места на диске

```bash
# Проверить место
df -h

# Найти самые большие файлы
du -sh output/videos/* | sort -h | tail -20

# Переместить на другой диск
rsync -av output/videos/ /mnt/storage/videos/
rm -rf output/videos/*
ln -s /mnt/storage/videos/ output/videos
```

### Высокое использование RAM

```bash
# Проверить память
free -h

# Если Chrome занимает много RAM:
# 1. Проверить что headless=True в config
# 2. Перезапустить скрипт раз в сутки (cron)
```

---

## Автоматизация

### Cron job для перезапуска

```bash
# Редактировать crontab
crontab -e

# Добавить перезапуск каждые 24 часа
0 3 * * * pkill -f download_vimeo.py && sleep 60 && cd /path/to/vimeo-downloader && /path/to/vimeo-downloader/venv/bin/python download_vimeo.py >> /path/to/cron.log 2>&1 &

# Или в screen
0 3 * * * screen -dmS vimeo bash -c 'cd /path/to/vimeo-downloader && source venv/bin/activate && python download_vimeo.py'
```

### Мониторинг места на диске

```bash
# Добавить в crontab проверку места
0 */6 * * * df -h | grep -E '^/dev/' | awk '{if($5+0 > 90) print "Disk space critical: "$0}' | mail -s "Disk Space Alert" your@email.com
```

---

## Чек-лист развертывания

- [ ] Упаковать проект локально
- [ ] Перенести на сервер (scp/rsync)
- [ ] Установить системные зависимости (Chrome, Python)
- [ ] Создать виртуальное окружение
- [ ] Установить Python пакеты
- [ ] Настроить config.json
- [ ] Протестировать Telegram уведомления
- [ ] Проверить IP сервера
- [ ] Тестовый запуск (50 видео)
- [ ] Запустить в screen/tmux
- [ ] Настроить мониторинг
- [ ] Переключить в полный режим

---

## Полезные алиасы

Добавьте в `~/.bashrc` или `~/.zshrc`:

```bash
alias vimeo-start='screen -dmS vimeo bash -c "cd /path/to/vimeo-downloader && source venv/bin/activate && python download_vimeo.py"'
alias vimeo-attach='screen -r vimeo'
alias vimeo-logs='tail -f /path/to/vimeo-downloader/output/logs/download.log'
alias vimeo-stats='echo "Videos: $(ls /path/to/vimeo-downloader/output/videos/ | wc -l) | JSONs: $(ls /path/to/vimeo-downloader/output/jsons/ | wc -l)"'
alias vimeo-stop='pkill -f download_vimeo.py'
```

Теперь можно использовать:
```bash
vimeo-start   # Запустить
vimeo-attach  # Подключиться
vimeo-logs    # Смотреть логи
vimeo-stats   # Статистика
vimeo-stop    # Остановить
```

---

**Успешного развертывания! 🚀**

При проблемах проверьте:
1. Логи: `tail -f output/logs/download.log`
2. Telegram уведомления
3. Системные ресурсы: `top`, `df -h`
