"""
Ждем ОЧЕНЬ долго и ищем кнопку Download
"""
from seleniumbase import SB
from selenium.webdriver.common.by import By

VIDEO_URL = "https://vimeo.com/973136123"

print(f"Opening {VIDEO_URL}")

with SB(uc=True, headless=False) as sb:
    sb.open(VIDEO_URL)
    print("Page opened!")

    # Проверяем каждые 10 секунд
    for i in range(6):  # 6 раз по 10 секунд = 60 секунд
        wait_time = 10
        print(f"\nWaiting {wait_time}s (check #{i+1}/6)...")
        sb.sleep(wait_time)

        # Считаем кнопки
        try:
            buttons = sb.driver.find_elements(By.CSS_SELECTOR, ".chakra-stack.css-tistzx button")
            print(f"Found {len(buttons)} buttons in action bar")

            # Ищем Download
            for j, btn in enumerate(buttons, 1):
                aria = btn.get_attribute("aria-label") or ""
                title = btn.get_attribute("title") or ""

                if "download" in aria.lower() or "download" in title.lower():
                    print(f"\n✅✅✅ FOUND DOWNLOAD BUTTON at position {j}!")
                    print(f"  aria-label: {aria}")
                    print(f"  title: {title}")

                    # Кликаем!
                    btn.click()
                    print("✅ Clicked!")
                    sb.sleep(10)
                    break
            else:
                print(f"  No Download button yet (after {(i+1)*10}s)")

        except Exception as e:
            print(f"Error: {e}")

    print("\n\nBrowser stays open for 60 more seconds...")
    sb.sleep(60)

print("Done!")
