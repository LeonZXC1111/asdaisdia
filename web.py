import asyncio
import hashlib
import logging
import re
import secrets
import uuid
import hmac
import json
import math
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import db
import payments
import service
from bot import open_app_kb
from vpn import vpn
from config import (BOT_TOKEN, BOT_USERNAME, ADMIN_ID, PLANS, REFERRAL_BONUS_DAYS,
                    SHOP, COINS_PER_PURCHASE, SUPPORT_URL, DEVICE_BASE, DEVICE_EXTRA_PRICES, DEVICE_MAX_EXTRA)

app = FastAPI(docs_url=None, redoc_url=None)
HTML = Path(__file__).parent / "webapp" / "index.html"
coin_lock = asyncio.Lock()
log = logging.getLogger(__name__)


def verify_init_data(init_data: str):
    """Проверка подписи Telegram WebApp. Без неё никто не сможет притвориться другим пользователем."""
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received = data.pop("hash", None)
    if not received or "user" not in data:
        return None
    if time.time() - int(data.get("auth_date", 0)) > 86400:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    return json.loads(data["user"])


async def current_user(x_init_data: str = Header(default="")):
    u = verify_init_data(x_init_data)
    if not u:
        raise HTTPException(401, "Откройте приложение из Telegram")
    user, _ = await db.ensure_user(u["id"], u.get("username"), u.get("first_name"))
    if user["banned"]:
        raise HTTPException(403, "Доступ ограничен")
    return u


async def admin_user(u=Depends(current_user)):
    if u["id"] != ADMIN_ID:
        raise HTTPException(403, "Нет доступа")
    return u


def remaining_days(until):
    if not until or until <= db.now():
        return 0
    return math.ceil((until - db.now()).total_seconds() / 86400)


@app.get("/app", response_class=HTMLResponse)
async def page():
    return HTML.read_text(encoding="utf-8")


# ---------- пользователь ----------

@app.get("/api/me")
async def me(u=Depends(current_user)):
    user = await db.get_user(u["id"])
    until = db.parse(user["sub_until"])
    active = bool(until and until > db.now())
    refs = await db.fetchone("SELECT COUNT(*) AS c FROM users WHERE referrer_id=?", (u["id"],))
    paid = await db.fetchone(
        "SELECT COUNT(*) AS c FROM users WHERE referrer_id=? AND ref_rewarded=1", (u["id"],))
    await ensure_primary(u["id"])
    dev_rows = await db.list_devices(u["id"])
    return {
        "id": u["id"],
        "name": u.get("first_name", ""),
        "is_admin": u["id"] == ADMIN_ID,
        "active": active,
        "until": user["sub_until"],
        "days_left": remaining_days(until),
        "sub_url": user["sub_url"] if active else None,
        "coins": user["coins"] or 0,
        "balance": user["balance"] or 0,
        "discount_pct": user["discount_pct"] or 0,
        "plans": [{"days": d, "price": p} for d, p in PLANS.items()],
        "shop": [{"id": k, "title": v["title"], "cost": v["cost"]} for k, v in SHOP.items()],
        "coins_per_purchase": COINS_PER_PURCHASE,
        "ref_link": f"https://t.me/{BOT_USERNAME}?start=ref{u['id']}",
        "ref_count": refs["c"],
        "ref_paid": paid["c"],
        "ref_bonus": REFERRAL_BONUS_DAYS,
        "email": user["email"],
        "devices_used": sum(1 for r in dev_rows if r["active"]),
        "devices_limit": DEVICE_BASE + (user["device_extra"] or 0),
        "device_extra": user["device_extra"] or 0,
        "device_prices": DEVICE_EXTRA_PRICES,
        "device_base": DEVICE_BASE,
        "max_extra": DEVICE_MAX_EXTRA,
        "faq_url": await db.get_setting("tg_faq_url"),
        "support_url": SUPPORT_URL,
    }


class PayIn(BaseModel):
    days: int


