"""
Тестовый скрипт V2 - поиск кнопки Download с прокруткой и ожиданием
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
    print("Page opened, waiting 10s for full page load...")
    sb.sleep(10)

    # Check page title
    title = sb.get_title()
    print(f"\nPage title: {title}")

    # Scroll down to load all content
    print("\nScrolling down...")
    sb.execute_script("window.scrollTo(0, 500);")
    sb.sleep(2)
    sb.execute_script("window.scrollTo(0, 1000);")
    sb.sleep(2)
    sb.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    sb.sleep(3)

    # Try clicking on video player to reveal controls
    print("\nTrying to click on video player...")
    try:
        sb.click("video", timeout=3)
        print("✓ Clicked on video")
        sb.sleep(3)
    except:
        print("✗ Could not click on video")

    # Try right-clicking on video (context menu might have Download)
    print("\nTrying right-click on video...")
    try:
        video = sb.find_element("video")
        from selenium.webdriver import ActionChains
        action = ActionChains(sb.driver)
        action.context_click(video).perform()
        print("✓ Right-clicked on video")
        sb.sleep(3)
    except Exception as e:
        print(f"✗ Could not right-click: {e}")

    # Search for Download in page
    print("\nSearching for 'Download' text on page...")
    page_text = sb.get_page_source().lower()
    if "download" in page_text:
        # Find all elements containing 'download'
        count = page_text.count("download")
        print(f"✓ Found 'download' {count} times in HTML")
    else:
        print("✗ 'Download' not found in HTML")

    # Try to find any buttons or links
    print("\nLooking for buttons and links...")
    try:
        from selenium.webdriver.common.by import By
        buttons = sb.driver.find_elements(By.TAG_NAME, "button")
        print(f"Found {len(buttons)} buttons total")

        # Check buttons for download-related text
        for i, btn in enumerate(buttons[:20]):  # Check first 20 buttons
            try:
                text = btn.text.lower()
                aria = btn.get_attribute("aria-label") or ""
                aria_lower = aria.lower()

                if "download" in text or "download" in aria_lower:
                    print(f"\n✓✓✓ FOUND DOWNLOAD BUTTON #{i}: text='{btn.text}', aria='{aria}'")
                    print(f"    Visible: {btn.is_displayed()}")
                    print(f"    HTML: {btn.get_attribute('outerHTML')[:200]}")
            except:
                pass
    except Exception as e:
        print(f"Error searching buttons: {e}")

    # Save page source for manual inspection
    html_file = "test_page_v2_973136123.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(sb.get_page_source())
    print(f"\n✓ Page HTML saved to: {html_file}")

    # Take screenshot
    screenshot_file = "test_screenshot_973136123.png"
    sb.save_screenshot(screenshot_file)
    print(f"✓ Screenshot saved to: {screenshot_file}")

    # Wait for user to check the browser
    print("\n==================================================")
    print("Browser will stay open for 60 seconds.")
    print("Please check where the Download button is located!")
    print("==================================================")
    time.sleep(60)

print("\nTest complete!")
