"""
Vimeo Video Downloader with SeleniumBase UC Mode
Версия с обходом Cloudflare и имитацией человеческого поведения

Техники обхода блокировок:
1. SeleniumBase UC Mode (скрытие автоматизации)
2. Случайные задержки между запросами (3-8 секунд)
3. Имитация движения мыши
4. Скроллинг страницы
5. Случайное ожидание после загрузки
6. "Чтение" информации о видео
"""

import vimeo
import json
import os
import time
import random
import logging
import urllib.request
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from telegram_notifier import TelegramNotifier

# Load configuration
with open('config.json', 'r') as f:
    config = json.load(f)

# Setup logging
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
logger.info("=" * 80)
logger.info("VIMEO DOWNLOADER STARTED (SeleniumBase UC Mode)")
logger.info("=" * 80)

# Initialize Telegram notifier
telegram = TelegramNotifier(config)

# Параметры имитации человека
MIN_DELAY_BETWEEN_VIDEOS = 3
MAX_DELAY_BETWEEN_VIDEOS = 8
PAGE_LOAD_WAIT_MIN = 2
PAGE_LOAD_WAIT_MAX = 5
READING_TIME_MIN = 1
READING_TIME_MAX = 3


def simulate_mouse_movement(sb):
    """Имитация случайного движения мыши"""
    try:
        actions = ActionChains(sb.driver)
        num_movements = random.randint(2, 4)
        for _ in range(num_movements):
            x_offset = random.randint(-200, 200)
            y_offset = random.randint(-200, 200)
            actions.move_by_offset(x_offset, y_offset)
            actions.pause(random.uniform(0.1, 0.3))
        actions.perform()
        actions.reset_actions()
    except Exception as e:
        logger.debug(f"Mouse movement error (not critical): {e}")


def simulate_page_scroll(sb):
    """Имитация скроллинга страницы"""
    try:
        scroll_amount = random.randint(300, 600)
        sb.execute_script(f"window.scrollBy(0, {scroll_amount});")
        time.sleep(random.uniform(0.3, 0.7))
        scroll_back = random.randint(100, 300)
        sb.execute_script(f"window.scrollBy(0, -{scroll_back});")
        time.sleep(random.uniform(0.2, 0.5))
    except Exception as e:
        logger.debug(f"Page scroll error (not critical): {e}")


def check_if_cloudflare_blocked(sb):
    """Проверка блокировки Cloudflare Turnstile"""
    try:
        page_source = sb.get_page_source().lower()
        page_title = sb.get_title().lower()

        cloudflare_indicators = [
            'cloudflare',
            'verify you are human',
            'verify to continue',
            'captcha',
            'challenge',
            'just a moment'
        ]

        for indicator in cloudflare_indicators:
            if indicator in page_source or indicator in page_title:
                return True
        return False
    except:
        return False


def download_file(url, local_filename):
    """Download file from URL to local path"""
    urllib.request.urlretrieve(url, local_filename)
    file_size_mb = os.path.getsize(local_filename) / (1024 * 1024)
    logger.info(f"Downloaded: {local_filename} ({file_size_mb:.1f} MB)")
    return file_size_mb


