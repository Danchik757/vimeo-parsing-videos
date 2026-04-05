"""
Vimeo Video Downloader - ФИНАЛЬНАЯ ВЕРСИЯ

ОБЪЕДИНЯЕТ:
1. ✅ Рабочую логику поиска кнопки Download из download_vimeo.py:256
2. ✅ SeleniumBase UC Mode для обхода Cloudflare
3. ✅ Исправленный check_if_cloudflare_blocked() без ложных срабатываний
4. ✅ ДОВЕРЯЕМ API privacy.download (не игнорируем!)
5. ✅ Обработка платных видео (403, error_code 3410)
6. ✅ Прогресс уведомления каждые 5 видео
7. ✅ Создание failed_downloads.json со списком неудачных скачиваний
"""

import os
import json
import time
import random
import logging
import urllib.request
from datetime import datetime
from seleniumbase import SB
import vimeo

from telegram_notifier import TelegramNotifier
from vimeo_cdp_helpers import (
    click_download_button,
    extract_best_api_download,
    extract_best_modal_download,
)

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

# Setup logging
log_dir = 'output/logs'
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'download.log')

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
logger.info("VIMEO DOWNLOADER FINAL (SeleniumBase UC Mode)")
logger.info("=" * 80)

# Initialize Telegram notifier
telegram = TelegramNotifier(config)

# Initialize Vimeo client
client = vimeo.VimeoClient(
    token=config['vimeo_api']['token'],
    key=config['vimeo_api']['client_id'],
    secret=config['vimeo_api']['secret']
)
logger.info("Vimeo client initialized")

# Параметры
MIN_DELAY_BETWEEN_VIDEOS = 3
MAX_DELAY_BETWEEN_VIDEOS = 8
CLOUDFLARE_TIMEOUT_MIN = 40
CLOUDFLARE_TIMEOUT_MAX = 60
MAX_RETRIES = 3

def check_if_cloudflare_blocked(sb):
    """
    Проверка блокировки Cloudflare (БЕЗ ложных срабатываний!)
    ИСПРАВЛЕНО: НЕ использует слово "captcha" (ловит recaptchasitekey)
    """
    try:
        page_source = sb.get_page_source().lower()
        page_title = sb.get_title().lower()

        # Проверка #1: Если title содержит " on vimeo" - страница загрузилась!
        if ' on vimeo' in page_title:
            return False

        # Более точные индикаторы Cloudflare (НЕ "captcha"!)
        cloudflare_indicators = [
            'cloudflare turnstile',
            'verify you are human',
            'verify to continue',
            'challenge-platform',
            'just a moment',
            'cf-challenge'
        ]

        for indicator in cloudflare_indicators:
            if indicator in page_source:
                return True

        return False
    except:
        return False


def download_file(url, local_filename):
    """Download file from URL to local path"""
    urllib.request.urlretrieve(url, local_filename)
    file_size_mb = os.path.getsize(local_filename) / (1024 * 1024)
    logger.info(f"Downloaded: {local_filename} ({file_size_mb:.1f} MB)")


