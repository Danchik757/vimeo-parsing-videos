# Тесты обхода Cloudflare Turnstile

**⚠️ ВАЖНО:** Эти скрипты созданы исключительно в **образовательных целях** для понимания работы систем защиты от ботов.

## Описание проблемы

При попытке доступа к Vimeo через скрипт, Cloudflare показывает капчу:
```
To continue, please confirm that you're a human (and not a spambot).
vimeo.com needs to review the security of your connection before proceeding.
```

Это **Cloudflare Turnstile** - система защиты, которая:
- Проверяет TLS отпечатки
- Анализирует поведение браузера
- Проверяет IP reputation
- Детектирует признаки автоматизации

## Доступные тесты

### 1. `test_seleniumbase_uc.py` - SeleniumBase UC Mode

**Что делает:**
- Использует SeleniumBase с UC Mode (Undetected Chrome)
- Автоматически скрывает признаки Selenium
- Иногда может автоматически пройти Cloudflare Turnstile

**Вероятность успеха:** ~30-40% (зависит от IP reputation)

**Запуск:**
```bash
conda activate /mnt/ssd1/29d_kon/environments/vimeo-downloader
cd /mnt/ssd1/29d_kon/projects/CODECS/Parsing

# Установить SeleniumBase (если еще не установлен)
pip install seleniumbase

# Запустить тест
python tests/test_seleniumbase_uc.py
```

### 2. `test_uc_advanced.py` - Продвинутый Undetected ChromeDriver

**Что делает:**
- Использует undetected-chromedriver с дополнительными stealth техниками
- Изменяет navigator.webdriver на undefined
- Имитирует движения мыши
- Использует реалистичный User-Agent
- Добавляет случайные задержки

**Вероятность успеха:** ~20-30% (зависит от IP reputation)

**Запуск:**
```bash
conda activate /mnt/ssd1/29d_kon/environments/vimeo-downloader
cd /mnt/ssd1/29d_kon/projects/CODECS/Parsing

# Запустить тест (undetected-chromedriver уже установлен)
python tests/test_uc_advanced.py
```

## Что проверить после запуска

1. **Размер HTML:**
   - < 15,000 байт = Cloudflare блокирует
   - > 50,000 байт = Страница загружена успешно

2. **Скриншоты:**
   - Проверьте файлы в `output/`:
     - `seleniumbase_test.png`
     - `uc_advanced_test.png`

3. **Сохранённый HTML:**
   - `output/seleniumbase_page.html`
   - `output/uc_advanced_page.html`
   - Поищите текст "Verify to continue"

## Интерпретация результатов

### ✅ Успех (капча пройдена)
```
📏 Размер HTML: 85,432 байт
✅ Страница загружена успешно!
```
→ Можно использовать этот метод в `download_vimeo.py`

### ❌ Неудача (капча не пройдена)
```
📏 Размер HTML: 11,315 байт
⚠️ Cloudflare Turnstile обнаружен!
❌ Cloudflare всё ещё активен
```
→ IP в blacklist или требуется другой подход

### ⏳ Частичный успех
```
📏 Размер HTML: 35,000 байт
```
→ Попробуйте подождать дольше или повторить попытку

## Если ничего не работает

### Вариант 1: Подождать
- Cloudflare reputation восстанавливается через 24-48 часов
- Не делайте запросы к Vimeo в это время

### Вариант 2: Другой IP
- Используйте VPN или другой сервер
- Убедитесь что новый IP не использовался для скрейпинга Vimeo

### Вариант 3: Ротирующие прокси
- Residential proxies (Smartproxy, Bright Data, IPRoyal)
- Стоимость: $75-500/месяц
- Эффективность: ~90%+

### Вариант 4: Сервисы решения капч
- 2captcha.com
- anti-captcha.com
- capsolver.com
- Стоимость: ~$3 за 1000 решений
- Для 694,343 видео: ~$2000

## Технические детали

### Что проверяет Cloudflare Turnstile:

1. **TLS Fingerprint:**
   - Версия TLS
   - Cipher suites
   - Extensions order

2. **JavaScript проверки:**
   - `navigator.webdriver` (должен быть undefined)
   - Canvas fingerprint
   - WebGL fingerprint
   - Шрифты системы

3. **Поведенческий анализ:**
   - Движения мыши
   - Скорость загрузки страниц
   - Паттерны кликов

4. **IP Reputation:**
   - История запросов с IP
   - Datacenter vs Residential
   - Geolocation

### Почему обход сложен:

- Cloudflare использует машинное обучение
- Проверки происходят на уровне CDN (до загрузки страницы)
- Даже самые продвинутые инструменты дают успех ~40%
- Без чистого IP вероятность прохода минимальна

## Легальность

⚠️ **Важно:**
- Эти тесты для образовательных целей
- Массовый обход капч может нарушать ToS Vimeo
- Используйте только для личных исследований
- Для production используйте официальные API

## Следующие шаги

Если тесты успешны:
1. Интегрировать метод в `download_vimeo.py`
2. Добавить проверку Cloudflare в основной код
3. Настроить задержки между запросами
4. Использовать консервативные лимиты (5-10 видео/час)

Если тесты неуспешны:
1. Подождать 24-48 часов
2. Попробовать другой IP
3. Рассмотреть использование residential proxies
