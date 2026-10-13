import logging

import db
from config import COINS_PER_PURCHASE, REFERRAL_BONUS_DAYS
from vpn import vpn

log = logging.getLogger(__name__)


async def activate(uid, days):
    """Продлевает подписку и все активные устройства. Возвращает (дата окончания, создан ли доступ)."""
    until = await db.add_days(uid, days)
    ok = True
    try:
        primary = await db.device_by_name(f"tg{uid}")
        if primary is None or primary["active"]:
            sub_url = await vpn.sync(uid, until)
            await db.execute("UPDATE users SET sub_url=? WHERE id=?", (sub_url, uid))
            if primary is not None:
                await db.execute("UPDATE devices SET sub_url=? WHERE name=?", (sub_url, f"tg{uid}"))
        for d in await db.list_devices(uid):
            if d["active"] and d["name"] != f"tg{uid}":
                await vpn.sync_name(d["name"], until)
    except Exception:
        log.exception("VPN-доступ не создан для пользователя %s. Проверьте Marzban.", uid)
        ok = False
    return until, ok


async def on_paid(uid, days, coupon, bot):
    """Обработка оплаты подписки: доступ, койны, реферал, промокод."""
    until, _ = await activate(uid, days)
    await db.execute(
        "UPDATE users SET discount_pct=0, pending_coupon=NULL, purchases=purchases+1 WHERE id=?",
        (uid,),
    )
    if coupon:
        await db.use_coupon(uid, coupon)
    await db.add_coins(uid, COINS_PER_PURCHASE, "purchase")
    await reward_referrer(uid, bot)
    return until


async def reward_referrer(uid, bot):
    user = await db.get_user(uid)
    if not user or not user["referrer_id"] or user["ref_rewarded"]:
        return
    await db.execute("UPDATE users SET ref_rewarded=1 WHERE id=?", (uid,))
    ref_id = user["referrer_id"]
    await activate(ref_id, REFERRAL_BONUS_DAYS)
    try:
        await bot.send_message(
            ref_id,
            f"🎁 Ваш друг оформил подписку! Вам начислено +{REFERRAL_BONUS_DAYS} дн.",
        )
    except Exception:
        log.exception("Не удалось уведомить реферера %s", ref_id)
