"""
Тестовый скрипт V3 - поиск кнопки Download рядом с Like/Share
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
    print("Page opened, waiting 15s for full page load...")
    sb.sleep(15)

    # Check page title
    title = sb.get_title()
    print(f"\nPage title: {title}")

    # Try to hover over video to reveal controls
    print("\nHovering over video player...")
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver import ActionChains

        # Find video element
        video = sb.find_element("video")
        action = ActionChains(sb.driver)
        action.move_to_element(video).perform()
        print("✓ Hovered over video")
        sb.sleep(3)
    except Exception as e:
        print(f"✗ Could not hover: {e}")

    # Click on video to show controls
    print("\nClicking on video...")
    try:
        sb.click("video", timeout=5)
        print("✓ Clicked on video")
        sb.sleep(5)
    except:
        print("✗ Could not click on video")

    # Look for the sidedock (where Like, Share buttons are)
    print("\nLooking for sidedock (button area)...")
    try:
        sidedock = sb.find_element('[data-sidedock="true"]')
        print("✓ Found sidedock")

        # Hover over it
        action = ActionChains(sb.driver)
        action.move_to_element(sidedock).perform()
        print("✓ Hovered over sidedock")
        sb.sleep(3)
    except Exception as e:
        print(f"✗ Could not find/hover sidedock: {e}")

    # Search for all buttons in the sidedock area
    print("\nSearching for Download button...")
    try:
        # Try different selectors
        download_selectors = [
            'button[aria-label*="Download"]',
            'button[aria-label*="download"]',
            'button[data-download-button]',
            'button.download-button',
            '[data-sidedock] button',  # All buttons in sidedock
        ]

        for selector in download_selectors:
            try:
                elements = sb.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    print(f"\n✓ Found {len(elements)} element(s) with selector: {selector}")
                    for i, elem in enumerate(elements):
                        try:
                            aria = elem.get_attribute("aria-label") or ""
                            text = elem.text or ""
                            visible = elem.is_displayed()
                            print(f"  [{i+1}] aria-label='{aria}', text='{text}', visible={visible}")

                            if "download" in aria.lower() or "download" in text.lower():
                                print(f"  ✓✓✓ THIS IS DOWNLOAD BUTTON!")
                                print(f"      HTML: {elem.get_attribute('outerHTML')[:300]}")
                        except:
                            pass
            except:
                pass

    except Exception as e:
        print(f"Error: {e}")

    # Get ALL buttons in sidedock
    print("\n\nAll buttons in sidedock:")
    try:
        sidedock_buttons = sb.driver.find_elements(By.CSS_SELECTOR, '[data-sidedock] button')
        print(f"Total buttons in sidedock: {len(sidedock_buttons)}")
        for i, btn in enumerate(sidedock_buttons):
            try:
                aria = btn.get_attribute("aria-label") or "(no aria)"
                text = btn.text or "(no text)"
                visible = btn.is_displayed()
                classes = btn.get_attribute("class") or ""
                print(f"\nButton {i+1}:")
                print(f"  aria-label: {aria}")
                print(f"  text: {text}")
                print(f"  visible: {visible}")
                print(f"  classes: {classes[:100]}")
            except Exception as e:
                print(f"  Error reading button: {e}")
    except Exception as e:
        print(f"Error getting sidedock buttons: {e}")

    # Save page source
    html_file = "test_page_v3_973136123.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(sb.get_page_source())
    print(f"\n✓ Page HTML saved to: {html_file}")

    # Take screenshot
    screenshot_file = "test_screenshot_v3_973136123.png"
    sb.save_screenshot(screenshot_file)
    print(f"✓ Screenshot saved to: {screenshot_file}")

    # Wait for manual check
    print("\n==================================================")
    print("Browser will stay open for 60 seconds.")
    print("Please manually click where Download button should be!")
    print("==================================================")
    time.sleep(60)

print("\nTest complete!")
