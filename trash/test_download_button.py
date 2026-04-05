"""
Тестовый скрипт для проверки селектора кнопки Download
"""
from seleniumbase import SB
import time

VIDEO_URL = "https://vimeo.com/973136123"

print(f"Opening {VIDEO_URL}")

with SB(
    uc=True,
    headless=False,
    disable_csp=True,
    block_images=False,
    incognito=False,
    chromium_arg="--disable-blink-features=AutomationControlled"
) as sb:

    sb.open(VIDEO_URL)
    print("Page opened, waiting for load...")
    sb.sleep(5)

    # Check page title
    title = sb.get_title()
    print(f"\nPage title: {title}")

    # Check if Cloudflare
    if ' on vimeo' in title.lower():
        print("✓ Page loaded successfully (no Cloudflare)")
    else:
        print("✗ Might be Cloudflare or other issue")

    # Try to find Download button with different selectors
    selectors_to_try = [
        ("button[aria-label='Download button']", "aria-label='Download button'"),
        ("button[aria-label='Download']", "aria-label='Download'"),
        ("button[data-download-button]", "data-download-button"),
        ("button:contains('Download')", "button containing 'Download'"),
        ("a:contains('Download')", "a containing 'Download'"),
        ("[class*='download']", "class containing 'download'"),
        ("button", "ANY button (debug)"),
    ]

    for selector, description in selectors_to_try:
        try:
            from selenium.webdriver.common.by import By
            elements = sb.driver.find_elements(By.CSS_SELECTOR, selector)

            if elements:
                print(f"\n✓ Found {len(elements)} element(s) with selector: {description}")
                for i, elem in enumerate(elements[:3]):  # Show first 3
                    try:
                        visible = elem.is_displayed()
                        text = elem.text[:50] if elem.text else "(no text)"
                        aria = elem.get_attribute("aria-label") or "(no aria-label)"
                        class_name = elem.get_attribute("class") or "(no class)"

                        print(f"  [{i+1}] visible={visible}, text='{text}', aria='{aria}'")
                        print(f"      class='{class_name[:80]}'")
                    except:
                        print(f"  [{i+1}] (error reading element)")
            else:
                print(f"✗ No elements found for: {description}")
        except Exception as e:
            print(f"✗ Error with selector {description}: {e}")

    # Save page source for manual inspection
    html_file = "test_page_973136123.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(sb.get_page_source())
    print(f"\n✓ Page HTML saved to: {html_file}")

    # Wait for user to check the browser
    print("\nBrowser will stay open for 30 seconds for manual inspection...")
    time.sleep(30)

print("\nTest complete!")
