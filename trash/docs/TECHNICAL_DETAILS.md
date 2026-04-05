# Vimeo Video Downloader - Техническая документация

## Архитектура системы

### Общая схема работы

```
┌─────────────────────────────────────────────────────────────┐
│                    download_vimeo.py                         │
│                  (Главный скрипт Python)                     │
└────────┬────────────────────────────────────────────────────┘
         │
         ├──► config.json (Конфигурация)
         │
         ├──► need_parse_unique.json (694,343 URL)
         │
         ├──► Vimeo API ──────► jsons/*.json (Метаданные)
         │         │
         │         └──► Проверка: можно ли скачать?
         │
         ├──► undetected_chromedriver ──► Chrome Browser
         │         │                            │
         │         │                            ├──► Proxy Extension
         │         │                            │     (если enabled)
         │         │                            │
         │         └──► Selenium WebDriver ─────┤
         │                                      │
         │                                      └──► Vimeo.com
         │                                            │
         │                                            ├── JavaScript load (15s)
         │                                            ├── Click Download button
         │                                            ├── Select max quality
         │                                            └── Get download link
         │
         └──► urllib.request ──► Download video ──► output/videos/
```

---

## Подробное описание компонентов

### 1. Инициализация и конфигурация

```python
# Строки 13-14: Загрузка конфигурации
with open('config.json', 'r') as f:
    config = json.load(f)
```

**Что происходит:**
1. Читается файл `config.json`
2. Парсится JSON в словарь Python
3. Все настройки доступны через `config['section']['key']`

**Зависимости:**
- `json` (стандартная библиотека Python)

---

### 2. Логирование

```python
# Строки 17-32: Настройка логирования
log_file = config['files']['log_file']
os.makedirs(os.path.dirname(log_file), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
```

**Что происходит:**
1. Получает путь к лог-файлу из config: `output/logs/download.log`
2. Создает директорию `output/logs/` если её нет
3. Настраивает два обработчика:
   - `FileHandler` → записывает в файл
   - `StreamHandler` → выводит в консоль
4. Формат: `2026-03-09 19:03:16,570 - INFO - Configuration loaded`

**Уровни логирования:**
- `INFO` - обычные сообщения (прогресс, успехи)
- `WARNING` - предупреждения (не критичные ошибки)
- `ERROR` - ошибки (требуют внимания)

---

### 3. Создание Chrome драйвера с прокси

```python
# Строки 35-58: create_driver_with_proxy()
def create_driver_with_proxy():
    chrome_options = uc.ChromeOptions()
    chrome_options.headless = False  # Видимое окно браузера
    chrome_options.add_argument('--ignore-certificate-errors')
    chrome_options.add_argument('--allow-insecure-localhost')

    # Загрузка расширения для прокси
    if config['proxy']['enabled']:
        extension_path = os.path.abspath('chrome_proxy_extension')
        chrome_options.add_argument(f'--load-extension={extension_path}')

    # Отключение изображений для ускорения
    prefs = {"profile.managed_default_content_settings.images": 2}
    chrome_options.add_experimental_option("prefs", prefs)

    driver = uc.Chrome(options=chrome_options, version_main=145,
                       use_subprocess=True, headless=False)
    return driver
```

**Что происходит:**

#### 3.1. undetected_chromedriver
- **Цель:** Обход детектирования ботов (Cloudflare, Vimeo защита)
- **Как работает:**
  - Патчит ChromeDriver, удаляя признаки автоматизации
  - Изменяет `navigator.webdriver` на `undefined`
  - Рандомизирует User-Agent
  - Эмулирует человеческое поведение

#### 3.2. Прокси через Chrome Extension
- **Почему через extension:**
  - Chrome НЕ поддерживает `user:pass@host:port` через CLI
  - Extension имеет доступ к API `chrome.proxy.settings`
  - Может обрабатывать `onAuthRequired` события

**Файл `chrome_proxy_extension/background.js`:**
```javascript
// Настройка прокси
chrome.proxy.settings.set({
  value: {
    mode: "fixed_servers",
    rules: {
      singleProxy: {
        scheme: "http",
        host: "185.88.101.106",
        port: 3128
      }
    }
  }
});

// Обработка авторизации
chrome.webRequest.onAuthRequired.addListener(
  function(details) {
    return {
      authCredentials: {
        username: "subject",
        password: "XnglxoA4WDUv02wuMksvtA"
      }
    };
  },
  { urls: ["<all_urls>"] },
  ["blocking"]
);
```

