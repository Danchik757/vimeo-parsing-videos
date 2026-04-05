#!/usr/bin/env python3
"""
Продвинутый тест undetected-chromedriver с дополнительными техниками
Использует профиль пользователя и stealth параметры
"""

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import random

def random_delay(min_sec=1, max_sec=3):
    """Случайная задержка для имитации человека"""
    time.sleep(random.uniform(min_sec, max_sec))

def test_with_advanced_uc():
    """
    Тест с продвинутыми настройками undetected-chromedriver
    """

    print("=" * 60)
    print("Advanced Undetected ChromeDriver Test")
    print("=" * 60)

    options = uc.ChromeOptions()

    # === STEALTH ПАРАМЕТРЫ ===

    # Отключить признаки автоматизации
    options.add_argument('--disable-blink-features=AutomationControlled')

    # Убрать "Chrome is being controlled by automated test software"
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Реалистичный User-Agent (Chrome 145 на macOS)
    user_agent = (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/145.0.0.0 Safari/537.36'
    )
    options.add_argument(f'--user-agent={user_agent}')

    # Параметры для обхода детекта
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-web-security')
    options.add_argument('--allow-running-insecure-content')

    # Язык и локаль
    options.add_argument('--lang=en-US')
    options.add_experimental_option('prefs', {
        'intl.accept_languages': 'en-US,en;q=0.9'
    })

    # Размер окна (реалистичный)
    options.add_argument('--window-size=1920,1080')

    # === ЗАПУСК ===

    print("\n🚀 Запуск Chrome с продвинутыми stealth настройками...")

    driver = uc.Chrome(
        options=options,
        version_main=145,
        use_subprocess=True,
        headless=False  # Видимый для теста
    )

    try:
        # Изменить navigator.webdriver на undefined (дополнительная защита)
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            '''
        })

        test_url = "https://vimeo.com/1071902981"

        print(f"\n🔍 Открываем: {test_url}")
        print("⏳ Имитация поведения человека...")

        # Открыть страницу
        driver.get(test_url)

        # Случайная задержка (имитация чтения)
        random_delay(3, 5)

        # Получить информацию о странице
        title = driver.title
        page_source = driver.page_source
        page_size = len(page_source)

        print(f"\n📄 Title: {title}")
        print(f"📏 Размер HTML: {page_size:,} байт")

        # Проверка Cloudflare
        is_cloudflare = (
            "cloudflare" in page_source.lower() or
            "turnstile" in page_source.lower() or
            "verify" in page_source.lower()
        )

        if is_cloudflare or page_size < 20000:
            print("\n⚠️ Cloudflare Turnstile обнаружен!")
            print("⏳ Ожидание автоматического прохождения...")

            # Имитация движений мыши (WebDriver может это делать)
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                actions = ActionChains(driver)

                # Случайные движения
                for i in range(3):
                    x = random.randint(100, 500)
                    y = random.randint(100, 500)
                    actions.move_by_offset(x, y)
                    actions.perform()
                    time.sleep(random.uniform(0.5, 1.5))
                    # Вернуть к начальной позиции
                    actions.move_by_offset(-x, -y)
                    actions.perform()

                print("🖱️ Имитация движений мыши выполнена")
            except Exception as e:
                print(f"⚠️ Не удалось имитировать мышь: {e}")

            # Подождать
            print("⏳ Ожидание 25 секунд...")
            time.sleep(25)

            # Обновить данные
            page_source = driver.page_source
            page_size = len(page_source)

            print(f"\n📏 Размер после ожидания: {page_size:,} байт")

            if page_size > 50000:
                print("✅ Похоже, Cloudflare пройден!")
            else:
                print("❌ Cloudflare всё ещё активен")
                print("\n💡 Возможные причины:")
                print("  - IP в blacklist Cloudflare")
                print("  - Требуется ручное решение капчи")
                print("  - Нужен ротирующий прокси")

        else:
            print("\n✅ Страница загружена успешно!")
            print("🎯 Cloudflare не обнаружен.")

        # Скриншот
        driver.save_screenshot("output/uc_advanced_test.png")
        print(f"\n📸 Скриншот: output/uc_advanced_test.png")

        # Сохранить HTML
        with open("output/uc_advanced_page.html", "w", encoding="utf-8") as f:
            f.write(page_source)
        print(f"💾 HTML: output/uc_advanced_page.html")

        # Оставить открытым
        print("\n⏸ Браузер открыт 15 секунд...")
        time.sleep(15)

    finally:
        driver.quit()
        print("\n✅ Тест завершён, браузер закрыт")

if __name__ == "__main__":
    try:
        test_with_advanced_uc()
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
