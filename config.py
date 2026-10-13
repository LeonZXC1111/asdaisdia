import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_USERNAME = os.environ["BOT_USERNAME"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
WEBAPP_URL = os.environ["WEBAPP_URL"]
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
DB_PATH = os.getenv("DB_PATH", "vpn.db")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/WispieVpn_supp")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@WispieVpn_supp")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET = os.getenv("YOOKASSA_SECRET", "")

MARZBAN_URL = os.getenv("MARZBAN_URL", "")
MARZBAN_USER = os.getenv("MARZBAN_USER", "")
MARZBAN_PASS = os.getenv("MARZBAN_PASS", "")
VPN_INBOUNDS = [x.strip() for x in os.getenv("VPN_INBOUNDS", "VLESS TCP REALITY").split(",")]

REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "7"))
COINS_PER_PURCHASE = int(os.getenv("COINS_PER_PURCHASE", "10"))

# дни -> цена в рублях
PLANS = {30: 199, 60: 499, 90: 699}

# Магазин койнов
SHOP = {
    "sub14": {"title": "14 дней подписки", "cost": 30, "days": 14},
    "sub30": {"title": "30 дней подписки", "cost": 50, "days": 30},
}

# Устройства: базовый лимит и цена каждого дополнительного устройства по порядку
DEVICE_BASE = 3
DEVICE_EXTRA_PRICES = [35, 35, 45, 55, 65, 75, 85, 95]
DEVICE_MAX_EXTRA = len(DEVICE_EXTRA_PRICES)