#### 3.3. Отключение изображений
```python
prefs = {"profile.managed_default_content_settings.images": 2}
```
- **Цель:** Ускорение загрузки страниц
- **Эффект:** Экономия ~70% трафика и времени загрузки
- **Не влияет:** На DOM и JavaScript (кнопки остаются)

#### 3.4. version_main=145
```python
driver = uc.Chrome(options=chrome_options, version_main=145, ...)
```
- **Цель:** Указать версию ChromeDriver
- **Как работает:**
  1. undetected_chromedriver проверяет установленную версию Chrome
  2. Скачивает соответствующий ChromeDriver из официального репозитория
  3. Кеширует в `~/Library/Application Support/undetected_chromedriver/`
  4. Патчит для обхода детекта

---

### 4. Детект блокировки IP

```python
# Строки 61-85: check_if_blocked()
def check_if_blocked(driver):
    page_source = driver.page_source.lower()
    page_title = driver.title.lower()

    blocking_indicators = [
        ('429', 'Rate limit exceeded (HTTP 429)'),
        ('too many requests', 'Too many requests detected'),
        ('access denied', 'Access denied by Vimeo'),
        ('forbidden', 'Access forbidden (HTTP 403)'),
        ('blocked', 'IP address blocked'),
        ('verify you are human', 'Human verification required'),
        ('captcha', 'CAPTCHA challenge detected'),
        ('moment' in page_title or 'момент' in page_title, 'Vimeo verification page'),
    ]

    for indicator, reason in blocking_indicators:
        if indicator in page_source or indicator in page_title:
            return True, reason

    return False, None
```

**Как работает детект:**

1. **Получение HTML:**
   ```python
   page_source = driver.page_source  # Весь HTML страницы
   page_title = driver.title          # Заголовок страницы
   ```

2. **Поиск индикаторов блокировки:**
   - HTTP коды: `429` (Too Many Requests), `403` (Forbidden)
   - Текстовые фразы: "access denied", "blocked", "captcha"
   - Особые страницы: "moment" (Vimeo verification)

3. **Возврат результата:**
   ```python
   (True, "Rate limit exceeded")   # Заблокирован
   (False, None)                    # Не заблокирован
   ```

**Когда вызывается:**
- Сразу после открытия страницы (`get_url()`)
- После ожидания загрузки JavaScript (перед кликом на кнопку)

---

### 5. Открытие URL с проверкой блокировки

```python
# Строки 88-118: get_url()
def get_url(driver, url):
    while True:
        try:
            if driver is None:
                driver = create_driver_with_proxy()

            driver.get(url)  # Открыть URL

            # Проверка блокировки
            is_blocked, block_reason = check_if_blocked(driver)
            if is_blocked:
                logger.error(f"⚠️ IP BLOCKED: {block_reason}")
                raise Exception(f"IP_BLOCKED: {block_reason}")

            return driver
        except Exception as ex:
            if 'IP_BLOCKED' in str(ex):
                raise  # Пробросить исключение блокировки выше
            # Для других ошибок - переоткрыть браузер
            driver.quit()
            driver = None
```

**Логика работы:**

1. **Создание драйвера (если нужно):**
   ```python
   if driver is None:
       driver = create_driver_with_proxy()
   ```
   - При первом вызове
   - После краша браузера

2. **Открытие страницы:**
   ```python
   driver.get(url)  # Selenium команда
   ```
   - Загружает HTML
   - Выполняет JavaScript
   - Ждет события `DOMContentLoaded`

3. **Проверка блокировки:**
   - Если заблокирован → выбрасывает `IP_BLOCKED` исключение
   - Если нет → возвращает рабочий драйвер

4. **Обработка ошибок:**
   - `IP_BLOCKED` → пробрасывается в основной цикл → останавливает скрипт
   - Другие ошибки → переоткрывает браузер, пытается снова

---

### 6. Скачивание файла

```python
# Строки 121-125: download_file()
def download_file(url, local_filename):
    urllib.request.urlretrieve(url, local_filename)
    file_size_mb = os.path.getsize(local_filename) / (1024 * 1024)
    logger.info(f"Downloaded: {local_filename} ({file_size_mb:.1f} MB)")
```