@app.post("/api/pay")
async def pay(body: PayIn, u=Depends(current_user)):
    if body.days not in PLANS:
        raise HTTPException(400, "Неизвестный тариф")
    user = await db.get_user(u["id"])
    disc = user["discount_pct"] or 0
    coupon = user["pending_coupon"] if disc else None
    amount = round(PLANS[body.days] * (100 - disc) / 100)
    pid, url = await payments.create_payment(u["id"], amount, f"VPN, подписка на {body.days} дн.", "plan", body.days)
    await db.execute(
        "INSERT INTO payments (id, user_id, amount, days, status, created_at, discount, coupon) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (pid, u["id"], amount, body.days, "pending", db.iso(db.now()), disc, coupon),
    )
    return {"url": url}


class CouponIn(BaseModel):
    code: str


@app.post("/api/coupon")
async def apply_coupon(body: CouponIn, u=Depends(current_user)):
    code = body.code.strip().upper()
    user = await db.get_user(u["id"])
    if user["discount_pct"]:
        raise HTTPException(400, "Скидка уже активна")
    c = await db.valid_coupon(code, u["id"])
    if not c:
        raise HTTPException(400, "Промокод не найден или уже использован")
    await db.execute("UPDATE users SET discount_pct=?, pending_coupon=? WHERE id=?",
                     (c["percent"], code, u["id"]))
    return {"percent": c["percent"]}


class ShopIn(BaseModel):
    item: str


@app.post("/api/shop/buy")
async def shop_buy(body: ShopIn, u=Depends(current_user)):
    item = SHOP.get(body.item)
    if not item:
        raise HTTPException(400, "Нет такого товара")
    uid = u["id"]
    async with coin_lock:
        user = await db.get_user(uid)
        if "discount" in item and user["discount_pct"]:
            raise HTTPException(400, "Скидка уже активна")
        if not await db.try_spend(uid, item["cost"], f"shop_{body.item}"):
            raise HTTPException(400, "Недостаточно койнов")
        if "discount" in item:
            await db.execute("UPDATE users SET discount_pct=?, pending_coupon=NULL WHERE id=?",
                             (item["discount"], uid))
    if "days" in item:
        until, ok = await service.activate(uid, item["days"])
        return {
            "until": db.iso(until),
            "warning": None if ok else "Подписка продлена, но VPN-доступ не создан. Проверьте Marzban.",
        }
    return {"ok": True}


class TopupIn(BaseModel):
    amount: int


@app.post("/api/topup")
async def topup(body: TopupIn, u=Depends(current_user)):
    if not (100 <= body.amount <= 20000):
        raise HTTPException(400, "Сумма от 100 до 20 000 ₽")
    pid, url = await payments.create_payment(u["id"], body.amount, "Пополнение баланса", "topup", 0)
    await db.execute(
        "INSERT INTO payments (id, user_id, amount, days, status, created_at, discount, coupon, kind) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, u["id"], body.amount, 0, "pending", db.iso(db.now()), 0, None, "topup"),
    )
    return {"url": url}


@app.post("/api/pay_balance")
async def pay_balance(body: PayIn, request: Request, u=Depends(current_user)):
    if body.days not in PLANS:
        raise HTTPException(400, "Неизвестный тариф")
    uid = u["id"]
    user = await db.get_user(uid)
    disc = user["discount_pct"] or 0
    coupon = user["pending_coupon"] if disc else None
    amount = round(PLANS[body.days] * (100 - disc) / 100)
    async with coin_lock:
        if not await db.try_spend_balance(uid, amount):
            raise HTTPException(400, "Недостаточно средств на балансе")
    await db.execute(
        "INSERT INTO payments (id, user_id, amount, days, status, created_at, discount, coupon, kind) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (f"bal_{uuid.uuid4()}", uid, amount, body.days, "succeeded", db.iso(db.now()), disc, coupon, "balance"),
    )
    until = await service.on_paid(uid, body.days, coupon, request.app.state.bot)
    return {"until": db.iso(until)}


