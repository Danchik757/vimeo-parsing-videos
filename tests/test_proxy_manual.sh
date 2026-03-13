#!/bin/bash
# Ручная проверка прокси через curl

echo "============================================================"
echo "         ПРОВЕРКА ПРОКСИ ЧЕРЕЗ ТЕРМИНАЛ"
echo "============================================================"
echo ""

# Настройки
PROXY_HOST="45.11.125.162"
PROXY_PORT="9824"
PROXY_USER="Cf0rm2"
PROXY_PASS="y4XUAq"

echo "🌐 Прокси: $PROXY_HOST:$PROXY_PORT"
echo "👤 Логин: $PROXY_USER"
echo ""

# Тест 1: Ваш реальный IP
echo "------------------------------------------------------------"
echo "ШАГ 1: Проверяю ваш настоящий IP..."
echo "------------------------------------------------------------"
echo "Команда: curl https://api.ipify.org"
echo ""

REAL_IP=$(curl -s https://api.ipify.org 2>/dev/null)

if [ -n "$REAL_IP" ]; then
    echo "✅ Ваш IP: $REAL_IP"
    echo "   (Все сайты видят этот адрес)"
else
    echo "❌ Не удалось узнать IP"
fi

echo ""
echo "------------------------------------------------------------"
echo "ШАГ 2: Проверяю прокси..."
echo "------------------------------------------------------------"
echo "Команда: curl --proxy http://$PROXY_USER:***@$PROXY_HOST:$PROXY_PORT https://api.ipify.org"
echo ""

# Тест 2: IP через прокси
PROXY_IP=$(curl -s --proxy "http://$PROXY_USER:$PROXY_PASS@$PROXY_HOST:$PROXY_PORT" \
           --connect-timeout 10 \
           https://api.ipify.org 2>&1)

if echo "$PROXY_IP" | grep -q "Connection refused"; then
    echo "❌ ПРОКСИ НЕ РАБОТАЕТ"
    echo ""
    echo "   Ошибка: Connection refused (Отказано в подключении)"
    echo ""
    echo "   Что это значит:"
    echo "   • Прокси-сервер выключен"
    echo "   • Или прокси на другом адресе/порту"
    echo "   • Или нужна регистрация вашего IP"
    echo ""
elif echo "$PROXY_IP" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "✅ ПРОКСИ РАБОТАЕТ!"
    echo ""
    echo "   IP через прокси: $PROXY_IP"
    echo ""
    if [ "$REAL_IP" != "$PROXY_IP" ]; then
        echo "   🎉 ОТЛИЧНО! IP изменился:"
        echo "   Было:  $REAL_IP"
        echo "   Стало: $PROXY_IP"
        echo ""
        echo "   Теперь сайты видят IP прокси, а не ваш!"
    else
        echo "   ⚠️  IP не изменился. Прокси не используется."
    fi
else
    echo "❌ ПРОКСИ НЕ РАБОТАЕТ"
    echo ""
    echo "   Ошибка: $PROXY_IP"
fi

echo ""
echo "============================================================"
echo "                      ИТОГ"
echo "============================================================"
echo ""
echo "Что делать дальше:"
echo ""
echo "Если прокси НЕ работает:"
echo "  1. Свяжитесь с провайдером прокси"
echo "  2. Проверьте логин/пароль"
echo "  3. Добавьте ваш IP ($REAL_IP) в whitelist"
echo ""
echo "Пока прокси не работает:"
echo "  → Используйте ./run.sh (работает БЕЗ прокси)"
echo "  → Для тестирования прокси не нужен"
echo ""
echo "============================================================"