def download_video_with_selenium(sb, video_id, video_url, json_data=None):
    """
    Download video using SeleniumBase
    ОБЪЕДИНЯЕТ: старую логику + новый обход Cloudflare
    """
    result = {
        'success': False,
        'error': None,
        'downloaded_file': None
    }

    try:
        api_download = extract_best_api_download(json_data or {})
        if api_download:
            download_link = api_download['href']
            logger.info(
                "Using direct API download link for %s (%s)",
                video_id,
                api_download.get('text') or api_download.get('quality') or 'best available',
            )
        else:
            # Открываем страницу
            logger.info(f"Opening {video_url}")
            sb.open(video_url)

            # Ждем загрузки страницы
            wait_time = random.uniform(3, 5)
            logger.info(f"Waiting {wait_time:.1f}s for page load...")
            sb.sleep(wait_time)

            # Проверяем Cloudflare
            cloudflare_retry_count = 0
            max_cloudflare_retries = 3

            while check_if_cloudflare_blocked(sb) and cloudflare_retry_count < max_cloudflare_retries:
                cloudflare_timeout = random.uniform(CLOUDFLARE_TIMEOUT_MIN, CLOUDFLARE_TIMEOUT_MAX)
                cloudflare_timeout += cloudflare_retry_count * 10  # Увеличиваем с каждой попыткой

                logger.warning(f"Cloudflare detected, waiting {cloudflare_timeout:.0f}s...")
                sb.sleep(cloudflare_timeout)

                cloudflare_retry_count += 1

            # Финальная проверка
            if check_if_cloudflare_blocked(sb):
                result['error'] = 'Cloudflare Turnstile not bypassed'
                logger.error(f"❌ Cloudflare still blocking after {cloudflare_retry_count} attempts")
                return result

            logger.info("✓ Page loaded successfully (no Cloudflare)")

            # Ждем немного для загрузки JavaScript
            sb.sleep(2)

            try:
                click_download_button(sb, timeout=10, logger=logger)
            except Exception as exc:
                result['error'] = 'Download button not found'
                logger.error(f"Download button not found for {video_id}: {exc}")
                return result

            try:
                best_option = extract_best_modal_download(sb, timeout=10, logger=logger)
            except Exception as exc:
                result['error'] = str(exc)
                logger.error(str(exc))
                return result

            download_link = best_option['href']
            logger.info("Got download link")

        # Определяем расширение файла
        ext = '.mp4'
        if ext not in download_link.lower():
            ext = '.mov'
            if ext not in download_link.lower():
                ext = ''

        # Скачиваем файл
        local_path = f'output/videos/{video_id}{ext}'
        logger.info(f"Starting download to {local_path}")
        download_file(download_link, local_path)

        result['success'] = True
        result['downloaded_file'] = f'{video_id}{ext}'
        logger.info(f"✓ Successfully downloaded {video_id}")

        return result

    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Error downloading {video_id}: {e}")
        return result