@app.post("/webhook/yookassa")
async def yookassa_hook(request: Request):
    event = await request.json()
    if event.get("event") != "payment.succeeded":
        return {"ok": True}
    pid = event["object"]["id"]
    payment = await payments.fetch_payment(pid)
    if payment["status"] != "succeeded":
        return {"ok": True}
    row = await db.fetchone("SELECT * FROM payments WHERE id=?", (pid,))
    if not row or not await db.claim_payment(pid):
        return {"ok": True}

    bot = request.app.state.bot
    if row.get("kind") == "topup":
        await db.add_balance(row["user_id"], row["amount"])
        balance = (await db.get_user(row["user_id"]))["balance"]
        try:
            await bot.send_message(
                row["user_id"],
                f"💰 Баланс пополнен на {row['amount']} ₽.\nСейчас на балансе: {balance} ₽.",
            )
        except Exception:
            pass
        return {"ok": True}
    if row.get("kind") == "devices":
        await db.execute("UPDATE users SET device_extra = device_extra + ? WHERE id=?",
                         (row["days"], row["user_id"]))
        try:
            await bot.send_message(row["user_id"], f"📱 Лимит устройств увеличен на {row['days']}.")
        except Exception:
            pass
        return {"ok": True}
    coupon = row["coupon"] if row["discount"] else None
    until = await service.on_paid(row["user_id"], row["days"], coupon, bot)
    try:
        await bot.send_message(
            row["user_id"],
            f"✅ Оплата прошла! Подписка активна до {until:%d.%m.%Y}. Откройте приложение, чтобы подключиться.",
            reply_markup=open_app_kb(),
        )
    except Exception:
        pass
    return {"ok": True}


# ---------- администратор ----------

@app.get("/api/admin/stats")
async def stats(_=Depends(admin_user)):
    now = db.iso(db.now())
    total = await db.fetchone("SELECT COUNT(*) AS c FROM users")
    active = await db.fetchone("SELECT COUNT(*) AS c FROM users WHERE sub_until > ?", (now,))
    rev = await db.fetchone(
        "SELECT COALESCE(SUM(amount),0) AS s, COUNT(*) AS c FROM payments WHERE status='succeeded' AND kind != 'topup'")
    return {"users": total["c"], "active": active["c"], "revenue": rev["s"], "payments": rev["c"]}


@app.get("/api/admin/users")
async def find_users(q: str = "", _=Depends(admin_user)):
    q = q.strip().lstrip("@")
    if q.isdigit():
        rows = await db.fetchall("SELECT * FROM users WHERE id=?", (int(q),))
    elif q:
        rows = await db.fetchall(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ? "
            "ORDER BY created_at DESC LIMIT 20", (f"%{q}%", f"%{q}%"))
    else:
        rows = await db.fetchall("SELECT * FROM users ORDER BY created_at DESC LIMIT 20")
    keys = ("id", "username", "first_name", "sub_until", "banned")
    return [{k: r[k] for k in keys} for r in rows]


class GrantIn(BaseModel):
    user_id: int
    days: int


@app.post("/api/admin/grant")
async def grant(body: GrantIn, request: Request, _=Depends(admin_user)):
    if body.days <= 0 or body.days > 3650:
        raise HTTPException(400, "Укажите от 1 до 3650 дней")
    if not await db.get_user(body.user_id):
        raise HTTPException(404, "Пользователь не найден")
    until, ok = await service.activate(body.user_id, body.days)
    try:
        await request.app.state.bot.send_message(
            body.user_id,
            f"🎁 Вам начислено {body.days} дн. Подписка активна до {until:%d.%m.%Y}.",
            reply_markup=open_app_kb(),
        )
    except Exception:
        pass
    return {
        "until": db.iso(until),
        "warning": None if ok else "Подписка выдана, но VPN-доступ не создан. Смотрите логи и настройки Marzban.",
    }


class BanIn(BaseModel):
    user_id: int
    banned: bool


@app.post("/api/admin/ban")
async def ban(body: BanIn, _=Depends(admin_user)):
    await db.execute("UPDATE users SET banned=? WHERE id=?", (int(body.banned), body.user_id))
    return {"ok": True}


class CouponNew(BaseModel):
    code: str
    percent: int
    max_uses: int = 0


@app.get("/api/admin/coupons")
async def list_coupons(_=Depends(admin_user)):
    return await db.fetchall("SELECT * FROM coupons ORDER BY created_at DESC")


@app.post("/api/admin/coupons")
async def new_coupon(body: CouponNew, _=Depends(admin_user)):
    code = body.code.strip().upper()
    if not code or not (1 <= body.percent <= 90):
        raise HTTPException(400, "Код пустой или процент вне диапазона 1–90")
    if await db.fetchone("SELECT code FROM coupons WHERE code=?", (code,)):
        raise HTTPException(400, "Такой код уже есть")
    await db.execute(
        "INSERT INTO coupons (code, percent, max_uses, used, active, created_at) VALUES (?,?,?,0,1,?)",
        (code, body.percent, max(0, body.max_uses), db.iso(db.now())),
    )
    return {"ok": True}