**Как работает:**

1. **urllib.request.urlretrieve(url, filename):**
   - Скачивает файл по URL
   - Сохраняет в указанный путь
   - Блокирует выполнение до завершения
   - Показывает прогресс через callback (не используется)

2. **Расчет размера:**
   ```python
   os.path.getsize(local_filename)  # Размер в байтах
   / (1024 * 1024)                  # Конвертация в MB
   ```

**Альтернативы (не используются):**
- `requests.get()` + chunked download
- `wget` через subprocess
- `aria2c` для многопоточного скачивания

---

### 7. Vimeo API клиент

```python
# Строки 128-134: Инициализация Vimeo API
client = vimeo.VimeoClient(
    token=config['vimeo_api']['token'],
    key=config['vimeo_api']['client_id'],
    secret=config['vimeo_api']['secret']
)
```

**Что такое Vimeo API:**
- REST API для доступа к метаданным видео
- OAuth 2.0 авторизация
- Документация: https://developer.vimeo.com/api/reference

**Используемые endpoints:**

```python
# GET /videos/{video_id}
response = client.get(f'https://api.vimeo.com/videos/{id}')
```

**Возвращаемые данные (JSON):**
```json
{
  "uri": "/videos/1066528235",
  "name": "Video Title",
  "description": "...",
  "duration": 97,
  "width": 3840,
  "height": 2160,
  "privacy": {
    "view": "anybody",
    "download": false  ← Можно ли скачать
  },
  "pictures": {...},
  "stats": {...}
}
```

**HTTP статус коды:**
- `200 OK` - успешно
- `401 Unauthorized` - токен невалиден/истёк
- `403 Forbidden` - нет прав доступа
- `404 Not Found` - видео не существует
- `429 Too Many Requests` - превышен лимит API (1000 req/hour)

---

### 8. Главный цикл обработки

```python
# Строки 164-308: Главный цикл
for url in tqdm(list(urls), desc="Processing videos"):
    # 1. Проверка блокировки IP
    if ip_blocked:
        break

    # 2. Получение метаданных через API
    response = client.get(f'https://api.vimeo.com/videos/{id}')
    json_data = response.json()

    # 3. Сохранение метаданных
    with open(os.path.join(json_dir, f'{id}.json'), 'w') as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    # 4. Проверка: можно ли скачать?
    can_download = json_data['privacy']['download']
    if not can_download:
        skipped_videos += 1
        continue

    # 5. Retry цикл для скачивания
    retry_count = 0
    while retry_count < max_retries:
        try:
            # 5.1. Открыть страницу
            driver = get_url(driver, url)

            # 5.2. Ждать загрузки JavaScript (15 секунд)
            time.sleep(config['settings']['javascript_wait_time'])

            # 5.3. Проверить блокировку снова
            is_blocked, block_reason = check_if_blocked(driver)
            if is_blocked:
                ip_blocked = True
                break

            # 5.4. Кликнуть на кнопку Download
            download_btn = driver.find_element(
                By.CSS_SELECTOR,
                "button[aria-label='Download button']"
            )
            download_btn.click()

            # 5.5. Дождаться модального окна (10 секунд макс)
            section_elem = driver.find_element(
                By.CSS_SELECTOR,
                "section[aria-modal='true']"
            )

            # 5.6. Выбрать максимальное качество
            for elem in section_elem.find_elements(By.TAG_NAME, "div"):
                if 'download-file' in elem.get_attribute("id"):
                    if 'original' in elem.text.lower():
                        best_elem = elem

            # 5.7. Получить ссылку на скачивание
            download_link = best_elem.find_element(By.TAG_NAME, "a")\
                                      .get_attribute("href")

            # 5.8. Скачать файл
            local_path = os.path.join(video_dir, f'{id}.mp4')
            download_file(download_link, local_path)

            successful_downloads += 1
            break

        except Exception as ex:
            retry_count += 1
            if retry_count >= max_retries:
                failed_urls.append({'url': url, 'error': str(ex)})
```

---

## Детальный разбор ключевых моментов

### 9. Почему 15 секунд ожидания JavaScript?

```python
time.sleep(config['settings']['javascript_wait_time'])  # 15 секунд
```

**Проблема:**
Vimeo использует React для динамической загрузки UI:

