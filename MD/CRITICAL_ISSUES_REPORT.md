# Отчет о критических проблемах и уязвимостях

**Проект**: codex_vimeo_fix
**Дата анализа**: 2026-04-05
**Статус**: Требует немедленного внимания

---

## Оглавление

1. [Критические проблемы безопасности](#критические-проблемы-безопасности)
2. [Проблемы высокой серьезности](#проблемы-высокой-серьезности)
3. [Проблемы средней серьезности](#проблемы-средней-серьезности)
4. [Статистика](#статистика)
5. [Срочные действия](#срочные-действия)

---

## Критические проблемы безопасности

### 1. 🔴 Открытые учетные данные в config.json

**Файл**: `config.json` (строки 2-12, 24-27)
**Серьезность**: КРИТИЧЕСКАЯ

#### Описание проблемы

Продакшн-креденшалы захардкожены непосредственно в основном конфигурационном файле, который находится в системе контроля версий:

- **Vimeo API token**: `b2f0ac71f0016aac1707a4946415c399`
- **Vimeo API secret**: `Zj40iPxurL3pR/i6cKXUvBj3oabe3tF+IYniIGjq59sHjUUPAd9Maf16uHIg2TawweOOCI1pLh4/2y8gMqT8ZU/plwdONvLfFKvLxi+K17ioScjv4q2F/5HbaUXSWq5M`
- **Vimeo client ID**: `2f50599f21d76450c218dcc3631a1752f9f05b8b`
- **Proxy password**: `XnglxoA4WDUv02wuMksvtA`
- **Telegram bot token**: `8663411286:AAEVovx8KDfbWjTvixlYIOi8Wk_auLPnh-U`
- **Telegram chat ID**: `-1003778922016`

#### Почему критично

- Креденшалы находятся в git истории и останутся там навсегда, даже если удалить их сейчас
- Любой с доступом к репозиторию получает полный доступ к:
  - Vimeo API для загрузки и манипуляций
  - Telegram боту для отправки сообщений
  - Прокси-серверу
- Если репозиторий когда-либо становился публичным или доступным третьим лицам, все ключи скомпрометированы

#### Потенциальное влияние

- Несанкционированное использование Vimeo API квоты
- Спам через Telegram бот
- Злоупотребление прокси-доступом
- Финансовые потери
- Нарушение конфиденциальности данных

---

### 2. 🔴 Path Traversal уязвимость при работе с файлами

**Файл**: `download_vimeo_seleniumbase_v3.py` (строка 1620)
**Серьезность**: КРИТИЧЕСКАЯ

#### Описание проблемы

Пути к файлам создаются из ID видео без должной валидации и санитизации:

```python
canonical_video_id = extract_canonical_video_id(video_id, json_data=json_data, result=result)
filename = f"{canonical_video_id}{ext}"
local_path = get_video_storage_dir(video_dir, canonical_video_id) / filename
```

Если `canonical_video_id` содержит последовательности обхода путей вроде `../../malicious` или `../../../etc/passwd`, файлы могут быть записаны за пределами предполагаемых директорий.

#### Почему критично

- Возможна запись произвольных файлов в любое место файловой системы
- Перезапись критических системных файлов
- Выполнение кода при перезаписи исполняемых файлов
- Обход любых ограничений безопасности на уровне директорий

#### Потенциальное влияние

- Полная компрометация системы
- Потеря данных
- Выполнение произвольного кода
- Эскалация привилегий

#### Связанные файлы

- `download_vimeo_seleniumbase_v3.py:548-553` - функция `extract_video_id()` без валидации
- `config_utils.py:9-13` - `resolve_path()` без проверки границ

---

### 3. 🔴 Race Condition при записи файлов состояния

**Файлы**:
- `run_assigned_shards.py` (строки 29-35)
- `download_vimeo_seleniumbase_v3.py` (строки 1786-1788)
- `scripts/offload_downloads.py` (строки 125-129)

**Серьезность**: КРИТИЧЕСКАЯ

#### Описание проблемы

По всему коду используется паттерн "атомарной" записи через временный файл, но без механизма блокировки:

```python
temp_path = path.with_suffix(path.suffix + ".tmp")
with open(temp_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2, ensure_ascii=False)
os.replace(temp_path, path)
```

**Сценарий гонки**:
1. Воркер A создает `resume_state.json.tmp` и начинает запись
2. Воркер B создает тот же `resume_state.json.tmp` (перезаписывает)
3. Воркер A завершает запись и вызывает `os.replace()`
4. Воркер B завершает запись и вызывает `os.replace()`
5. Данные от одного из воркеров потеряны

#### Почему критично

- Молчаливая потеря данных в критических файлах состояния
- Повреждение `resume_state.json` - потеря всего прогресса загрузки
- Повреждение файлов реестра - десинхронизация воркеров
- Проблема усугубляется при масштабировании (больше воркеров = выше вероятность)

#### Потенциальное влияние

- Потеря прогресса загрузки многих часов работы
- Повторная загрузка уже скачанных видео
- Неконсистентное состояние между воркерами
- Трудно диагностируемые проблемы в продакшене

#### Затронутые файлы состояния

- `resume_state.json`
- `registry.json`
- `coordinator_state.json`
- Различные `.tmp` файлы

---

### 4. 🔴 Потенциальная Command Injection в subprocess

**Файл**: `download_vimeo_seleniumbase_v3.py` (строки 617-651)
**Серьезность**: КРИТИЧЕСКАЯ

#### Описание проблемы

Команда curl строится с аргументами из конфигурации без должной валидации:

```python
cmd = [
    curl_binary,
    "--fail",
    "--location",
    "--max-time", str(download_timeout_seconds),
    "--speed-limit", "1024",
    "--speed-time", "30",
    "--retry", "3",
    "--retry-delay", "5",
    "--interface", download_interface,  # ❌ Из конфига, не валидируется
    "--output", str(part_path),         # ❌ Путь от пользовательского ввода
]
cmd.append(url)  # ❌ URL из внешнего источника
process = subprocess.Popen(cmd, ...)
```

#### Почему критично

Хотя используется list форма (не `shell=True`), что предотвращает классическую shell injection:

- `download_interface` из конфига может содержать специальные символы
- `part_path` может содержать path traversal последовательности
- `url` от внешнего источника может быть вредоносным
- Неправильная валидация путей позволяет записать файл куда угодно

#### Потенциальное влияние

- Запись произвольных файлов через `--output`
- Использование неожиданного сетевого интерфейса
- Загрузка с вредоносных URL
- Перезапись системных файлов

---

### 5. 🔴 Утечка паролей в логах и exception traces

**Файл**: `download_vimeo_seleniumbase_v3.py` (строки 402-409)
**Серьезность**: КРИТИЧЕСКАЯ

#### Описание проблемы

Креденшалы для входа извлекаются из переменных окружения и используются без должной защиты:

```python
def require_login_credentials(config):
    login_cfg = config.get("vimeo_login", {})
    email = (os.environ.get("VIMEO_EMAIL") or login_cfg.get("email") or "").strip()
    password = os.environ.get("VIMEO_PASSWORD") or login_cfg.get("password") or ""

    if not email or not password:
        raise RuntimeError("Missing vimeo_login credentials in config or environment")

    return email, password
```

Эти креденшалы затем передаются в `login_to_vimeo()` и могут появиться в:
- Exception stack traces
- Debug логах
- Error messages
- Selenium логах

#### Почему критично

- Логи часто хранятся в незашифрованном виде
- Логи доступны системным администраторам
- Логи могут отправляться в системы агрегации (ELK, Splunk)
- Exception traces могут быть отправлены в системы мониторинга
- Логи могут быть скопированы или украдены

#### Потенциальное влияние

- Компрометация учетных записей Vimeo
- Несанкционированный доступ к аккаунтам
- Утечка данных
- Нарушение политик безопасности

---

## Проблемы высокой серьезности

### 6. 🟠 Отсутствие обработки ошибок при запуске воркеров

**Файл**: `run_assigned_shards.py` (строки 828-833)
**Серьезность**: ВЫСОКАЯ

#### Описание проблемы

Subprocess воркеров запускается с полным отбрасыванием вывода:

```python
process = subprocess.Popen(
    [python_bin, str(worker_script), "--config", str(batch_config_path)],
    cwd=str(PROJECT_ROOT),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.STDOUT,
)
```

Если воркер не запускается или падает сразу после старта:
- Нет логов для диагностики
- Нет способа узнать причину сбоя
- Молчаливая деградация системы

#### Потенциальное влияние

- Невозможность диагностировать проблемы запуска
- Потеря времени на отладку
- Воркеры могут не работать, а координатор об этом не знает
- Сложности в продакшене

---

### 7. 🟠 Небезопасный парсинг JSON без логирования ошибок

**Файл**: `download_vimeo_seleniumbase_v3.py` (строка 1073)
**Серьезность**: ВЫСОКАЯ

#### Описание проблемы

```python
try:
    return json.loads(raw_payload)
except Exception:
    return None  # ❌ Молчаливо игнорирует ВСЕ исключения
```

Этот паттерн используется по всему коду для парсинга:
- API ответов от Vimeo
- Конфигурационных файлов
- Файлов состояния

#### Почему это проблема

- Скрывает синтаксические ошибки в JSON
- Скрывает проблемы с кодировкой
- Маскирует проблемы с API
- Возвращает `None` без объяснения причины
- Невозможно отладить проблемы

#### Потенциальное влияние

- Неожиданное поведение программы
- Трудно диагностируемые баги
- Молчаливые сбои обработки данных

---

### 8. 🟠 Telegram токен в URL конструкции

**Файл**: `telegram_notifier.py` (строка 212)
**Серьезность**: ВЫСОКАЯ

#### Описание проблемы

```python
url = f"{self.api_base_url}/bot{self.bot_token}/sendMessage"
```

Токен бота встраивается непосредственно в URL, который затем используется в HTTP запросах. Проблемы:

- URL могут логироваться в HTTP access logs
- URL могут попадать в прокси логи
- URL могут быть в debug выводе
- URL в exception traces

#### Потенциальное влияние

- Компрометация Telegram bot токена
- Несанкционированная отправка сообщений
- Спам через бот
- Необходимость ротации токена

---

### 9. 🟠 Потенциально бесконечный цикл загрузки

**Файл**: `download_vimeo_seleniumbase_v3.py` (строки 744-821)
**Серьезность**: ВЫСОКАЯ

#### Описание проблемы

Цикл загрузки с retry логикой:

```python
try:
    while True:
        with requests.get(...) as response:
            response.raise_for_status()
            # Логика загрузки
            break  # ✓ Выход только при успехе
except Exception as exc:
    if download_retry_interface:
        # ❌ Рекурсивный retry через curl
        return download_file_via_curl(...)
    raise
```

Если оба метода (requests и curl) постоянно получают восстанавливаемые ошибки:
- Цикл может работать бесконечно
- Накопление ресурсов
- Memory leaks
- CPU перегрузка

#### Потенциальное влияние

- Зависшие воркеры
- Исчерпание ресурсов
- Перегрузка системы
- Необходимость ручного вмешательства

---

### 10. 🟠 Пароли прокси в plaintext конфигурации

**Файл**: `config.json` (строки 7-12)
**Серьезность**: ВЫСОКАЯ

#### Описание проблемы

```json
"proxy": {
  "host": "185.239.0.98",
  "port": 12323,
  "username": "subject",
  "password": "XnglxoA4WDUv02wuMksvtA"
}
```

Креденшалы прокси хранятся в plaintext в конфигурационном файле.

#### Потенциальное влияние

- Компрометация прокси-доступа
- Несанкционированное использование
- Финансовые потери (если прокси платный)

---

## Проблемы средней серьезности

### 11. 🟡 Утечка файловых дескрипторов

**Файл**: `download_vimeo_seleniumbase_v3.py` (строки 646-677)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
process = subprocess.Popen(...)
# ... код который может бросить исключения ...
if process.stderr is not None:
    stderr_output = process.stderr.read().strip()
```

Если между созданием процесса и чтением stderr происходит исключение, файловый дескриптор может не закрыться.

#### Влияние

- Исчерпание файловых дескрипторов при многих ошибках
- Деградация производительности
- Eventual system instability

---

### 12. 🟡 Проблемы потокобезопасности в Telegram notifier

**Файл**: `telegram_notifier.py` (строки 262-276)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
self._sender_thread = threading.Thread(
    target=self._sender_loop,
    name="TelegramSenderThread",
    daemon=True,  # ❌ Daemon поток
)
```

Daemon потоки принудительно завершаются при выходе из программы, что означает:
- Сообщения в очереди могут быть потеряны
- Отправка может быть прервана посередине
- Нет graceful shutdown

#### Влияние

- Потеря уведомлений при завершении
- Неполная информация о статусе
- Трудно отследить проблемы

---

### 13. 🟡 Отсутствие валидации путей в конфигурации

**Файл**: `config_utils.py` (строки 9-13)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
def resolve_path(config_dir, value):
    path = Path(value)
    if path.is_absolute():
        return path  # ❌ Абсолютный путь без ограничений
    return (config_dir / path).resolve()
```

Абсолютные пути в конфигурации обходят любые ограничения на директории.

#### Влияние

- Конфиг может ссылаться на чувствительные системные файлы
- Обход sandbox ограничений
- Непредсказуемое поведение

---

### 14. 🟡 Недостаточная валидация Video ID

**Файл**: `download_vimeo_seleniumbase_v3.py` (строки 548-553)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
def extract_video_id(video_url):
    parsed = urlparse(video_url)
    path = parsed.path.rstrip("/")
    if not path:
        return video_url.rstrip("/").split("/")[-1]
    return path.split("/")[-1]  # ❌ Нет валидации формата
```

Не проверяется, что результат действительно похож на video ID:
- Могут быть специальные символы
- Path traversal последовательности
- Неожиданные значения

#### Влияние

- Неожиданное поведение при обработке
- Потенциальные проблемы безопасности
- Некорректные имена файлов

---

### 15. 🟡 Захардкоженные таймауты без возможности настройки

**Файл**: `download_vimeo_seleniumbase_v3.py` (строки 36-43)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
MIN_DELAY_BETWEEN_VIDEOS = 3
MAX_DELAY_BETWEEN_VIDEOS = 8
PAGE_LOAD_WAIT_MIN = 2
PAGE_LOAD_WAIT_MAX = 5
CLOUDFLARE_TIMEOUT_MIN = 40
CLOUDFLARE_TIMEOUT_MAX = 60
SCREENSHOT_TIMEOUT = 10
```

Все критичные таймауты захардкожены и не могут быть изменены без правки кода.

#### Влияние

- Негибкая настройка rate limiting
- Может триггерить анти-бот меры
- Невозможно адаптироваться под разные условия
- Необходимость редеплоя для изменения параметров

---

### 16. 🟡 Запутанная логика доступа к массиву

**Файл**: `run_assigned_shards.py` (строки 584-596)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
api_pool = master_config.get("workers", {}).get("api_pool") or []
if worker_index <= len(api_pool):
    api_creds = api_pool[worker_index - 1] or {}
```

Логика с `worker_index - 1` запутанная и подверженная ошибкам off-by-one.

#### Влияние

- Потенциальный IndexError
- Трудно поддерживаемый код
- Возможные баги при рефакторинге

---

### 17. 🟡 Избыточный catch Exception по всему коду

**Локации**: Множество мест по всему коду
**Серьезность**: СРЕДНЯЯ

#### Описание

Паттерн используется везде:

```python
except Exception as exc:
    logger.warning("Failed to read...")
```

Ловит ВСЕ исключения, включая:
- `KeyboardInterrupt`
- `SystemExit`
- Программные ошибки (AttributeError, TypeError)
- Баги в коде

#### Влияние

- Маскирует реальные баги
- Затрудняет отладку
- Может скрывать серьезные проблемы
- Нарушает принцип "fail fast"

---

### 18. 🟡 Отсутствие exponential backoff при retry

**Файл**: `telegram_notifier.py` (строки 221-257)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
for attempt in range(1, self.retry_attempts + 1):
    # ... попытка отправки ...
    if attempt < self.retry_attempts and self.retry_delay_seconds > 0:
        time.sleep(self.retry_delay_seconds)  # ❌ Фиксированная задержка
```

Использует фиксированную задержку между попытками вместо exponential backoff.

#### Влияние

- Может перегружать API во время проблем
- Риск rate limiting от Telegram
- Усугубление проблем вместо их решения
- Неэффективное использование ресурсов

---

### 19. 🟡 Небезопасные redirect'ы в HTTP запросах

**Файл**: `download_vimeo_seleniumbase_v3.py` (строки 745-750)
**Серьезность**: СРЕДНЯЯ

#### Описание

```python
with requests.get(
    url,
    stream=True,
    timeout=(connect_timeout, read_timeout),
    allow_redirects=True,  # ❌ Следует редиректам без проверки
    headers=request_headers,
) as response:
```

Следует HTTP redirect'ам без:
- Проверки финального URL
- Ограничения количества редиректов
- Валидации доменов

#### Влияние

- Может быть перенаправлен на вредоносные сайты
- Загрузка нежелательного контента
- SSRF уязвимости
- Bypass ограничений безопасности

---

### 20. 🟡 Потенциальная уязвимость в bash скрипте

**Файл**: `scripts/collect_system_metrics.sh` (строка 74)
**Серьезность**: СРЕДНЯЯ

#### Описание

```bash
df -B1 --output=used,avail "$DISK_PATH"
```

Использование переменной `DISK_PATH` в команде. Если переменная контролируется извне и содержит специальные символы, возможна injection.

#### Влияние

- Потенциальная command injection
- Выполнение произвольных команд
- Зависит от того, как устанавливается DISK_PATH

---

## Статистика

| Категория | Количество |
|-----------|------------|
| 🔴 Критические проблемы | 5 |
| 🟠 Высокая серьезность | 5 |
| 🟡 Средняя серьезность | 10 |
| **ВСЕГО** | **20** |

### Распределение по категориям

- **Безопасность**: 8 проблем (креденшалы, injection, path traversal)
- **Надежность**: 6 проблем (race conditions, error handling, resource leaks)
- **Архитектура**: 4 проблемы (threading, logging, retries)
- **Качество кода**: 2 проблемы (validation, hardcoded values)

---

## Срочные действия

### Немедленно (Сегодня)

1. **🚨 РОТИРОВАТЬ ВСЕ КРЕДЕНШАЛЫ**
   - Создать новые Vimeo API ключи
   - Создать новый Telegram bot
   - Сменить пароль прокси
   - Считать старые ключи скомпрометированными

2. **🚨 УДАЛИТЬ config.json ИЗ GIT**
   ```bash
   # Удалить из истории
   git filter-branch --force --index-filter \
     "git rm --cached --ignore-unmatch config.json" \
     --prune-empty --tag-name-filter cat -- --all

   # Добавить в .gitignore
   echo "config.json" >> .gitignore
   ```

3. **🚨 СОЗДАТЬ config.example.json** с placeholder значениями

### В течение недели

4. **Внедрить систему управления секретами**
   - Использовать переменные окружения
   - Или vault решение (HashiCorp Vault, AWS Secrets Manager)
   - Документировать процесс

5. **Исправить Path Traversal уязвимости**
   - Добавить валидацию video ID (regex: `^[0-9]+$`)
   - Санитизировать все пути
   - Добавить проверки границ директорий

6. **Реализовать file locking**
   - Использовать `fcntl.flock()` или `filelock` библиотеку
   - Защитить все операции записи в shared файлы

7. **Улучшить error handling**
   - Логировать все исключения
   - Собирать stderr от subprocess
   - Добавить structured logging

### В течение месяца

8. **Улучшить retry логику**
   - Exponential backoff с jitter
   - Максимальное количество попыток
   - Circuit breaker паттерн

9. **Добавить input validation**
   - Валидация всех URL
   - Проверка redirect destinations
   - Санитизация пользовательского ввода

10. **Code review и тесты**
    - Написать integration тесты
    - Security тесты
    - Load тесты для race conditions

---

## Рекомендации по архитектуре

### Управление секретами

```python
# ✅ Правильно
import os
from pathlib import Path

def load_secrets():
    """Load secrets from environment or secrets file"""
    secrets_file = Path.home() / ".config" / "vimeo_downloader" / "secrets.json"

    if secrets_file.exists():
        with open(secrets_file) as f:
            return json.load(f)

    return {
        "vimeo_token": os.environ["VIMEO_TOKEN"],
        "telegram_token": os.environ["TELEGRAM_TOKEN"],
        # ...
    }
```

### File locking

```python
# ✅ Правильно
import fcntl
import json

def atomic_json_write(path, data):
    """Atomically write JSON with file locking"""
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with open(temp_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_path, path)
```

### Path validation

```python
# ✅ Правильно
import re
from pathlib import Path

def safe_video_id(video_id: str) -> str:
    """Validate and sanitize video ID"""
    # Only allow digits
    if not re.match(r'^\d+$', video_id):
        raise ValueError(f"Invalid video ID format: {video_id}")
    return video_id

def safe_path(base_dir: Path, user_path: str) -> Path:
    """Ensure path stays within base directory"""
    full_path = (base_dir / user_path).resolve()

    # Check if resolved path is under base_dir
    if not str(full_path).startswith(str(base_dir.resolve())):
        raise ValueError(f"Path traversal detected: {user_path}")

    return full_path
```

### Exponential backoff

```python
# ✅ Правильно
import time
import random

def retry_with_backoff(func, max_attempts=5, base_delay=1):
    """Retry with exponential backoff and jitter"""
    for attempt in range(max_attempts):
        try:
            return func()
        except Exception as e:
            if attempt == max_attempts - 1:
                raise

            # Exponential backoff with jitter
            delay = base_delay * (2 ** attempt)
            jitter = random.uniform(0, delay * 0.1)
            time.sleep(delay + jitter)
```

---

## Заключение

Проект имеет **серьезные проблемы безопасности** требующие немедленного внимания. Основные риски:

1. **Утечка креденшалов** - уже произошла через git
2. **Path traversal** - может привести к RCE
3. **Race conditions** - теряет данные в продакшене
4. **Отсутствие валидации** - множественные векторы атак

**Кодовая база НЕ готова к продакшену** без исправления как минимум критических проблем.

### Приоритизация

**P0 (Блокирующие)**:
- Проблемы #1, #2, #3 - должны быть исправлены до любого деплоя

**P1 (Критические)**:
- Проблемы #4, #5 - исправить в течение недели

**P2 (Важные)**:
- Остальные проблемы - исправить по возможности

---

**Дата составления**: 2026-04-05
**Требует обновления после**: Исправления критических проблем
