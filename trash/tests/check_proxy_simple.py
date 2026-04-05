#!/usr/bin/env python3
"""Простая проверка прокси - понятный результат"""

import requests

print("\n" + "="*60)
print("         ПРОСТАЯ ПРОВЕРКА ПРОКСИ")
print("="*60 + "\n")

# НАСТРОЙКИ ПРОКСИ
PROXY = "45.11.125.162:9824"
USER = "Cf0rm2"
PASSWORD = "y4XUAq"

print(f"🌐 Прокси сервер: {PROXY}")
print(f"👤 Логин: {USER}")
print(f"🔒 Пароль: {'*' * len(PASSWORD)}\n")

# ШАГ 1: Узнаём ваш реальный IP
print("-" * 60)
print("ШАГ 1: Узнаю ваш настоящий IP адрес...")
print("-" * 60)

try:
    response = requests.get('https://api.ipify.org', timeout=5)
    real_ip = response.text
    print(f"✅ Ваш IP: {real_ip}")
    print(f"   (Это ваш настоящий адрес в интернете)\n")
except Exception as e:
    print(f"❌ Ошибка: {e}\n")
    real_ip = None

# ШАГ 2: Пробуем подключиться через прокси
print("-" * 60)
print("ШАГ 2: Пробую подключиться через прокси...")
print("-" * 60)

proxy_url = f"http://{USER}:{PASSWORD}@{PROXY}"
proxies = {
    'http': proxy_url,
    'https': proxy_url
}

try:
    print(f"Подключаюсь к прокси...")
    print(f"Адрес: {PROXY}")
    print(f"Авторизуюсь как: {USER}")

    response = requests.get('https://api.ipify.org',
                          proxies=proxies,
                          timeout=10)

    proxy_ip = response.text
    print(f"\n✅ ПРОКСИ РАБОТАЕТ!")
    print(f"   IP через прокси: {proxy_ip}")

    if real_ip and proxy_ip != real_ip:
        print(f"\n🎉 ОТЛИЧНО! IP изменился:")
        print(f"   Было:  {real_ip}")
        print(f"   Стало: {proxy_ip}")
        print(f"\n   Сайты видят IP прокси, а не ваш!")

except requests.exceptions.ProxyError:
    print(f"\n❌ ПРОКСИ НЕ РАБОТАЕТ")
    print(f"\n   Что случилось:")
    print(f"   Компьютер не смог подключиться к прокси")
    print(f"   Прокси сказал: 'Connection refused' (Отказано)")

    print(f"\n   Возможные причины:")
    print(f"   1. Прокси-сервер выключен")
    print(f"   2. Неправильный адрес: {PROXY}")
    print(f"   3. Неправильный логин/пароль")
    print(f"   4. Ваш IP нужно добавить в разрешённые")

except requests.exceptions.Timeout:
    print(f"\n❌ TIMEOUT (Время ожидания истекло)")
    print(f"\n   Что случилось:")
    print(f"   Прокси не ответил за 10 секунд")
    print(f"\n   Возможные причины:")
    print(f"   1. Прокси сервер очень медленный")
    print(f"   2. Прокси сервер недоступен")
    print(f"   3. Проблемы с интернетом")

except Exception as e:
    print(f"\n❌ ДРУГАЯ ОШИБКА")
    print(f"   {str(e)[:100]}")

print("\n" + "="*60)
print("                    ИТОГ")
print("="*60)

print("\nЧто делать дальше:\n")
print("✅ Если ПРОКСИ РАБОТАЕТ:")
print("   → Можно использовать для скачивания")
print("   → Запустите: python download_with_proxy.py\n")

print("❌ Если ПРОКСИ НЕ РАБОТАЕТ:")
print("   → Проверьте у провайдера прокси")
print("   → Или работайте БЕЗ прокси: ./run.sh")
print("   → Прокси не обязателен для тестирования\n")

print("="*60 + "\n")