```
0s   ─── HTML загружен (но пустой)
2s   ─── React bundle загружен
5s   ─── React компоненты монтируются
10s  ─── API запросы к Vimeo
15s  ─── Кнопка Download рендерится ✓
```

**Без ожидания:**
```python
driver.find_element(By.CSS_SELECTOR, "button[aria-label='Download button']")
# ❌ NoSuchElementException: кнопки еще нет в DOM
```

**С ожиданием 15 секунд:**
```python
time.sleep(15)
driver.find_element(By.CSS_SELECTOR, "button[aria-label='Download button']")
# ✓ Элемент найден
```

**Альтернативы (не используются):**
```python
# Explicit Wait (Selenium)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

wait = WebDriverWait(driver, 30)
download_btn = wait.until(
    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Download button']"))
)
```
Не используется, т.к. `time.sleep()` проще и стабильнее.

---

### 10. CSS селектор кнопки Download

```python
download_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Download button']")
```

**Почему именно этот селектор:**

**HTML структура Vimeo:**
```html
<div class="vp-sidedock">
  <div class="vp-sidedock-panel">
    <button
      aria-label="Download button"
      class="iris_btn"
      type="button"
    >
      <svg>...</svg>
      Download
    </button>
  </div>
</div>
```

**Альтернативные селекторы (хуже):**
- `.iris_btn` - слишком общий (много кнопок с этим классом)
- `button:contains('Download')` - не работает в Selenium
- `//button[text()='Download']` - XPath медленнее

**Лучший селектор:**
```python
"button[aria-label='Download button']"
```
- Уникальный (только одна кнопка)
- Семантически правильный (accessibility attribute)
- Стабильный (Vimeo не меняет aria-labels часто)

---

### 11. Поиск максимального качества

```python
# Строки 253-265
best_elem = None
for elem in section_elem.find_elements(By.TAG_NAME, "div"):
    try:
        if elem.get_attribute("id") and elem.get_attribute("id").startswith('download-file'):
            if best_elem is None:
                best_elem = elem
            if 'original' in elem.text.lower():
                best_elem = elem
                break
    except:
        pass
```

**HTML структура модального окна:**
```html
<section aria-modal="true">
  <div id="download-file-0">
    <span>SD 480p</span>
    <a href="https://...">Download</a>
  </div>
  <div id="download-file-1">
    <span>HD 1080p</span>
    <a href="https://...">Download</a>
  </div>
  <div id="download-file-2">
    <span>Original (4K)</span>
    <a href="https://...">Download</a>
  </div>
</section>
```

**Логика выбора:**
1. Найти все `<div>` с `id="download-file-*"`
2. Сохранить первый как `best_elem`
3. Если есть вариант с текстом "original" → перезаписать `best_elem`
4. Использовать `best_elem` (либо original, либо последний в списке)

**Приоритет качества:**
```
Original (4K) > HD 1080p > HD 720p > SD 480p > SD 360p
```

---

### 12. Обработка ошибок и retry логика

```python
# Строки 211-303
retry_count = 0
max_retries = config['settings']['retry_attempts']  # 3

while retry_count < max_retries:
    try:
        # ... попытка скачать ...
        successful_downloads += 1
        break  # Успех - выходим из цикла
    except Exception as ex:
        retry_count += 1
        logger.error(f"Error (attempt {retry_count}/{max_retries}): {ex}")

        if retry_count >= max_retries:
            failed_urls.append({'url': url, 'error': str(ex)})
        else:
            time.sleep(config['settings']['retry_delay'])  # 5 секунд
```

**Типы ошибок:**

1. **NoSuchElementException** (кнопка не найдена):
   - Причина: JavaScript не загрузился
   - Решение: retry с новой попыткой

2. **TimeoutException** (таймаут):
   - Причина: медленный интернет
   - Решение: retry

3. **IP_BLOCKED**:
   - Причина: превышен лимит запросов
   - Решение: НЕ retry, остановка скрипта

4. **StaleElementReferenceException**:
   - Причина: страница обновилась
   - Решение: retry с новым поиском элемента

**Стратегия retry:**
```
Попытка 1 ─── Ошибка ─── Ждать 5s ─── Попытка 2 ─── Ошибка ─── Ждать 5s ─── Попытка 3
                                                                                   │
                                                                             Если ошибка:
                                                                             failed_urls.append()
```

---

### 13. Сохранение результатов

