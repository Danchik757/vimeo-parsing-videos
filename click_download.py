"""
Находим и кликаем кнопку Download
"""
from seleniumbase import SB
from selenium.webdriver.common.by import By
import time

VIDEO_URL = "https://vimeo.com/973136123"

print(f"Opening {VIDEO_URL}")

with SB(uc=True, headless=False) as sb:
    sb.open(VIDEO_URL)
    print("Page opened, waiting 20s...")
    sb.sleep(20)

    print("\nSearching for Download button near Watch Later...")

    # Сначала найдем кнопку Watch Later, потом найдем Download рядом
    try:
        # Ищем все кнопки в action bar
        buttons = sb.driver.find_elements(By.CSS_SELECTOR, ".chakra-stack.css-tistzx button")
        print(f"Found {len(buttons)} buttons in action bar\n")

        watch_later_index = None
        download_btn = None

        # Найдем Watch Later и Download
        for i, btn in enumerate(buttons):
            try:
                aria = btn.get_attribute("aria-label") or ""
                title = btn.get_attribute("title") or ""
                visible = btn.is_displayed()

                print(f"Button {i+1}: aria='{aria}', title='{title}', visible={visible}")

                if "watch later" in aria.lower() or "watch later" in title.lower():
                    watch_later_index = i
                    print(f"  ^ Found Watch Later at position {i+1}")

                if "download" in aria.lower() or "download" in title.lower():
                    download_btn = btn
                    print(f"  ^ Found Download at position {i+1}")

            except Exception as e:
                print(f"  Error: {e}")

        # Если нашли Download - кликаем
        if download_btn:
            print(f"\n✅ Found Download button!")
            print(f"Clicking...")
            download_btn.click()
            print("✅ Clicked!")

            sb.sleep(5)

            # Проверим, что модальное окно появилось
            try:
                modal = sb.driver.find_element(By.CSS_SELECTOR, "section[aria-modal='true']")
                print("\n✅ Download modal opened!")
                print("Modal found, checking download options...")

                # Найдем все опции скачивания
                options = sb.driver.find_elements(By.CSS_SELECTOR, "section[aria-modal='true'] a")
                print(f"\nFound {len(options)} download options:")
                for i, opt in enumerate(options, 1):
                    text = opt.text or ""
                    href = opt.get_attribute("href") or ""
                    print(f"  Option {i}: {text}")
                    if href:
                        print(f"    URL: {href[:80]}...")

            except:
                print("\n❌ Download modal not found")
        else:
            print(f"\n❌ Download button not found!")
            if watch_later_index is not None:
                print(f"Watch Later was at position {watch_later_index+1}")
                print(f"Download should be nearby!")

    except Exception as e:
        print(f"Error: {e}")

    print("\nBrowser will stay open for 60 seconds...")
    sb.sleep(60)

print("Done!")
