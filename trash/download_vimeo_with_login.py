"""
Vimeo Downloader с авторизацией

КЛЮЧЕВАЯ ОСОБЕННОСТЬ:
- Логин ОДИН РАЗ в начале
- Cookies сохраняются на всю сессию
- Кнопка Download видна на всех видео
"""
from seleniumbase import SB
import json
import time

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

# Vimeo credentials (ADD YOUR EMAIL AND PASSWORD TO config.json!)
VIMEO_EMAIL = input("Enter your Vimeo email: ")
VIMEO_PASSWORD = input("Enter your Vimeo password: ")

VIDEO_URL = "https://vimeo.com/973136123"

print("Starting browser...")
with SB(uc=True, headless=False) as sb:

    # Step 1: Login to Vimeo
    print("\nStep 1: Logging in to Vimeo...")
    sb.open("https://vimeo.com/log_in")
    sb.sleep(3)

    # Fill login form
    sb.type("input[name='email']", VIMEO_EMAIL)
    sb.type("input[name='password']", VIMEO_PASSWORD)
    sb.click("button[type='submit']")

    print("Waiting for login to complete...")
    sb.sleep(10)

    # Check if logged in
    if "log_in" not in sb.get_current_url():
        print("✅ Successfully logged in!")
    else:
        print("❌ Login failed!")
        exit(1)

    # Step 2: Open video page
    print(f"\nStep 2: Opening video {VIDEO_URL}")
    sb.open(VIDEO_URL)
    sb.sleep(10)

    # Step 3: Check for Download button
    print("\nStep 3: Looking for Download button...")
    try:
        from selenium.webdriver.common.by import By
        buttons = sb.driver.find_elements(By.CSS_SELECTOR, ".chakra-stack.css-tistzx button")
        print(f"Found {len(buttons)} buttons in action bar:")

        for i, btn in enumerate(buttons, 1):
            aria = btn.get_attribute("aria-label") or ""
            print(f"  Button {i}: {aria}")

            if "download" in aria.lower():
                print(f"\n✅✅✅ FOUND DOWNLOAD BUTTON!")
                print(f"      Clicking it now...")
                btn.click()
                sb.sleep(3)
                break
        else:
            print("\n❌ Download button still not found!")
            print("This means the video owner settings don't allow downloads")

    except Exception as e:
        print(f"Error: {e}")

    # Keep browser open
    print("\nBrowser will stay open for 60 seconds...")
    sb.sleep(60)

print("Test complete!")
