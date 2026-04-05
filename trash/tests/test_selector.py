#!/usr/bin/env python3
"""Тестовый скрипт для поиска селектора кнопки Download"""

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
import time
import json

# Загружаем конфиг
with open('config.json', 'r') as f:
    config = json.load(f)

# Создаём браузер БЕЗ прокси для теста
chrome_options = uc.ChromeOptions()
chrome_options.headless = False
chrome_options.add_argument('--ignore-certificate-errors')
# Прокси с авторизацией не работает напрямую в Chrome
# chrome_options.add_argument(f'--proxy-server=http://Cf0rm2:y4XUAq@45.11.125.162:9824')

driver = uc.Chrome(options=chrome_options, version_main=145, use_subprocess=True)

try:
    # Открываем видео которое можно скачать
    test_url = "https://vimeo.com/1071902981"
    print(f"Открываю {test_url}...")
    driver.get(test_url)

    # Ждём загрузки страницы и JavaScript
    print("Жду загрузки страницы (15 секунд)...")
    time.sleep(15)

    print("\n=== Поиск всех кнопок на странице ===")

    # Ищем все кнопки
    buttons = driver.find_elements(By.TAG_NAME, "button")
    print(f"Найдено кнопок: {len(buttons)}")

    for i, btn in enumerate(buttons):
        try:
            text = btn.text[:50] if btn.text else ""
            aria_label = btn.get_attribute("aria-label") or ""
            btn_class = btn.get_attribute("class") or ""

            # Ищем кнопки связанные со скачиванием
            if any(word in text.lower() for word in ['download', 'загруз', 'скач']) or \
               any(word in aria_label.lower() for word in ['download', 'загруз']) or \
               any(word in btn_class.lower() for word in ['download']):
                print(f"\n[{i}] НАЙДЕНА DOWNLOAD КНОПКА:")
                print(f"  Текст: {text}")
                print(f"  aria-label: {aria_label}")
                print(f"  class: {btn_class[:100]}")
        except:
            pass

    # Ищем все ссылки
    print("\n=== Поиск всех ссылок со словом Download ===")
    links = driver.find_elements(By.TAG_NAME, "a")
    print(f"Найдено ссылок: {len(links)}")

    for i, link in enumerate(links):
        try:
            text = link.text[:50] if link.text else ""
            href = link.get_attribute("href") or ""

            if 'download' in text.lower() or 'download' in href.lower():
                print(f"\n[{i}] НАЙДЕНА DOWNLOAD ССЫЛКА:")
                print(f"  Текст: {text}")
                print(f"  href: {href[:100]}")
        except:
            pass

    # Ищем по тексту "Download"
    print("\n=== Поиск по XPATH с текстом Download ===")
    try:
        download_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Download') or contains(text(), 'download')]")
        print(f"Найдено элементов: {len(download_elements)}")

        for i, elem in enumerate(download_elements[:10]):  # Первые 10
            try:
                print(f"\n[{i}] {elem.tag_name}: {elem.text[:50]}")
                print(f"  class: {elem.get_attribute('class')}")
            except:
                pass
    except Exception as e:
        print(f"Ошибка XPATH: {e}")

    print("\n\n=== Сохраняю HTML страницы ===")
    with open('page_source.html', 'w', encoding='utf-8') as f:
        f.write(driver.page_source)
    print("Сохранено в page_source.html")

    print("\n\nБраузер закроется через 3 секунды...")
    time.sleep(3)

except Exception as e:
    print(f"ОШИБКА: {e}")
    import traceback
    traceback.print_exc()

finally:
    driver.quit()
