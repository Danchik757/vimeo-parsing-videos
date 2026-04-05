"""
Тест поиска кнопки Download с различными задержками
"""
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

VIDEO_URL = "https://vimeo.com/1071902981"

print(f"Testing: {VIDEO_URL}")
print("=" * 80)

with SB(uc=True, headless=False) as sb:
    print("Opening page...")
    sb.open(VIDEO_URL)

    # Проверяем через разные интервалы времени
    for wait_time in [5, 10, 15, 20, 30]:
        print(f"\n{'='*80}")
        print(f"Waiting {wait_time} seconds...")
        sb.sleep(wait_time - (5 if wait_time > 5 else 0))  # Компенсируем предыдущее ожидание

        print(f"\nAttempt after {wait_time}s:")

        # Проверка 1: Ищем кнопку напрямую
        try:
            button = sb.driver.find_element(By.CSS_SELECTOR, "button[aria-label='Download button']")
            print(f"  ✅ FOUND! Direct selector works")
            print(f"     Visible: {button.is_displayed()}")
            print(f"     Enabled: {button.is_enabled()}")
            print(f"     Location: {button.location}")
            print(f"     Size: {button.size}")

            # Пытаемся получить текст
            try:
                print(f"     Text: '{button.text}'")
            except:
                pass

            # Пробуем кликнуть
            try:
                print(f"\n  Trying to click...")
                button.click()
                print(f"  ✅ CLICKED!")
                sb.sleep(3)

                # Проверяем модальное окно
                try:
                    modal = sb.driver.find_element(By.CSS_SELECTOR, "section[aria-modal='true']")
                    print(f"  ✅ MODAL OPENED!")

                    # Ищем ссылки на скачивание
                    links = modal.find_elements(By.TAG_NAME, "a")
                    print(f"  Found {len(links)} download links in modal")
                    for i, link in enumerate(links[:3], 1):
                        href = link.get_attribute("href") or ""
                        text = link.text or ""
                        if href:
                            print(f"    Link {i}: {text} -> {href[:80]}...")

                    break  # Успех! Выходим из цикла

                except Exception as e:
                    print(f"  ❌ Modal not found: {e}")

            except Exception as e:
                print(f"  ❌ Click failed: {e}")

        except Exception as e:
            print(f"  ❌ Button NOT found: {e}")

        # Проверка 2: Ищем все кнопки на странице
        try:
            all_buttons = sb.driver.find_elements(By.TAG_NAME, "button")
            print(f"\n  Total buttons on page: {len(all_buttons)}")

            # Ищем среди всех кнопок
            download_buttons = []
            for btn in all_buttons:
                try:
                    aria = btn.get_attribute("aria-label") or ""
                    if "download" in aria.lower():
                        download_buttons.append({
                            'aria-label': aria,
                            'class': btn.get_attribute("class"),
                            'visible': btn.is_displayed(),
                            'text': btn.text
                        })
                except:
                    pass

            if download_buttons:
                print(f"  Found {len(download_buttons)} buttons with 'download' in aria-label:")
                for i, btn_info in enumerate(download_buttons, 1):
                    print(f"    Button {i}:")
                    print(f"      aria-label: {btn_info['aria-label']}")
                    print(f"      class: {btn_info['class']}")
                    print(f"      visible: {btn_info['visible']}")
                    print(f"      text: {btn_info['text']}")
        except Exception as e:
            print(f"  ❌ Error searching all buttons: {e}")

        # Проверка 3: Scroll to button location
        if wait_time == 15:
            print(f"\n  Trying to scroll...")
            try:
                sb.driver.execute_script("window.scrollTo(0, 800);")
                sb.sleep(2)
                print(f"  ✅ Scrolled down")
            except:
                pass

    print(f"\n{'='*80}")
    print("Browser will stay open for 30 seconds...")
    sb.sleep(30)

print("\nDone!")
