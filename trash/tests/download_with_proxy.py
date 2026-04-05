#!/usr/bin/env python3
"""
Версия download_vimeo.py С ПРОКСИ через расширение Chrome
Использовать когда прокси заработает
"""

import undetected_chromedriver as uc
import os

def create_driver_with_proxy():
    """Создаёт Chrome драйвер с прокси через расширение"""
    chrome_options = uc.ChromeOptions()
    chrome_options.headless = False
    chrome_options.add_argument('--ignore-certificate-errors')

    # Отключаем загрузку изображений
    prefs = {
        "profile.managed_default_content_settings.images": 2
    }
    chrome_options.add_experimental_option("prefs", prefs)

    # Загружаем расширение для прокси
    extension_path = os.path.abspath("chrome_proxy_extension")
    chrome_options.add_argument(f'--load-extension={extension_path}')

    driver = uc.Chrome(options=chrome_options, version_main=145, use_subprocess=True)
    print(f"✓ Chrome запущен с прокси расширением")

    return driver

if __name__ == "__main__":
    print("Тест Chrome с прокси расширением")
    print("-" * 60)

    try:
        driver = create_driver_with_proxy()

        # Проверяем IP
        print("\nПроверка IP через прокси...")
        driver.get("https://api.ipify.org?format=json")

        import time
        time.sleep(3)

        body = driver.find_element("tag name", "body").text
        print(f"IP через прокси: {body}")

        print("\n✓ Прокси работает через расширение!")

        time.sleep(3)
        driver.quit()

    except Exception as e:
        print(f"\n✗ Ошибка: {e}")
        print("\nЕсли прокси не работает - используйте download_vimeo.py без прокси")
