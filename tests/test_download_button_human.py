"""
Тест доступности кнопки Download на 20-30 видео
С имитацией человеческого поведения (без скачивания файлов)

Техники имитации человека:
1. Случайные задержки между запросами (3-8 секунд)
2. Имитация движения мыши (random mouse movements)
3. Скроллинг страницы (вверх-вниз)
4. Случайное время ожидания после загрузки (2-5 секунд)
5. "Чтение" информации о видео (пауза 1-3 секунды)
6. Прогрессивное увеличение задержек при детекте блокировки
"""

import json
import time
import random
from seleniumbase import SB
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains

# Конфигурация
TEST_VIDEO_COUNT = 30  # Количество видео для теста
MIN_DELAY_BETWEEN_VIDEOS = 3  # Минимальная задержка между видео (секунды)
MAX_DELAY_BETWEEN_VIDEOS = 8  # Максимальная задержка между видео (секунды)
PAGE_LOAD_WAIT_MIN = 2  # Минимальное ожидание после загрузки страницы
PAGE_LOAD_WAIT_MAX = 5  # Максимальное ожидание после загрузки страницы
READING_TIME_MIN = 1  # Минимальное время "чтения" информации
READING_TIME_MAX = 3  # Максимальное время "чтения" информации


def simulate_mouse_movement(sb):
    """
    Имитация случайного движения мыши
    Техника #2: Random mouse movements
    """
    try:
        actions = ActionChains(sb.driver)

        # Делаем 2-4 случайных движения мыши
        num_movements = random.randint(2, 4)
        for _ in range(num_movements):
            # Случайные координаты в пределах видимой области
            x_offset = random.randint(-200, 200)
            y_offset = random.randint(-200, 200)

            # Плавное движение с паузой
            actions.move_by_offset(x_offset, y_offset)
            actions.pause(random.uniform(0.1, 0.3))

        actions.perform()
        # Сброс позиции для следующего вызова
        actions.reset_actions()

    except Exception as e:
        # Игнорируем ошибки движения мыши (не критично)
        pass


def simulate_page_scroll(sb):
    """
    Имитация скроллинга страницы (человек просматривает контент)
    Техника #3: Page scrolling
    """
    try:
        # Скроллим вниз на случайное расстояние
        scroll_amount = random.randint(300, 600)
        sb.execute_script(f"window.scrollBy(0, {scroll_amount});")
        time.sleep(random.uniform(0.3, 0.7))

        # Скроллим обратно вверх (частично)
        scroll_back = random.randint(100, 300)
        sb.execute_script(f"window.scrollBy(0, -{scroll_back});")
        time.sleep(random.uniform(0.2, 0.5))

    except Exception as e:
        pass


def simulate_reading_pause():
    """
    Имитация чтения информации о видео
    Техника #5: "Reading" video information
    """
    reading_time = random.uniform(READING_TIME_MIN, READING_TIME_MAX)
    time.sleep(reading_time)


def check_if_cloudflare_blocked(sb):
    """
    Проверка блокировки Cloudflare Turnstile
    """
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