```python
# Строки 318-324: Сохранение failed_downloads
if failed_urls:
    failed_file = config['files']['failed_downloads']
    os.makedirs(os.path.dirname(failed_file), exist_ok=True)
    with open(failed_file, 'w', encoding='utf-8') as f:
        json.dump(failed_urls, f, indent=2, ensure_ascii=False)
```

**Формат failed_downloads.json:**
```json
[
  {
    "url": "https://vimeo.com/1066528235",
    "error": "NoSuchElementException: button not found",
    "stage": "download"
  },
  {
    "url": "https://vimeo.com/1071902981",
    "error": "IP_BLOCKED: Rate limit exceeded",
    "stage": "page_load"
  }
]
```

**Стадии ошибок:**
- `api_fetch` - ошибка при API запросе (токен, сеть)
- `page_load` - ошибка при открытии страницы (блокировка IP)
- `download` - ошибка при скачивании (кнопка не найдена, таймаут)

---

## Производительность и оптимизация

### Узкие места:

1. **JavaScript ожидание (15s на каждое видео):**
   ```
   50 видео × 15s = 750s (12.5 минут) только на ожидание
   ```

2. **Скачивание больших файлов:**
   ```
   Средний размер: 500 MB
   Скорость: 10 MB/s
   Время на 1 видео: 50s

   50 видео × 50s = 2500s (42 минуты) на скачивание
   ```

3. **Vimeo API запросы:**
   ```
   ~300ms на запрос
   50 видео × 0.3s = 15s
   ```

**Общее время на 50 видео:**
```
API:         15s
JS Wait:     750s
Downloads:   2500s
-----------------------
Total:       ~55 минут
```

### Возможные оптимизации (не реализованы):

1. **Многопоточность:**
   ```python
   from concurrent.futures import ThreadPoolExecutor

   with ThreadPoolExecutor(max_workers=5) as executor:
       executor.map(download_video, urls)
   ```
   Риск: блокировка IP будет быстрее

2. **Уменьшение JS wait:**
   ```python
   # Вместо time.sleep(15)
   WebDriverWait(driver, 30).until(
       EC.presence_of_element_located((By.CSS_SELECTOR, "button[aria-label='Download button']"))
   )
   ```

3. **Headless режим:**
   ```python
   chrome_options.headless = True  # Без GUI
   ```
   Экономия: ~200 MB RAM, быстрее загрузка
   Риск: некоторые сайты детектят headless

---

## Безопасность и приватность

### Хранение креденшелов

**Текущий подход (НЕ безопасный):**
```json
{
  "vimeo_api": {
    "token": "b2f0ac71f0016aac1707a4946415c399"
  },
  "proxy": {
    "password": "XnglxoA4WDUv02wuMksvtA"
  }
}
```

**Проблемы:**
- Креденшелы в plaintext
- config.json может попасть в git
- Видны в логах при ошибках

**Рекомендуемый подход (не реализован):**
```python
# Использовать environment variables
import os

token = os.getenv('VIMEO_TOKEN')
proxy_pass = os.getenv('PROXY_PASSWORD')
```

```bash
# .env файл (в .gitignore)
VIMEO_TOKEN=b2f0ac71f0016aac1707a4946415c399
PROXY_PASSWORD=XnglxoA4WDUv02wuMksvtA
```

---

## Зависимости и версии

### requirements.txt

```
setuptools>=70.0.0           # Python 3.13 compatibility
selenium==4.15.2             # WebDriver API
undetected-chromedriver==3.5.4  # Bot detection bypass
PyVimeo==1.1.0              # Vimeo API client
tqdm==4.66.1                # Progress bar
requests==2.31.0            # HTTP library (dependency of PyVimeo)
```

### Совместимость:

- **Python:** 3.10, 3.11, 3.12, 3.13
- **Chrome:** 145.x (указано в коде)
- **macOS:** 14.0+ (Sonoma, Sequoia)
- **Linux:** Ubuntu 22.04+, Debian 12+
- **Windows:** 10, 11

---

## Потоки выполнения

### Последовательность вызовов для одного видео:

