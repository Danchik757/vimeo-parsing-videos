"""
Тест конкретного видео 1063530738 - которое должно иметь кнопку Download
"""
from seleniumbase import SB

VIDEO_ID = "1063530738"
VIDEO_URL = f"https://vimeo.com/{VIDEO_ID}"

print(f"Opening {VIDEO_URL}")
print("Looking for Download button...")

with SB(uc=True, headless=False) as sb:
    sb.open(VIDEO_URL)
    print("Page loaded, waiting 10s...")
    sb.sleep(10)

    title = sb.get_title()
    print(f"\nPage title: {title}")

    # Save HTML
    html = sb.get_page_source()
    html_file = f"test_{VIDEO_ID}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Saved HTML to {html_file}")

    # Check buttons in chakra-stack
    print("\nSearching for buttons in chakra-stack...")
    try:
        from selenium.webdriver.common.by import By
        buttons = sb.driver.find_elements(By.CSS_SELECTOR, ".chakra-stack.css-tistzx button")
        print(f"Found {len(buttons)} buttons in chakra-stack:")

        for i, btn in enumerate(buttons, 1):
            aria = btn.get_attribute("aria-label") or "(no aria-label)"
            title_attr = btn.get_attribute("title") or ""
            visible = btn.is_displayed()
            print(f"  Button {i}: aria='{aria}', title='{title_attr}', visible={visible}")

            if "download" in aria.lower() or "download" in title_attr.lower():
                print(f"  >>> FOUND DOWNLOAD BUTTON!")
    except Exception as e:
        print(f"Error: {e}")

    print("\n\nBrowser will stay open for 30 seconds. Check manually!")
    sb.sleep(30)

print("Test complete!")
