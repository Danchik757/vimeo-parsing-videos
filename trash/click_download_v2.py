"""
Находим и кликаем кнопку Download - V2 с явным ожиданием
"""
from seleniumbase import SB
from selenium.webdriver.common.by import By
import time

VIDEO_URL = "https://vimeo.com/973136123"

print(f"Opening {VIDEO_URL}")

with SB(uc=True, headless=False) as sb:
    sb.open(VIDEO_URL)
    print("Page opened!")

    print("Waiting 30 seconds for FULL page load...")
    sb.sleep(30)

    print("\nPage should be fully loaded now")
    print("Current URL:", sb.get_current_url())
    print("Page title:", sb.get_title())

    print("\nSearching for Download button...")

    # Используем SeleniumBase методы вместо прямого driver
    try:
        # Проверим, есть ли action bar
        if sb.is_element_visible(".chakra-stack.css-tistzx"):
            print("✓ Action bar found!")

            # Подождем еще немного для полной загрузки кнопок
            sb.sleep(5)

            # Найдем все кнопки в action bar используя SeleniumBase
            action_bar_selector = ".chakra-stack.css-tistzx"

            # Прокрутим к action bar
            sb.scroll_to(action_bar_selector)
            sb.sleep(2)

            # Попробуем найти кнопку Download разными способами
            download_selectors = [
                ".chakra-stack.css-tistzx button[aria-label*='Download']",
                ".chakra-stack.css-tistzx button[title*='Download']",
                "button[aria-label*='Download']",
                "button[title*='Download']",
            ]

            download_found = False
            for selector in download_selectors:
                try:
                    if sb.is_element_visible(selector, timeout=2):
                        print(f"\n✅ Found Download button with selector: {selector}")
                        sb.click(selector)
                        print("✅ Clicked Download button!")
                        download_found = True
                        sb.sleep(5)
                        break
                except:
                    continue

            if not download_found:
                print("\n❌ Download button not found with any selector")
                print("Let me try to get ALL buttons...")

                # Сохраним HTML для анализа
                html = sb.get_page_source()
                with open("page_with_download.html", "w", encoding="utf-8") as f:
                    f.write(html)
                print("Saved HTML to: page_with_download.html")

        else:
            print("❌ Action bar not found!")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    print("\nBrowser will stay open for 60 seconds...")
    print("Check the page manually!")
    sb.sleep(60)

print("Done!")