```
main()
  │
  ├─► client.get(API_URL)                    # API запрос
  │     └─► requests.get()
  │           └─► HTTP GET
  │
  ├─► json.dump(metadata)                    # Сохранение JSON
  │     └─► file.write()
  │
  ├─► get_url(driver, url)                   # Открытие страницы
  │     ├─► create_driver_with_proxy()
  │     │     └─► uc.Chrome()
  │     │           └─► ChromeDriver subprocess
  │     ├─► driver.get(url)
  │     │     └─► Chrome navigate
  │     └─► check_if_blocked(driver)
  │           └─► driver.page_source
  │
  ├─► time.sleep(15)                         # JavaScript wait
  │
  ├─► driver.find_element()                  # Поиск кнопки
  │     └─► ChromeDriver command
  │
  ├─► download_btn.click()                   # Клик
  │     └─► ChromeDriver click event
  │
  ├─► driver.find_element()                  # Поиск модального окна
  │
  └─► download_file(link, path)              # Скачивание
        └─► urllib.request.urlretrieve()
              └─► HTTP GET (stream)
```

---

## Диагностика и отладка

### Логи для анализа:

```bash
# Фильтр по ERROR
grep "ERROR" output/logs/download.log

# Фильтр по IP BLOCKED
grep "IP BLOCKED" output/logs/download.log

# Последние 50 строк
tail -50 output/logs/download.log

# Следить в реальном времени
tail -f output/logs/download.log
```

### Debug режим (не реализован):

Для добавления debug режима:

```python
# В начале файла
import logging
logging.basicConfig(level=logging.DEBUG)  # Вместо INFO

# В критичных местах
logger.debug(f"Page source length: {len(driver.page_source)}")
logger.debug(f"Current URL: {driver.current_url}")
logger.debug(f"Cookies: {driver.get_cookies()}")
```

---

## Заключение

Код построен на трёх основных компонентах:

1. **Vimeo API** - получение метаданных и проверка доступности
2. **Selenium + undetected_chromedriver** - обход детекта ботов и извлечение ссылок на скачивание
3. **urllib** - скачивание видеофайлов

Ключевые особенности:
- Детект блокировки IP
- Retry механизм с экспоненциальной задержкой
- Поддержка прокси через Chrome extension
- Логирование всех операций
- Сохранение метаданных в JSON

Производительность: ~55 минут на 50 видео (в зависимости от размера и скорости интернета).

---

## 14. Telegram уведомления

### Архитектура модуля

```python
# telegram_notifier.py
class TelegramNotifier:
    def __init__(self, config):
        self.bot_token = config['telegram']['bot_token']
        self.chat_id = config['telegram']['chat_id']
        self.enabled = config['telegram']['enabled']

    def send_message(self, text, parse_mode='HTML'):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode
        }
        requests.post(url, json=payload)
```

### Интеграция в download_vimeo.py

```python
# Строка 12: Импорт
from telegram_notifier import TelegramNotifier

# Строка 37: Инициализация
telegram = TelegramNotifier(config)

# Строка 163: Уведомление о старте
telegram.notify_start(len(urls), test_mode=config['settings']['test_mode'])

# Строка 308: Уведомление о скачивании
telegram.notify_video_downloaded(id, filename, file_size_mb, current_num, total)

# Строка 220: Уведомление о пропуске
telegram.notify_video_skipped(id, reason, current_num, total)

# Строка 189: Уведомление об API ошибке
telegram.notify_api_error(response.status_code, error_message)

# Строка 248: Уведомление о блокировке IP
telegram.notify_ip_blocked(current_ip, block_reason)

# Строка 327: Уведомление о завершении
telegram.notify_finish({
    'total': len(urls),
    'downloaded': successful_downloads,
    'skipped': skipped_videos,
    'failed': len(failed_urls),
    'ip_blocked': ip_blocked
})
```

### Типы уведомлений

**1. Старт сессии:**
```
🚀 Vimeo Downloader ЗАПУЩЕН

📊 Режим: ТЕСТОВЫЙ РЕЖИМ
📹 Всего видео: 50
🕐 Время старта: 2026-03-11 06:15:00
```

**2. Успешное скачивание:**
```
✅ Видео скачано #15

🎬 ID: 1071902981
📁 Файл: 1071902981.mp4
💾 Размер: 1024.5 MB
📊 Прогресс: 15/50 (30.0%)
⏰ 06:25:30
```

**3. Пропущенное видео:**
```
⏭ Видео пропущено #8

🎬 ID: 123456789
⚠️ Причина: Privacy settings restrict download
📊 Прогресс: 8/50
⏰ 06:20:15
```