class CouponOff(BaseModel):
    code: str


@app.post("/api/admin/coupons/off")
async def off_coupon(body: CouponOff, _=Depends(admin_user)):
    await db.execute("UPDATE coupons SET active=0 WHERE code=?", (body.code,))
    return {"ok": True}


# ---------- устройства ----------

async def ensure_primary(uid):
    """Основное устройство — ключ, созданный при первой подписке."""
    user = await db.get_user(uid)
    if not user or not user["sub_url"]:
        return
    if not await db.device_by_name(f"tg{uid}"):
        await db.insert_device(uid, f"tg{uid}", "Основное устройство", user["sub_url"])


def _active_until(user):
    until = db.parse(user["sub_until"])
    return until if until and until > db.now() else None


@app.get("/api/devices")
async def devices(u=Depends(current_user)):
    uid = u["id"]
    user = await db.get_user(uid)
    await ensure_primary(uid)
    rows = await db.list_devices(uid)
    active = _active_until(user) is not None
    async def _online(r):
        if not r["active"]:
            return None
        try:
            return await vpn.online_at(r["name"])
        except Exception:
            return None

    onlines = await asyncio.gather(*[_online(r) for r in rows])
    out = []
    for r, online in zip(rows, onlines):
        out.append({
            "id": r["id"],
            "label": r["label"],
            "active": bool(r["active"]),
            "sub_url": r["sub_url"] if (active and r["active"]) else None,
            "online_at": online,
        })
    extra = user["device_extra"] or 0
    return {
        "devices": out,
        "limit": DEVICE_BASE + extra,
        "used": sum(1 for r in rows if r["active"]),
        "extra": extra,
        "max_extra": DEVICE_MAX_EXTRA,
        "prices": DEVICE_EXTRA_PRICES,
        "base": DEVICE_BASE,
    }


@app.post("/api/devices/add")
async def add_device(u=Depends(current_user)):
    uid = u["id"]
    user = await db.get_user(uid)
    until = _active_until(user)
    if not until:
        raise HTTPException(400, "Сначала оформите подписку")
    await ensure_primary(uid)
    rows = await db.list_devices(uid)
    used = sum(1 for r in rows if r["active"])
    limit = DEVICE_BASE + (user["device_extra"] or 0)
    if used >= limit:
        raise HTTPException(400, "Все устройства заняты. Расширьте лимит.")
    name = f"tg{uid}_{secrets.token_hex(3)}"
    try:
        sub_url = await vpn.sync_name(name, until)
    except Exception:
        log.exception("Не удалось создать устройство %s", name)
        raise HTTPException(502, "VPN-панель недоступна, попробуйте позже")
    await db.insert_device(uid, name, f"Устройство {used + 1}", sub_url)
    return {"sub_url": sub_url}


class DeviceIdIn(BaseModel):
    id: int


@app.post("/api/devices/revoke")
async def revoke_device(body: DeviceIdIn, u=Depends(current_user)):
    dev = await db.get_device(u["id"], body.id)
    if not dev or not dev["active"]:
        raise HTTPException(404, "Устройство не найдено")
    try:
        await vpn.disable_name(dev["name"])
    except Exception:
        log.exception("Не удалось отключить устройство %s", dev["name"])
        raise HTTPException(502, "VPN-панель недоступна, попробуйте позже")
    await db.revoke_device(u["id"], body.id)
    return {"ok": True}


class DevBuyIn(BaseModel):
    count: int
    method: str