def main():
    logger.info("Loading URLs from need_parse_unique.json")

    with open('need_parse_unique.json', 'r') as f:
        urls = json.load(f)

    # Test mode
    if config['settings']['test_mode']:
        test_limit = config['settings']['test_limit']
        urls = urls[:test_limit]
        logger.info(f"TEST MODE: Processing only first {test_limit} videos")

    # Directories
    video_dir = 'output/videos'
    json_dir = 'output/jsons'
    os.makedirs(video_dir, exist_ok=True)
    os.makedirs(json_dir, exist_ok=True)

    # Stats
    successful_downloads = 0
    skipped_videos = 0
    failed_videos = 0
    failed_list = []  # Список неудачных скачиваний

    # Send start notification
    telegram.notify_start(len(urls))

    logger.info(f"Starting download process for {len(urls)} videos")

    # Start SeleniumBase browser
    with SB(uc=True, headless=False) as sb:
        logger.info("SeleniumBase UC Mode browser started")

        for i, url in enumerate(urls, 1):
            video_id = url.split('/')[-1]

            logger.info("=" * 80)
            logger.info(f"[{i}/{len(urls)}] Processing video {video_id}")
            logger.info(f"URL: {url}")
            logger.info("=" * 80)

            try:
                # Проверяем видео через API
                logger.info(f"Checking video {video_id} via API...")
                response = client.get(f'/videos/{video_id}')

                # Обработка ошибок API
                if response.status_code == 403:
                    data = response.json()
                    if data.get('error_code') == 3410:
                        logger.info(f"Video {video_id} is On Demand (paid), skipping")
                        skipped_videos += 1
                        failed_list.append({
                            'video_id': video_id,
                            'url': url,
                            'reason': 'paid_video',
                            'error': 'On Demand (paid content)'
                        })
                        continue

                if response.status_code == 404:
                    logger.warning(f"Video {video_id} not found (404)")
                    skipped_videos += 1
                    failed_list.append({
                        'video_id': video_id,
                        'url': url,
                        'reason': 'not_found',
                        'error': '404 Not Found'
                    })
                    continue

                if response.status_code != 200:
                    logger.error(f"API error {response.status_code} for {video_id}")
                    failed_videos += 1
                    failed_list.append({
                        'video_id': video_id,
                        'url': url,
                        'reason': 'api_error',
                        'error': f'API status {response.status_code}'
                    })
                    continue

                json_data = response.json()

                # КРИТИЧЕСКИ ВАЖНО: ДОВЕРЯЕМ API privacy.download!
                if not json_data.get('privacy', {}).get('download', False):
                    logger.info(f"Video {video_id} is not downloadable (privacy.download = false)")
                    skipped_videos += 1
                    failed_list.append({
                        'video_id': video_id,
                        'url': url,
                        'reason': 'not_downloadable',
                        'error': 'privacy.download = false (no Download button on page)'
                    })
                    continue

                # Видео можно скачивать, начинаем!
                logger.info(f"✓ Video {video_id} is downloadable (privacy.download = true)")

                # Скачиваем через Selenium
                result = download_video_with_selenium(sb, video_id, url, json_data=json_data)

                if result['success']:
                    # Сохраняем JSON ТОЛЬКО после успешного скачивания
                    json_path = os.path.join(json_dir, f'{video_id}.json')
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(json_data, f, indent=2, ensure_ascii=False)
                    logger.info(f"✓ Saved metadata for {video_id}")

                    successful_downloads += 1

                    # Telegram notification
                    file_path = os.path.join(video_dir, result['downloaded_file'])
                    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                    telegram.notify_video_downloaded(
                        video_id,
                        result['downloaded_file'],
                        file_size_mb,
                        successful_downloads,
                        len(urls)
                    )
                else:
                    # Не удалось скачать
                    failed_videos += 1
                    failed_list.append({
                        'video_id': video_id,
                        'url': url,
                        'reason': 'download_failed',
                        'error': result['error']
                    })
                    logger.error(f"Failed to download {video_id}: {result['error']}")

            except Exception as e:
                failed_videos += 1
                failed_list.append({
                    'video_id': video_id,
                    'url': url,
                    'reason': 'exception',
                    'error': str(e)
                })
                logger.error(f"Exception for {video_id}: {e}")

            # Прогресс уведомление каждые 5 видео
            if i % 5 == 0:
                telegram.notify_progress(i, len(urls), successful_downloads, skipped_videos, failed_videos)

            # Задержка между видео
            if i < len(urls):
                delay = random.uniform(MIN_DELAY_BETWEEN_VIDEOS, MAX_DELAY_BETWEEN_VIDEOS)
                logger.info(f"Delay before next video: {delay:.1f}s")
                sb.sleep(delay)

    # Сохраняем список неудачных скачиваний
    failed_file = 'output/failed_downloads.json'
    with open(failed_file, 'w', encoding='utf-8') as f:
        json.dump({
            'total_failed': len(failed_list),
            'timestamp': datetime.now().isoformat(),
            'failed_videos': failed_list
        }, f, indent=2, ensure_ascii=False)
    logger.info(f"✓ Saved failed downloads list to {failed_file}")

    # Final summary
    logger.info("=" * 80)
    logger.info("DOWNLOAD COMPLETE")
    logger.info("=" * 80)
    logger.info(f"✅ Successfully downloaded: {successful_downloads}")
    logger.info(f"⏭  Skipped: {skipped_videos}")
    logger.info(f"❌ Failed: {failed_videos}")
    logger.info(f"📄 Failed list saved to: {failed_file}")
    logger.info("=" * 80)

    # Send final notification
    telegram.notify_finish(successful_downloads, skipped_videos, failed_videos)


if __name__ == "__main__":
    main()
