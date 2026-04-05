"""
Просто открываем страницу и смотрим
"""
from seleniumbase import SB

VIDEO_URL = "https://vimeo.com/973136123"

print(f"Opening {VIDEO_URL}")
print("Browser will stay open - check the page manually!")

with SB(uc=True, headless=False) as sb:
    sb.open(VIDEO_URL)
    print("\nPage opened!")
    print("Waiting 30 seconds for page to fully load...")
    sb.sleep(30)

    print("\n" + "="*60)
    print("NOW CHECK THE PAGE - IS THERE A DOWNLOAD BUTTON?")
    print("="*60)

    # Keep browser open for a long time
    print("\nBrowser will stay open for 5 minutes...")
    print("Press Ctrl+C to close early")

    try:
        sb.sleep(300)  # 5 minutes
    except KeyboardInterrupt:
        print("\nClosing...")

print("Done!")
