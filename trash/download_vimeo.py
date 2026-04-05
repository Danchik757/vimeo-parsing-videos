import vimeo
import json
from tqdm import tqdm
import os
import urllib.request
import time
import logging

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

# Import Telegram notifier
from telegram_notifier import TelegramNotifier

# Load configuration
with open('config.json', 'r') as f:
    config = json.load(f)

# Setup logging with new paths
log_file = config['files']['log_file']
os.makedirs(os.path.dirname(log_file), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info("VIMEO DOWNLOADER STARTED")
logger.info("=" * 60)
logger.info("Configuration loaded successfully")

# Initialize Telegram notifier
telegram = TelegramNotifier(config)


def create_driver_with_proxy():
    """Create Chrome driver with proxy extension"""
    chrome_options = uc.ChromeOptions()
    chrome_options.headless = True  # Headless mode for server deployment
    chrome_options.add_argument('--ignore-certificate-errors')
    chrome_options.add_argument('--allow-insecure-localhost')

    # Additional optimizations for server
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--disable-logging')
    chrome_options.add_argument('--disable-dev-shm-usage')  # For Docker/low memory
    chrome_options.add_argument('--no-sandbox')  # Required for some servers

    # Load proxy extension if proxy is enabled
    if config['proxy']['enabled']:
        extension_path = os.path.abspath('chrome_proxy_extension')
        chrome_options.add_argument(f'--load-extension={extension_path}')
        logger.info(f"Proxy enabled: {config['proxy']['host']}:{config['proxy']['port']}")
    else:
        logger.info("Proxy disabled, using direct connection")

    # Disable images for faster loading
    prefs = {
        "profile.managed_default_content_settings.images": 2
    }
    chrome_options.add_experimental_option("prefs", prefs)

    driver = uc.Chrome(options=chrome_options, version_main=145, use_subprocess=True, headless=True)
    logger.info("Chrome driver created for Chrome version 145 (headless mode)")
    return driver


def check_if_blocked(driver):
    """
    Check if IP is blocked by Vimeo
    Returns: (is_blocked: bool, reason: str)
    """
    page_source = driver.page_source.lower()
    page_title = driver.title.lower()

    # Check for various blocking indicators
    blocking_indicators = [
        ('429', 'Rate limit exceeded (HTTP 429)'),
        ('too many requests', 'Too many requests detected'),
        ('access denied', 'Access denied by Vimeo'),
        ('forbidden', 'Access forbidden (HTTP 403)'),
        ('blocked', 'IP address blocked'),
        ('verify you are human', 'Human verification required'),
        ('captcha', 'CAPTCHA challenge detected'),
        ('moment' in page_title or 'момент' in page_title, 'Vimeo verification page'),
    ]

    for indicator, reason in blocking_indicators:
        if indicator in page_source or indicator in page_title:
            return True, reason

    return False, None


def get_url(driver, url):
    """Open URL and check for blocking"""
    while True:
        try:
            if driver is None:
                driver = create_driver_with_proxy()
                logger.info("Created new browser instance")

            driver.get(url)

            # Check if blocked
            is_blocked, block_reason = check_if_blocked(driver)
            if is_blocked:
                logger.error(f"⚠️ IP BLOCKED: {block_reason}")
                logger.error("Your IP has been blocked by Vimeo due to too many requests")
                logger.error("Possible solutions:")
                logger.error("  1. Wait 1-2 hours before retrying")
                logger.error("  2. Change your IP address (VPN/proxy rotation)")
                logger.error("  3. Use rotating residential proxies")
                raise Exception(f"IP_BLOCKED: {block_reason}")

            return driver
        except Exception as ex:
            if 'IP_BLOCKED' in str(ex):
                raise  # Re-raise blocking errors
            logger.error(f"Error loading URL {url}: {ex}")
            try:
                driver.quit()
            except:
                pass
            driver = None


def download_file(url, local_filename):
    """Download file from URL to local path"""
    urllib.request.urlretrieve(url, local_filename)
    file_size_mb = os.path.getsize(local_filename) / (1024 * 1024)
    logger.info(f"Downloaded: {local_filename} ({file_size_mb:.1f} MB)")


# Initialize Vimeo client
client = vimeo.VimeoClient(
    token=config['vimeo_api']['token'],
    key=config['vimeo_api']['client_id'],
    secret=config['vimeo_api']['secret']
)
logger.info("Vimeo client initialized")

# Load URLs
source_file = config['files']['source_json']
logger.info(f"Loading URLs from {source_file}")

with open(source_file, 'r') as f:
    urls = json.load(f)

# Apply test mode
if config['settings']['test_mode']:
    urls = urls[:config['settings']['test_limit']]
    logger.info(f"TEST MODE: Processing only first {len(urls)} videos")
else:
    logger.info(f"Processing {len(urls)} videos")

# Create directories
json_dir = config['files']['jsons_dir']
video_dir = config['files']['videos_dir']
os.makedirs(video_dir, exist_ok=True)
os.makedirs(json_dir, exist_ok=True)
logger.info(f"Directories ready: {video_dir}, {json_dir}")

# Send start notification
telegram.notify_start(len(urls), test_mode=config['settings']['test_mode'])

# Track statistics
failed_urls = []
driver = None
successful_downloads = 0
skipped_videos = 0
ip_blocked = False

for url in tqdm(list(urls), desc="Processing videos"):
    # Stop if IP is blocked
    if ip_blocked:
        logger.error("Stopping due to IP block. Please resolve the issue and restart.")
        break

    id = url.split('/')[-1]
    logger.info(f"Processing video ID: {id}")

    try:
        response = client.get(f'https://api.vimeo.com/videos/{id}')

        # Check for API authentication errors
        if response.status_code in [401, 403]:
            logger.error(f"Authentication error (status {response.status_code}). Token may be expired.")
            logger.error("Please check your Vimeo API credentials in config.json")
            telegram.notify_api_error(response.status_code, "Authentication error. Token may be expired.")
            break

        # Check for rate limiting on API
        if response.status_code == 429:
            logger.error("⚠️ API RATE LIMIT EXCEEDED")
            logger.error("Your API token has exceeded rate limits")
            logger.error("Wait 1 hour or use a different API token")
            telegram.notify_api_error(429, "API rate limit exceeded. Wait 1 hour or use different token.")
            break

        json_data = response.json()

        # Check if downloadable BEFORE saving JSON
        try:
            can_download = json_data['privacy']['download']
        except KeyError as ex:
            logger.warning(f"Missing 'privacy.download' field for {id}: {ex}")
            skipped_videos += 1
            continue

        if not can_download:
            logger.info(f"Video {id} is not downloadable (privacy settings)")
            skipped_videos += 1
            # Count current video number for telegram notification
            current_num = successful_downloads + skipped_videos
            telegram.notify_video_skipped(id, "Privacy settings restrict download", current_num, len(urls))
            continue

        logger.info(f"Video {id} is downloadable, starting download process")

        # Retry logic for downloading
        retry_count = 0
        max_retries = config['settings']['retry_attempts']

        while retry_count < max_retries:
            try:
                driver = get_url(driver, url)
                logger.info(f"Opened video page for {id}")

                # Wait for JavaScript to load
                js_wait = config['settings']['javascript_wait_time']
                logger.info(f"Waiting {js_wait} seconds for page JavaScript to load...")
                time.sleep(js_wait)

                # Check for blocking again after page load
                is_blocked, block_reason = check_if_blocked(driver)
                if is_blocked:
                    logger.error(f"⚠️ IP BLOCKED: {block_reason}")
                    ip_blocked = True
                    failed_urls.append({'url': url, 'error': f'IP_BLOCKED: {block_reason}', 'stage': 'page_load'})
                    # Send IP block notification
                    try:
                        import requests
                        current_ip = requests.get('https://api.ipify.org', timeout=5).text
                    except:
                        current_ip = "unknown"
                    telegram.notify_ip_blocked(current_ip, block_reason)
                    break

                # Click download button
                download_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Download button']")
                download_btn.click()
                logger.info(f"Clicked download button for {id}")

                # Wait for download modal
                start_time = time.time()
                section_elem = None
                while time.time() - start_time < 10:
                    try:
                        section_elem = driver.find_element(By.CSS_SELECTOR, "section[aria-modal='true']")
                        break
                    except:
                        time.sleep(0.5)

                if section_elem is None:
                    logger.warning(f"Download modal not found for {id}, retry {retry_count + 1}/{max_retries}")
                    retry_count += 1
                    continue

                # Find best quality download option
                best_elem = None
                for elem in section_elem.find_elements(By.TAG_NAME, "div"):
                    try:
                        if elem.get_attribute("id") and elem.get_attribute("id").startswith('download-file'):
                            if best_elem is None:
                                best_elem = elem
                            if 'original' in elem.text.lower():
                                best_elem = elem
                                logger.info(f"Found 'original' quality option for {id}")
                                break
                    except:
                        pass

                if best_elem is None:
                    logger.warning(f"No download option found for {id}, retry {retry_count + 1}/{max_retries}")
                    retry_count += 1
                    continue

                # Get download link
                download_link = best_elem.find_element(By.TAG_NAME, "a").get_attribute("href")
                logger.info(f"Got download link for {id}")

                # Determine file extension
                ext = '.mp4'
                if ext not in download_link.lower():
                    ext = '.mov'
                    if ext not in download_link.lower():
                        ext = ''

                # Download file
                local_path = os.path.join(video_dir, f'{id}{ext}')
                logger.info(f"Starting download to {local_path}")
                download_file(download_link, local_path)

                # Save metadata JSON only after successful download
                with open(os.path.join(json_dir, f'{id}.json'), 'w', encoding='utf-8') as f:
                    json.dump(json_data, f, indent=2, ensure_ascii=False)
                logger.info(f"✓ Saved metadata for {id}")

                successful_downloads += 1
                logger.info(f"✓ Successfully downloaded {id} ({successful_downloads}/{len(urls)})")

                # Send Telegram notification
                file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
                telegram.notify_video_downloaded(id, f'{id}{ext}', file_size_mb, successful_downloads, len(urls))
                break

            except Exception as ex:
                if 'IP_BLOCKED' in str(ex):
                    ip_blocked = True
                    failed_urls.append({'url': url, 'error': str(ex), 'stage': 'page_load'})
                    break

                retry_count += 1
                logger.error(f"Error downloading {id} (attempt {retry_count}/{max_retries}): {ex}")
                if retry_count >= max_retries:
                    failed_urls.append({'url': url, 'error': str(ex), 'stage': 'download'})
                    # Send error notification on final failure
                    current_num = successful_downloads + skipped_videos + len(failed_urls)
                    telegram.notify_error(id, str(ex), current_num, len(urls))
                else:
                    time.sleep(config['settings']['retry_delay'])

    except Exception as ex:
        logger.error(f"Error fetching video info for {id}: {ex}")
        failed_urls.append({'url': url, 'error': str(ex), 'stage': 'api_fetch'})
        continue

# Cleanup
if driver is not None:
    try:
        driver.quit()
        logger.info("Browser closed")
    except:
        pass

# Save failed downloads
if failed_urls:
    failed_file = config['files']['failed_downloads']
    os.makedirs(os.path.dirname(failed_file), exist_ok=True)
    with open(failed_file, 'w', encoding='utf-8') as f:
        json.dump(failed_urls, f, indent=2, ensure_ascii=False)
    logger.warning(f"Saved {len(failed_urls)} failed downloads to {failed_file}")

# Final summary
logger.info("=" * 60)
logger.info("DOWNLOAD SESSION SUMMARY")
logger.info("=" * 60)
logger.info(f"Total videos processed: {len(urls)}")
logger.info(f"Successfully downloaded: {successful_downloads}")
logger.info(f"Skipped (not downloadable): {skipped_videos}")
logger.info(f"Failed: {len(failed_urls)}")
if ip_blocked:
    logger.error("⚠️ SESSION STOPPED: IP BLOCKED BY VIMEO")
    logger.error("Please wait 1-2 hours or change your IP address")
logger.info("=" * 60)

if config['settings']['test_mode']:
    logger.info(f"TEST MODE was enabled - only processed first {config['settings']['test_limit']} videos")
    logger.info("To process all videos, set 'test_mode': false in config.json")

# Send final Telegram notification
telegram.notify_finish({
    'total': len(urls),
    'downloaded': successful_downloads,
    'skipped': skipped_videos,
    'failed': len(failed_urls),
    'ip_blocked': ip_blocked
})
