#!/usr/bin/env python3
"""
Test SeleniumBase UC Mode для обхода Cloudflare Turnstile
Образовательный пример обхода капчи
"""

from seleniumbase import SB
import time

def test_vimeo_with_seleniumbase():
    """
    Тест доступа к Vimeo через SeleniumBase UC Mode
    UC Mode автоматически пытается обойти Cloudflare
    """

    print("=" * 60)
    print("SeleniumBase UC Mode Test - Cloudflare Bypass")
    print("=" * 60)

    # UC Mode параметры
    with SB(
        uc=True,              # Undetected Chrome Mode
        headless=False,       # Видимый браузер (для теста)
        disable_csp=True,     # Отключить Content Security Policy
        block_images=False,   # Не блокировать картинки
        incognito=False       # НЕ инкогнито (использовать профиль)
    ) as sb:

        test_url = "https://vimeo.com/1071902981"

        print(f"\n🔍 Открываем: {test_url}")
        print("⏳ Подождите 5-10 секунд...")

        # Открыть страницу
        sb.open(test_url)

        # Подождать начальной загрузки
        sb.sleep(5)

        # Получить размер страницы
        page_source = sb.get_page_source()
        page_size = len(page_source)
        title = sb.get_title()

        print(f"\n📄 Title: {title}")
        print(f"📏 Размер HTML: {page_size:,} байт")

        # Проверить наличие Cloudflare
        if "cloudflare" in page_source.lower() or "turnstile" in page_source.lower():
            print("\n⚠️ Cloudflare Turnstile обнаружен!")
            print("⏳ Ожидание автоматического прохождения (20 секунд)...")

            # SeleniumBase UC Mode иногда проходит капчу автоматически
            sb.sleep(20)

            # Обновить информацию
            page_source = sb.get_page_source()
            page_size = len(page_source)

            print(f"📏 Размер после ожидания: {page_size:,} байт")

            if page_size > 50000:
                print("✅ Cloudflare пройден! Страница загружена.")
            else:
                print("❌ Cloudflare всё ещё активен.")
                print("💡 Попробуйте подождать дольше или используйте другой метод.")

        elif page_size < 15000:
            print("\n⚠️ Маленький размер страницы - возможна блокировка")
            print(f"Первые 500 символов:\n{page_source[:500]}")
        else:
            print("\n✅ Страница загружена успешно!")
            print("🎯 Cloudflare не обнаружен или пройден автоматически.")

        # Сохранить скриншот
        screenshot_path = "output/seleniumbase_test.png"
        sb.save_screenshot(screenshot_path)
        print(f"\n📸 Скриншот сохранён: {screenshot_path}")

        # Сохранить HTML для анализа
        with open("output/seleniumbase_page.html", "w", encoding="utf-8") as f:
            f.write(page_source)
        print(f"💾 HTML сохранён: output/seleniumbase_page.html")

        # Оставить браузер открытым для просмотра
        print("\n⏸ Браузер останется открытым 15 секунд для просмотра...")
        sb.sleep(15)

        print("\n✅ Тест завершён")

if __name__ == "__main__":
    try:
        test_vimeo_with_seleniumbase()
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