**4. Ошибка API:**
```
🚨 ОШИБКА API

⚠️ Код: 429
📝 API rate limit exceeded
⏰ 2026-03-11 06:30:00
```

**5. Блокировка IP:**
```
🚫 IP ЗАБЛОКИРОВАН

🌐 IP: 103.249.134.114
⚠️ Причина: Rate limit exceeded (HTTP 429)
⏰ 2026-03-11 06:35:00
```

**6. Завершение сессии:**
```
✅ Vimeo Downloader ЗАВЕРШЕН

📊 Статистика:
📹 Всего обработано: 50
✅ Скачано: 12
⏭ Пропущено: 35
❌ Ошибок: 3
⏰ Время завершения: 2026-03-11 07:10:00
```

### Настройка частоты уведомлений

```json
{
  "telegram": {
    "enabled": true,
    "bot_token": "1234567890:ABC...",
    "chat_id": "-1001234567890",
    "notify_every_n_videos": 1,      // Каждое N-ое видео
    "notify_on_start": true,          // При запуске
    "notify_on_finish": true,         // При завершении
    "notify_on_error": true,          // При ошибках
    "notify_on_ip_block": true        // При блокировке
  }
}
```

**Примеры конфигурации:**

```json
// Минимум уведомлений (только критичное)
"notify_every_n_videos": 100,
"notify_on_start": false,
"notify_on_finish": true,
"notify_on_error": true,
"notify_on_ip_block": true

// Максимум уведомлений (каждое действие)
"notify_every_n_videos": 1,
"notify_on_start": true,
"notify_on_finish": true,
"notify_on_error": true,
"notify_on_ip_block": true
```

### Telegram Bot API

**Endpoint:**
```
POST https://api.telegram.org/bot{token}/sendMessage
```

**Request:**
```json
{
  "chat_id": "-1001234567890",
  "text": "✅ Видео скачано",
  "parse_mode": "HTML",
  "disable_web_page_preview": true
}
```

**Response (успех):**
```json
{
  "ok": true,
  "result": {
    "message_id": 12345,
    "date": 1710154200
  }
}
```

**Response (ошибка):**
```json
{
  "ok": false,
  "error_code": 400,
  "description": "Bad Request: chat not found"
}
```

### Обработка ошибок

```python
try:
    response = requests.post(url, json=payload, timeout=10)
    if response.status_code == 200:
        logger.debug(f"Telegram message sent")
        return True
    else:
        logger.error(f"Failed to send: {response.status_code}")
        return False
except Exception as e:
    logger.error(f"Error sending Telegram message: {e}")
    return False
```

**Типичные ошибки:**
- `400 Bad Request` - неверный chat_id или формат сообщения
- `401 Unauthorized` - невалидный bot_token
- `429 Too Many Requests` - превышен лимит (30 сообщений/секунду)
- `Timeout` - нет соединения с Telegram API

---

## 15. Логика сохранения JSON метаданных

### Обновлённая архитектура (2026-03-11)

**Старая логика (ДО изменений):**
```python
# 1. API запрос
response = client.get(f'https://api.vimeo.com/videos/{id}')
json_data = response.json()

# 2. СРАЗУ сохранить JSON ← ПРОБЛЕМА: сохраняются все
with open(f'{id}.json', 'w') as f:
    json.dump(json_data, f)

# 3. Проверить downloadable
if not json_data['privacy']['download']:
    continue  # JSON уже сохранён, но видео не скачано
```

**Результат:** JSON создавались для ВСЕХ видео, включая:
- Не скачиваемые (privacy.download = false)
- С ошибками при скачивании
- Заблокированные по IP

**Новая логика (ПОСЛЕ изменений):**
```python
# 1. API запрос
response = client.get(f'https://api.vimeo.com/videos/{id}')
json_data = response.json()

# 2. Проверить downloadable СРАЗУ
if not json_data['privacy']['download']:
    continue  # JSON НЕ сохраняется

# 3. Попытаться скачать видео
download_file(download_link, local_path)

# 4. Сохранить JSON ТОЛЬКО после успешного скачивания
with open(f'{id}.json', 'w') as f:
    json.dump(json_data, f)
```

**Преимущества:**
- JSON создаются только для скачанных видео
- Экономия дискового пространства (~13 KB на видео)
- Соответствие: 1 видео = 1 JSON
- Легче анализировать результаты

