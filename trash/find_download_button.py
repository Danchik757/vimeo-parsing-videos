"""
Простой скрипт для поиска и нажатия кнопки Download
БЕЗ авторизации
"""
from seleniumbase import SB
import time

VIDEO_URL = "https://vimeo.com/973136123"

print(f"Opening {VIDEO_URL}")

with SB(uc=True, headless=False) as sb:
    sb.open(VIDEO_URL)
    print("Page opened, waiting 20s for full page load...")
    sb.sleep(20)  # Ждем долго для загрузки всего JavaScript

    title = sb.get_title()
    print(f"\nPage title: {title}")

    # Ищем ВСЕ кнопки на странице
    print("\nSearching for ALL buttons on page...")
    try:
        from selenium.webdriver.common.by import By

        all_buttons = sb.driver.find_elements(By.TAG_NAME, "button")
        print(f"Total buttons found: {len(all_buttons)}\n")

        download_found = False

        for i, btn in enumerate(all_buttons):
            try:
                aria = btn.get_attribute("aria-label") or ""
                text = btn.text or ""
                classes = btn.get_attribute("class") or ""
                title_attr = btn.get_attribute("title") or ""
                visible = btn.is_displayed()

                # Check if this is Download button
                if "download" in aria.lower() or "download" in text.lower() or "download" in title_attr.lower():
                    print(f">>> FOUND DOWNLOAD BUTTON #{i}:")
                    print(f"    aria-label: {aria}")
                    print(f"    text: {text}")
                    print(f"    title: {title_attr}")
                    print(f"    visible: {visible}")
                    print(f"    classes: {classes[:100]}")

                    if visible:
                        print(f"\n✅ Clicking Download button...")
                        btn.click()
                        download_found = True
                        sb.sleep(5)
                        break
            except:
                pass

        if not download_found:
            print("\n❌ Download button not found among all buttons!")
            print("\nLet's check ALL links (a tags) too...")

            all_links = sb.driver.find_elements(By.TAG_NAME, "a")
            for i, link in enumerate(all_links):
                try:
                    text = link.text or ""
                    href = link.get_attribute("href") or ""

                    if "download" in text.lower() or "download" in href.lower():
                        print(f">>> FOUND DOWNLOAD LINK #{i}:")
                        print(f"    text: {text}")
                        print(f"    href: {href}")
                        print(f"    visible: {link.is_displayed()}")

                        if link.is_displayed():
                            print(f"\n✅ Clicking Download link...")
                            link.click()
                            download_found = True
                            sb.sleep(5)
                            break
                except:
                    pass

        if not download_found:
            print("\n❌ Download button/link not found anywhere!")
            print("Saving page HTML for manual inspection...")
            with open("page_no_download.html", "w", encoding="utf-8") as f:
                f.write(sb.get_page_source())
            print("Saved to: page_no_download.html")

    except Exception as e:
        print(f"Error: {e}")

    # Keep browser open
    print("\nBrowser will stay open for 30 seconds...")
    sb.sleep(30)

print("Test complete!")
