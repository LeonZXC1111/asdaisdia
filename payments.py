import uuid
import httpx
from config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET, WEBAPP_URL

API = "https://api.yookassa.ru/v3/payments"


def _auth():
    return (YOOKASSA_SHOP_ID, YOOKASSA_SECRET)


async def create_payment(user_id, amount, description, kind="plan", days=0):
    """kind: 'plan' — оплата подписки, 'topup' — пополнение баланса."""
    body = {
        "amount": {"value": f"{amount}.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": WEBAPP_URL},
        "description": description,
        "metadata": {"user_id": str(user_id), "days": str(days), "kind": kind},
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(API, json=body, auth=_auth(),
                         headers={"Idempotence-Key": str(uuid.uuid4())})
        r.raise_for_status()
        data = r.json()
    return data["id"], data["confirmation"]["confirmation_url"]


async def fetch_payment(pid):
    """Перепроверяем платёж в API ЮKassa, а не доверяем телу вебхука."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{API}/{pid}", auth=_auth())
        r.raise_for_status()
        return r.json()