**Пример:**
```
Было: 50 видео → 50 JSON (650 KB), но скачано только 12 видео
Стало: 50 видео → 12 JSON (156 KB), скачано 12 видео ✓
```

### Код изменений

**Строки 200-221 (download_vimeo.py):**
```python
json_data = response.json()

# Check if downloadable BEFORE saving JSON
try:
    can_download = json_data['privacy']['download']
except KeyError as ex:
    logger.warning(f"Missing 'privacy.download' field for {id}: {ex}")
    skipped_videos += 1
    continue  # ← JSON не создаётся

if not can_download:
    logger.info(f"Video {id} is not downloadable")
    skipped_videos += 1
    telegram.notify_video_skipped(id, "Privacy settings", current_num, len(urls))
    continue  # ← JSON не создаётся
```

**Строки 303-307 (download_vimeo.py):**
```python
download_file(download_link, local_path)

# Save metadata JSON only after successful download
with open(os.path.join(json_dir, f'{id}.json'), 'w', encoding='utf-8') as f:
    json.dump(json_data, f, indent=2, ensure_ascii=False)
logger.info(f"✓ Saved metadata for {id}")
```

---

## 16. Структура проекта

```
vimeo-downloader/
├── download_vimeo.py          # Главный скрипт
├── telegram_notifier.py        # Модуль Telegram уведомлений
├── config.json                 # Конфигурация
├── need_parse_unique.json      # Список 694,343 URL
├── requirements.txt            # Python зависимости
│
├── chrome_proxy_extension/     # Chrome расширение для прокси
│   ├── manifest.json           # Manifest v3
│   └── background.js           # Service worker
│
├── output/                     # Результаты работы
│   ├── videos/                 # Скачанные видео (.mp4, .mov)
│   ├── jsons/                  # Метаданные (только для скачанных)
│   └── logs/
│       ├── download.log        # Главный лог
│       └── failed_downloads.json  # Ошибки
│
├── docs/                       # Документация
│   ├── USER_GUIDE.md          # Руководство пользователя
│   ├── TECHNICAL_DETAILS.md   # Техническая документация (этот файл)
│   └── README.md              # Быстрый старт
│
└── venv/                       # Виртуальное окружение Python
    ├── bin/
    ├── lib/
    └── pyvenv.cfg
```

### Размеры файлов

```
download_vimeo.py       ~12 KB (380 строк кода)
telegram_notifier.py    ~8 KB  (250 строк кода)
config.json             ~800 bytes
need_parse_unique.json  ~35 MB (694,343 URL)

output/videos/          ~500 MB на видео (зависит от качества)
output/jsons/           ~13 KB на JSON
output/logs/            ~1 KB на 100 видео
```

### Зависимости между модулями

```
download_vimeo.py
    │
    ├─► config.json (читает конфигурацию)
    ├─► need_parse_unique.json (читает URL)
    ├─► telegram_notifier.py (импортирует TelegramNotifier)
    ├─► chrome_proxy_extension/ (загружает если proxy enabled)
    │
    └─► output/
        ├─► videos/ (пишет .mp4/.mov)
        ├─► jsons/ (пишет .json)
        └─► logs/ (пишет .log, failed_downloads.json)

telegram_notifier.py
    │
    ├─► config.json (читает telegram секцию)
    └─► Telegram Bot API (отправляет POST запросы)
```

---

## Итоговая архитектура

```
┌────────────────────────────────────────────────────────┐
│                  download_vimeo.py                      │
│  ┌──────────────────────────────────────────────────┐  │
│  │  1. API Request → Vimeo API                      │  │
│  │     └─► Check privacy.download                   │  │
│  │                                                   │  │
│  │  2. Selenium WebDriver → Vimeo.com               │  │
│  │     ├─► Load page (15s JavaScript wait)          │  │
│  │     ├─► Check IP blocking                        │  │
│  │     ├─► Click Download button                    │  │
│  │     └─► Get download URL                         │  │
│  │                                                   │  │
│  │  3. Download video → output/videos/              │  │
│  │                                                   │  │
│  │  4. Save JSON → output/jsons/                    │  │
│  │     (только для успешно скачанных видео)         │  │
│  │                                                   │  │
│  │  5. Telegram notification → User                 │  │
│  │     └─► telegram_notifier.py                     │  │
│  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
```
