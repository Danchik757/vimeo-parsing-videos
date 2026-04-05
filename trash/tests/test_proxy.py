#!/usr/bin/env python3
"""Тест прокси - проверка работоспособности"""

import requests
from requests.auth import HTTPProxyAuth
import json

# Ваш прокси
PROXY_USER = "Cf0rm2"
PROXY_PASS = "y4XUAq"
PROXY_HOST = "45.11.125.162"
PROXY_PORT = "9824"

print("=" * 60)
print("ПРОВЕРКА ПРОКСИ")
print("=" * 60)

# Формируем URL прокси
proxy_url = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"

proxies = {
    'http': proxy_url,
    'https': proxy_url
}

print(f"\nПрокси: {PROXY_HOST}:{PROXY_PORT}")
print(f"Логин: {PROXY_USER}")
print(f"Пароль: {'*' * len(PROXY_PASS)}")

# Тест 1: Проверка IP БЕЗ прокси
print("\n" + "=" * 60)
print("Тест 1: Ваш реальный IP (без прокси)")
print("=" * 60)
try:
    response = requests.get('https://api.ipify.org?format=json', timeout=10)
    real_ip = response.json()['ip']
    print(f"✓ Ваш реальный IP: {real_ip}")
except Exception as e:
    print(f"✗ Ошибка: {e}")
    real_ip = None

# Тест 2: Проверка IP ЧЕРЕЗ прокси
print("\n" + "=" * 60)
print("Тест 2: IP через прокси")
print("=" * 60)
try:
    response = requests.get('https://api.ipify.org?format=json',
                          proxies=proxies,
                          timeout=15)
    proxy_ip = response.json()['ip']
    print(f"✓ IP через прокси: {proxy_ip}")

    if real_ip and proxy_ip != real_ip:
        print(f"✓ ПРОКСИ РАБОТАЕТ! IP изменился: {real_ip} → {proxy_ip}")
    elif real_ip:
        print(f"✗ ПРОКСИ НЕ РАБОТАЕТ! IP не изменился: {proxy_ip}")

except requests.exceptions.ProxyError as e:
    print(f"✗ Ошибка прокси: {e}")
    print("\nВозможные причины:")
    print("1. Неправильный логин/пароль")
    print("2. Прокси не работает")
    print("3. Прокси требует whitelist (добавить ваш IP в разрешённые)")
except requests.exceptions.ConnectTimeout:
    print(f"✗ Timeout: Прокси не отвечает")
    print("\nВозможные причины:")
    print("1. Прокси сервер недоступен")
    print("2. Порт заблокирован фаерволом")
except Exception as e:
    print(f"✗ Неизвестная ошибка: {e}")

# Тест 3: Проверка доступа к Vimeo через прокси
print("\n" + "=" * 60)
print("Тест 3: Доступ к Vimeo через прокси")
print("=" * 60)
try:
    response = requests.get('https://vimeo.com',
                          proxies=proxies,
                          timeout=15,
                          allow_redirects=True)

    if response.status_code == 200:
        print(f"✓ Vimeo доступен через прокси (код: {response.status_code})")
    else:
        print(f"⚠ Vimeo ответил кодом: {response.status_code}")

except Exception as e:
    print(f"✗ Ошибка доступа к Vimeo: {e}")

print("\n" + "=" * 60)
print("ИТОГО")
print("=" * 60)
print("\nЕсли все 3 теста прошли успешно - прокси работает!")
print("Можно использовать для скачивания видео.")
