VPN Bot: установка

1. Создайте бота в @BotFather, возьмите токен.
2. Скопируйте .env.example в .env и заполните значения.
3. pip install -r requirements.txt
4. python main.py
5. Для работы в фоне оформите как systemd-сервис.
6. Снаружи нужен HTTPS (nginx -> 127.0.0.1:8000).
   Адрес вебхука ЮKassa: https://your-domain.com/webhook/yookassa (событие payment.succeeded)

Код не проверен на реальных сервисах: сначала протестируйте оплату в тестовом режиме ЮKassa.