def download_video(sb, video_url, video_id, json_data, video_dir, json_dir):
    """
    Скачать одно видео с имитацией человеческого поведения

    Returns:
        dict: {
            'success': bool,
            'error': str|None,
            'file_size_mb': float|None
        }
    """
    result = {
        'success': False,
        'error': None,
        'file_size_mb': None
    }

    try:
        # Открываем страницу
        logger.info(f"Opening {video_url}")
        sb.open(video_url)

        # Техника #4: Случайное ожидание после загрузки
        initial_wait = random.uniform(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX)
        logger.info(f"Waiting {initial_wait:.1f}s for page load...")
        time.sleep(initial_wait)

        # Проверяем Cloudflare
        if check_if_cloudflare_blocked(sb):
            logger.warning(f"Cloudflare detected, waiting 20s for auto-bypass...")
            time.sleep(20)

            if check_if_cloudflare_blocked(sb):
                result['error'] = 'Cloudflare Turnstile not bypassed'
                logger.error(f"Cloudflare still blocking after 20s")
                return result
            else:
                logger.info("✓ Cloudflare bypassed successfully!")

        # Техника #2: Имитация движения мыши
        simulate_mouse_movement(sb)

        # Техника #3: Скроллинг страницы
        simulate_page_scroll(sb)

        # Техника #5: "Чтение" информации
        reading_time = random.uniform(READING_TIME_MIN, READING_TIME_MAX)
        logger.info(f"Simulating reading ({reading_time:.1f}s)...")
        time.sleep(reading_time)

        # Ищем кнопку Download
        download_selectors = [
            "button[aria-label='Download button']",
            "button[aria-label='Download']",
            "button[data-download-button]"
        ]

        download_btn = None
        for selector in download_selectors:
            try:
                elements = sb.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements and elements[0].is_displayed():
                    download_btn = elements[0]
                    logger.info(f"Found download button: {selector}")
                    break
            except:
                continue

        if not download_btn:
            result['error'] = 'Download button not found'
            logger.error(f"Download button not found for {video_id}")
            return result

        # Кликаем на кнопку Download
        download_btn.click()
        logger.info("Clicked download button")

        # Ждем модальное окно
        time.sleep(2)

        # Ищем модальное окно с опциями загрузки
        section_elem = None
        start_time = time.time()
        while time.time() - start_time < 10:
            try:
                section_elem = sb.driver.find_element(By.CSS_SELECTOR, "section[aria-modal='true']")
                logger.info("Found download modal")
                break
            except:
                time.sleep(0.5)

        if not section_elem:
            result['error'] = 'Download modal not found'
            logger.error("Download modal not found")
            return result

        # Ищем лучшее качество (original или первый доступный)
        best_elem = None
        for elem in section_elem.find_elements(By.TAG_NAME, "div"):
            try:
                if elem.get_attribute("id") and elem.get_attribute("id").startswith('download-file'):
                    if best_elem is None:
                        best_elem = elem
                    if 'original' in elem.text.lower():
                        best_elem = elem
                        logger.info("Found 'original' quality option")
                        break
            except:
                pass

        if not best_elem:
            result['error'] = 'No download option found'
            logger.error("No download option found in modal")
            return result

        # Получаем ссылку на скачивание
        download_link = best_elem.find_element(By.TAG_NAME, "a").get_attribute("href")
        logger.info(f"Got download link")

        # Определяем расширение файла
        ext = '.mp4'
        if ext not in download_link.lower():
            ext = '.mov'
            if ext not in download_link.lower():
                ext = ''

        # Скачиваем файл
        local_path = os.path.join(video_dir, f'{video_id}{ext}')
        logger.info(f"Starting download to {local_path}")
        file_size_mb = download_file(download_link, local_path)

        # Сохраняем JSON метаданные
        with open(os.path.join(json_dir, f'{video_id}.json'), 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved metadata for {video_id}")

        result['success'] = True
        result['file_size_mb'] = file_size_mb
        logger.info(f"✓ Successfully downloaded {video_id} ({file_size_mb:.1f} MB)")

    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Error downloading {video_id}: {e}")

    return result


def main():
    logger.info("Initializing Vimeo client...")

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
    successful_downloads = 0
    skipped_videos = 0
    failed_videos = 0
    failed_urls = []

    logger.info(f"\n{'='*80}")
    logger.info(f"STARTING DOWNLOAD PROCESS")
    logger.info(f"Total videos: {len(urls)}")
    logger.info(f"Delay between videos: {MIN_DELAY_BETWEEN_VIDEOS}-{MAX_DELAY_BETWEEN_VIDEOS}s")
    logger.info(f"{'='*80}\n")

    # Запускаем браузер с SeleniumBase UC Mode
    with SB(
        uc=True,              # Undetected Chrome Mode
        headless=False,       # Видимый браузер для обхода детекта
        disable_csp=True,
        block_images=False,   # Загружаем картинки (более естественно)
        incognito=False,
        chromium_arg="--disable-blink-features=AutomationControlled"
    ) as sb:

        logger.info("SeleniumBase UC Mode browser started")

        for i, video_url in enumerate(urls, 1):
            video_id = video_url.split('/')[-1]

            logger.info(f"\n{'='*80}")
            logger.info(f"[{i}/{len(urls)}] Processing video {video_id}")
            logger.info(f"URL: {video_url}")
            logger.info(f"{'='*80}")

            try:
                # Проверяем через API можно ли скачать
                logger.info(f"Checking video {video_id} via API...")
                response = client.get(f'https://api.vimeo.com/videos/{video_id}')

                # Check for API errors
                if response.status_code in [401, 403]:
                    logger.error(f"API Authentication error (status {response.status_code})")
                    telegram.notify_api_error(response.status_code, "Authentication error")
                    break

                if response.status_code == 429:
                    logger.error("API RATE LIMIT EXCEEDED")
                    telegram.notify_api_error(429, "API rate limit exceeded")
                    break

                json_data = response.json()

                # Check if downloadable
                try:
                    can_download = json_data['privacy']['download']
                except KeyError as ex:
                    logger.warning(f"Missing 'privacy.download' field for {video_id}")
                    skipped_videos += 1
                    continue

                if not can_download:
                    logger.info(f"Video {video_id} is not downloadable (privacy settings)")
                    skipped_videos += 1
                    current_num = successful_downloads + skipped_videos
                    telegram.notify_video_skipped(video_id, "Privacy settings", current_num, len(urls))

                    # Задержка даже для пропущенных видео
                    if i < len(urls):
                        delay = random.uniform(MIN_DELAY_BETWEEN_VIDEOS, MAX_DELAY_BETWEEN_VIDEOS)
                        logger.info(f"Delay before next video: {delay:.1f}s")
                        time.sleep(delay)
                    continue

                logger.info(f"Video {video_id} is downloadable, starting download...")

                # Скачиваем видео с имитацией человека
                result = download_video(sb, video_url, video_id, json_data, video_dir, json_dir)

                if result['success']:
                    successful_downloads += 1
                    telegram.notify_video_downloaded(
                        video_id,
                        f'{video_id}.mp4',
                        result['file_size_mb'],
                        successful_downloads,
                        len(urls)
                    )
                else:
                    failed_videos += 1
                    failed_urls.append({
                        'url': video_url,
                        'error': result['error'],
                        'stage': 'download'
                    })
                    logger.error(f"Failed to download {video_id}: {result['error']}")

                # Техника #1: Случайная задержка между видео
                if i < len(urls):
                    delay = random.uniform(MIN_DELAY_BETWEEN_VIDEOS, MAX_DELAY_BETWEEN_VIDEOS)
                    logger.info(f"Delay before next video: {delay:.1f}s\n")
                    time.sleep(delay)

            except Exception as e:
                logger.error(f"Unexpected error processing {video_id}: {e}")
                failed_videos += 1
                failed_urls.append({
                    'url': video_url,
                    'error': str(e),
                    'stage': 'api_check'
                })

    # Final statistics
    logger.info(f"\n{'='*80}")
    logger.info("DOWNLOAD PROCESS COMPLETED")
    logger.info(f"{'='*80}")
    logger.info(f"✅ Successfully downloaded: {successful_downloads}")
    logger.info(f"⚠️  Skipped (privacy):      {skipped_videos}")
    logger.info(f"❌ Failed:                 {failed_videos}")
    logger.info(f"📊 Total processed:        {successful_downloads + skipped_videos + failed_videos}")
    logger.info(f"{'='*80}\n")

    # Save failed URLs
    if failed_urls:
        failed_file = config['files']['failed_downloads']
        with open(failed_file, 'w', encoding='utf-8') as f:
            json.dump(failed_urls, f, indent=2, ensure_ascii=False)
        logger.info(f"Failed downloads saved to {failed_file}")

    # Send completion notification
    telegram.notify_completion(successful_downloads, skipped_videos, failed_videos, len(urls))

    logger.info("Script finished")


if __name__ == "__main__":
    main()