def check_download_button(sb, video_url):
    """
    Проверка доступности кнопки Download

    Returns:
        dict: {
            'url': str,
            'video_id': str,
            'status': 'success'|'blocked'|'no_button'|'error',
            'button_found': bool,
            'page_size': int,
            'cloudflare_detected': bool,
            'error_message': str|None
        }
    """
    video_id = video_url.split('/')[-1]
    result = {
        'url': video_url,
        'video_id': video_id,
        'status': 'unknown',
        'button_found': False,
        'page_size': 0,
        'cloudflare_detected': False,
        'error_message': None
    }

    try:
        # Открываем страницу
        sb.open(video_url)

        # Техника #4: Случайное ожидание после загрузки
        initial_wait = random.uniform(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX)
        time.sleep(initial_wait)

        # Получаем размер страницы
        page_source = sb.get_page_source()
        result['page_size'] = len(page_source)

        # Проверяем Cloudflare
        result['cloudflare_detected'] = check_if_cloudflare_blocked(sb)

        if result['cloudflare_detected']:
            result['status'] = 'blocked'
            result['error_message'] = 'Cloudflare Turnstile detected'
            print(f"  ⚠️  Cloudflare блокировка обнаружена")

            # Ждем дольше при обнаружении Cloudflare
            print(f"  ⏳ Ожидание автоматического прохождения (20 секунд)...")
            time.sleep(20)

            # Проверяем еще раз
            result['cloudflare_detected'] = check_if_cloudflare_blocked(sb)
            if not result['cloudflare_detected']:
                print(f"  ✅ Cloudflare успешно пройден!")
            else:
                print(f"  ❌ Cloudflare не пройден")
                return result

        # Техника #2: Имитация движения мыши
        simulate_mouse_movement(sb)

        # Техника #3: Скроллинг страницы
        simulate_page_scroll(sb)

        # Техника #5: "Чтение" информации
        simulate_reading_pause()

        # Ищем кнопку Download
        download_selectors = [
            "button[aria-label='Download']",
            "button[data-download-button]",
            "a[download]",
            "button:contains('Download')",
            ".download-button",
            "button.download"
        ]

        button_element = None
        for selector in download_selectors:
            try:
                if selector.startswith("button:contains"):
                    # Используем XPath для поиска по тексту
                    elements = sb.driver.find_elements(By.XPATH,
                        "//button[contains(text(), 'Download')]")
                    if elements:
                        button_element = elements[0]
                        break
                else:
                    elements = sb.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        button_element = elements[0]
                        break
            except:
                continue

        if button_element:
            result['button_found'] = True
            result['status'] = 'success'

            # Проверяем видимость и доступность кнопки
            is_displayed = button_element.is_displayed()
            is_enabled = button_element.is_enabled()

            print(f"  ✅ Кнопка найдена! (видима: {is_displayed}, активна: {is_enabled})")
        else:
            result['status'] = 'no_button'
            result['error_message'] = 'Download button not found'
            print(f"  ⚠️  Кнопка Download не найдена")

    except TimeoutException as e:
        result['status'] = 'error'
        result['error_message'] = f'Timeout: {str(e)}'
        print(f"  ❌ Таймаут загрузки")

    except Exception as e:
        result['status'] = 'error'
        result['error_message'] = str(e)
        print(f"  ❌ Ошибка: {str(e)}")

    return result


