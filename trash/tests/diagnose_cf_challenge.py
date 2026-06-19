"""
Minimal diagnostic script: opens a single Vimeo video URL and analyses
whether a Cloudflare Turnstile challenge is present.

Usage (Windows, 1 worker):
    cd C:/Users/msu_cc/Desktop/work/29d_kon/parsing
    python trash/tests/diagnose_cf_challenge.py [video_id]

Default video_id: 973136123  (confirmed CF trigger in parse1_smoke run)
"""
import sys
import time
import json
import re

VIDEO_ID = sys.argv[1] if len(sys.argv) > 1 else "973136123"
VIDEO_URL = f"https://vimeo.com/{VIDEO_ID}"

CF_INDICATORS = [
    "cloudflare turnstile",
    "verify you are human",
    "verify to continue",
    "challenge-platform",
    "just a moment",
    "cf-challenge",
]

print(f"=== Cloudflare Turnstile Diagnostic ===")
print(f"Target URL: {VIDEO_URL}")
print()

try:
    from seleniumbase import SB
except ImportError:
    print("ERROR: seleniumbase not installed. Run: pip install seleniumbase")
    sys.exit(1)

sb_kwargs = {
    "browser": "chrome",
    "uc": True,
    "headless": False,
    "disable_csp": True,
    "chromium_arg": "--disable-blink-features=AutomationControlled",
}

print(f"Browser kwargs: {sb_kwargs}")
print()

with SB(**sb_kwargs) as sb:
    print(f"[1/6] Opening {VIDEO_URL} ...")
    sb.open(VIDEO_URL)
    time.sleep(5)

    title = sb.get_title()
    url = sb.get_current_url()
    src = sb.get_page_source()
    src_lower = src.lower()

    print(f"[2/6] Page title  : {title!r}")
    print(f"[2/6] Current URL : {url}")
    print(f"[2/6] Page size   : {len(src):,} bytes")
    print()

    # CF indicators
    found = [ind for ind in CF_INDICATORS if ind in src_lower]
    has_cf_iframe = "challenges.cloudflare.com" in src
    has_cf_turnstile_el = "cf-turnstile" in src
    has_cf_response_input = "cf-turnstile-response" in src

    print(f"[3/6] CF indicators found    : {found}")
    print(f"[3/6] CF challenge iframe    : {has_cf_iframe}")
    print(f"[3/6] cf-turnstile element   : {has_cf_turnstile_el}")
    print(f"[3/6] cf-turnstile-response  : {has_cf_response_input}")

    # Sitekey
    m = re.search(r"0x4[A-Za-z0-9]{20,}", src)
    print(f"[3/6] Turnstile sitekey      : {m.group(0) if m else '(not found)'}")
    print()

    # viewer-bootstrap
    vb_match = re.search(r'<script id="viewer-bootstrap"[^>]*>(.*?)</script>', src, re.DOTALL)
    if vb_match:
        try:
            vb = json.loads(vb_match.group(1).strip())
            print(f"[4/6] viewer-bootstrap.user     : {vb.get('user')}")
            print(f"[4/6] viewer-bootstrap.location : {vb.get('location')}")
            logged = ((vb.get("ablincolnConfig") or {}).get("user") or {}).get("logged_in")
            print(f"[4/6] ablincoln logged_in       : {logged!r}")
        except Exception as e:
            print(f"[4/6] viewer-bootstrap parse error: {e}")
    else:
        print("[4/6] viewer-bootstrap: NOT FOUND (page might be CF challenge)")
    print()

    # Verdict
    if has_cf_iframe or found:
        print("❌ RESULT: CLOUDFLARE TURNSTILE CHALLENGE DETECTED")
        print()
        print("   iframe src: challenges.cloudflare.com (sandboxed, cannot inject)")
        print("   Mode: normal (visible checkbox — requires human click OR captcha service)")
        print()
        print("   Options:")
        print("   A) Wait up to 60s — UC mode may auto-solve if IP reputation is OK")
        print("   B) Click the checkbox manually (if browser is visible)")
        print("   C) Use capsolver/2captcha with sitekey above")
        print("   D) Use persistent Chrome profile with cf_clearance cookie")
    elif " on vimeo" in title.lower():
        print("✅ RESULT: VIMEO PAGE LOADED SUCCESSFULLY (no CF challenge)")
        print(f"   title: {title}")
    else:
        print("⚠️  RESULT: UNKNOWN PAGE STATE")
        print(f"   title: {title}")

    print()
    print("[5/6] Waiting 30s to check if CF auto-resolves ...")
    time.sleep(30)
    src2 = sb.get_page_source()
    title2 = sb.get_title()
    found2 = [ind for ind in CF_INDICATORS if ind in src2.lower()]
    has_cf2 = "challenges.cloudflare.com" in src2

    print(f"[6/6] After 30s — title: {title2!r}")
    if found2 or has_cf2:
        print("❌ CF challenge still present after 30s — IP reputation too low for auto-bypass")
    else:
        print("✅ CF challenge resolved after 30s — IP reputation acceptable")