@app.post("/api/devices/buy")
async def buy_devices(body: DevBuyIn, request: Request, u=Depends(current_user)):
    uid = u["id"]
    if not (1 <= body.count <= DEVICE_MAX_EXTRA):
        raise HTTPException(400, "Неверное количество устройств")
    user = await db.get_user(uid)
    extra = user["device_extra"] or 0
    if extra + body.count > DEVICE_MAX_EXTRA:
        raise HTTPException(400, "Превышен максимум дополнительных устройств")
    price = sum(DEVICE_EXTRA_PRICES[extra:extra + body.count])
    if body.method == "balance":
        async with coin_lock:
            if not await db.try_spend_balance(uid, price):
                raise HTTPException(400, "Недостаточно средств на балансе")
            await db.execute("UPDATE users SET device_extra = device_extra + ? WHERE id=?",
                             (body.count, uid))
        await db.execute(
            "INSERT INTO payments (id, user_id, amount, days, status, created_at, discount, coupon, kind) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (f"bal_{uuid.uuid4()}", uid, price, body.count, "succeeded", db.iso(db.now()), 0, None, "devices"),
        )
        return {"ok": True}
    if body.method == "card":
        pid, url = await payments.create_payment(
            uid, price, f"Дополнительные устройства: {body.count}", "devices", body.count)
        await db.execute(
            "INSERT INTO payments (id, user_id, amount, days, status, created_at, discount, coupon, kind) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, uid, price, body.count, "pending", db.iso(db.now()), 0, None, "devices"),
        )
        return {"url": url}
    raise HTTPException(400, "Неизвестный способ оплаты")


# ---------- подарки, почта, история ----------

class GiftIn(BaseModel):
    days: int


@app.post("/api/gift")
async def create_gift(body: GiftIn, u=Depends(current_user)):
    uid = u["id"]
    if body.days not in PLANS:
        raise HTTPException(400, "Неизвестный тариф")
    price = PLANS[body.days]
    async with coin_lock:
        if not await db.try_spend_balance(uid, price):
            raise HTTPException(400, "Недостаточно средств на балансе")
    code = secrets.token_hex(4).upper()
    await db.add_gift(code, uid, body.days)
    await db.execute(
        "INSERT INTO payments (id, user_id, amount, days, status, created_at, discount, coupon, kind) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (f"gift_{uuid.uuid4()}", uid, price, body.days, "succeeded", db.iso(db.now()), 0, None, "gift"),
    )
    return {"code": code, "days": body.days}


class CodeIn(BaseModel):
    code: str


@app.post("/api/gift/redeem")
async def redeem_gift(body: CodeIn, u=Depends(current_user)):
    uid = u["id"]
    code = body.code.strip().upper()
    g = await db.get_gift(code)
    if not g or g["redeemed_by"]:
        raise HTTPException(400, "Код не найден или уже использован")
    if g["from_uid"] == uid:
        raise HTTPException(400, "Нельзя активировать свой подарок")
    if not await db.redeem_gift(code, uid):
        raise HTTPException(400, "Код уже использован")
    until, ok = await service.activate(uid, g["days"])
    return {
        "until": db.iso(until),
        "warning": None if ok else "Подписка активна, но VPN-доступ не создан. Напишите в поддержку.",
    }


class EmailIn(BaseModel):
    email: str


@app.post("/api/profile/email")
async def set_email(body: EmailIn, u=Depends(current_user)):
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Некорректный email")
    await db.set_email(u["id"], email)
    return {"ok": True}


@app.get("/api/payments")
async def payments_history(u=Depends(current_user)):
    return await db.payment_history(u["id"])


# ---------- чат с поддержкой ----------

class SupportIn(BaseModel):
    text: str


@app.get("/api/support")
async def support_list(u=Depends(current_user)):
    return await db.list_support(u["id"])


@app.post("/api/support")
async def support_send(body: SupportIn, request: Request, u=Depends(current_user)):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Пустое сообщение")
    if len(text) > 1000:
        raise HTTPException(400, "Сообщение слишком длинное")
    user = await db.get_user(u["id"])
    nick = f"@{user['username']}" if user["username"] else (user["first_name"] or "пользователь")
    try:
        sent = await request.app.state.bot.send_message(
            ADMIN_ID,
            f"💬 Поддержка от {nick} (ID {u['id']}):\n\n{text}\n\n"
            "Ответьте реплаем на это сообщение, чтобы ответить пользователю.",
        )
    except Exception:
        log.exception("Не удалось переслать обращение в поддержку")
        raise HTTPException(502, "Не удалось отправить, попробуйте позже")
    await db.add_support(u["id"], "in", text, sent.message_id)
    return {"ok": True}