def main():
    print("=" * 80)
    print("ТЕСТ ДОСТУПНОСТИ КНОПКИ DOWNLOAD")
    print("С имитацией человеческого поведения (без скачивания)")
    print("=" * 80)

    # Загружаем список видео
    with open('need_parse_unique.json', 'r') as f:
        all_videos = json.load(f)

    # Берем первые N видео для теста
    test_videos = all_videos[:TEST_VIDEO_COUNT]

    print(f"\n📊 Конфигурация теста:")
    print(f"   • Количество видео: {len(test_videos)}")
    print(f"   • Задержка между видео: {MIN_DELAY_BETWEEN_VIDEOS}-{MAX_DELAY_BETWEEN_VIDEOS} сек")
    print(f"   • Ожидание после загрузки: {PAGE_LOAD_WAIT_MIN}-{PAGE_LOAD_WAIT_MAX} сек")
    print(f"   • Время 'чтения': {READING_TIME_MIN}-{READING_TIME_MAX} сек")
    print()
    print("🎭 Техники имитации человека:")
    print("   1. Случайные задержки между запросами")
    print("   2. Имитация движения мыши (random movements)")
    print("   3. Скроллинг страницы (вверх-вниз)")
    print("   4. Случайное ожидание после загрузки")
    print("   5. Пауза на чтение информации о видео")
    print()

    results = []
    stats = {
        'success': 0,
        'blocked': 0,
        'no_button': 0,
        'error': 0
    }

    # Запускаем браузер с SeleniumBase UC Mode
    with SB(
        uc=True,              # Undetected Chrome Mode
        headless=False,       # Видимый браузер для визуального контроля
        disable_csp=True,
        block_images=False,   # Загружаем картинки (более естественно)
        incognito=False,
        chromium_arg="--disable-blink-features=AutomationControlled"
    ) as sb:

        print(f"🚀 Начинаем тестирование...\n")

        for i, video_url in enumerate(test_videos, 1):
            video_id = video_url.split('/')[-1]

            print(f"[{i}/{len(test_videos)}] Проверка видео {video_id}")
            print(f"     URL: {video_url}")

            # Проверяем кнопку Download
            result = check_download_button(sb, video_url)
            results.append(result)

            # Обновляем статистику
            stats[result['status']] += 1

            # Техника #1: Случайная задержка между видео
            if i < len(test_videos):  # Не ждем после последнего видео
                delay = random.uniform(MIN_DELAY_BETWEEN_VIDEOS, MAX_DELAY_BETWEEN_VIDEOS)
                print(f"  ⏱  Задержка перед следующим видео: {delay:.1f} сек\n")
                time.sleep(delay)

        print("\n" + "=" * 80)
        print("РЕЗУЛЬТАТЫ ТЕСТА")
        print("=" * 80)
        print(f"\n📊 Статистика:")
        print(f"   ✅ Успешно (кнопка найдена):     {stats['success']:3d} ({stats['success']/len(test_videos)*100:.1f}%)")
        print(f"   ⚠️  Кнопка не найдена:            {stats['no_button']:3d} ({stats['no_button']/len(test_videos)*100:.1f}%)")
        print(f"   🚫 Заблокировано Cloudflare:     {stats['blocked']:3d} ({stats['blocked']/len(test_videos)*100:.1f}%)")
        print(f"   ❌ Ошибки:                       {stats['error']:3d} ({stats['error']/len(test_videos)*100:.1f}%)")
        print(f"   📈 ИТОГО:                        {len(test_videos):3d}")

        # Детальные результаты
        print(f"\n📋 Детальные результаты:")
        print(f"{'#':<4} {'ID':<15} {'Статус':<15} {'Размер HTML':<12} {'Cloudflare':<10}")
        print("-" * 70)

        for i, result in enumerate(results, 1):
            status_icon = {
                'success': '✅',
                'no_button': '⚠️ ',
                'blocked': '🚫',
                'error': '❌'
            }.get(result['status'], '?')

            cloudflare = '🔥' if result['cloudflare_detected'] else '✓'

            print(f"{i:<4} {result['video_id']:<15} {status_icon} {result['status']:<13} "
                  f"{result['page_size']:>8,} bytes  {cloudflare}")

        # Сохраняем результаты в JSON
        output_file = 'output/download_button_test_results.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'test_config': {
                    'video_count': len(test_videos),
                    'delay_range': f"{MIN_DELAY_BETWEEN_VIDEOS}-{MAX_DELAY_BETWEEN_VIDEOS}s",
                    'techniques': [
                        'Random delays between requests',
                        'Mouse movement simulation',
                        'Page scrolling',
                        'Random wait after page load',
                        'Reading pause simulation'
                    ]
                },
                'statistics': stats,
                'results': results
            }, f, indent=2, ensure_ascii=False)

        print(f"\n💾 Результаты сохранены: {output_file}")

        # Анализ и рекомендации
        print(f"\n💡 Анализ и рекомендации:")

        success_rate = stats['success'] / len(test_videos) * 100
        blocked_rate = stats['blocked'] / len(test_videos) * 100

        if success_rate >= 90:
            print(f"   🎉 Отлично! {success_rate:.0f}% успешных проверок.")
            print(f"   ✓ Cloudflare успешно обходится")
            print(f"   ✓ Можно интегрировать в основной скрипт")
        elif success_rate >= 70:
            print(f"   ⚠️  Хорошо, но есть проблемы: {success_rate:.0f}% успеха")
            print(f"   → Рекомендуется увеличить задержки между запросами")
        elif blocked_rate > 30:
            print(f"   🚫 Высокий процент блокировок: {blocked_rate:.0f}%")
            print(f"   → Cloudflare детектирует автоматизацию")
            print(f"   → Нужны дополнительные техники обхода")
        else:
            print(f"   ❌ Низкий успех: {success_rate:.0f}%")
            print(f"   → Проблемы с поиском кнопки Download")

        # Средний размер страницы
        avg_page_size = sum(r['page_size'] for r in results) / len(results)
        print(f"\n📏 Средний размер HTML: {avg_page_size:,.0f} байт")

        if avg_page_size < 20000:
            print(f"   ⚠️  Слишком маленький размер - возможна блокировка")
        elif avg_page_size > 100000:
            print(f"   ✅ Нормальный размер - страницы загружаются полностью")

        print(f"\n⏱  Общее время теста: ~{len(test_videos) * (MIN_DELAY_BETWEEN_VIDEOS + MAX_DELAY_BETWEEN_VIDEOS) / 2 / 60:.1f} минут")
        print(f"✅ Тест завершён!\n")


if __name__ == "__main__":
    main()
