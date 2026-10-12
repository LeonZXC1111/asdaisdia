import asyncio
import logging
import math
import random
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

import db
from bot import open_app_kb

log = logging.getLogger(__name__)

SOON = [
    "⏳ {name}, VPN заканчивается через {left}. Продлите заранее, чтобы соединение не прервалось.",
    "🔔 Осталось {left} подписки. Продлите сейчас, и всё продолжит работать без перерыва.",
    "📅 Подписка закончится {date}. Один клик в приложении, и вы в сети без пауз.",
]

EXPIRED = [
    "🔒 Подписка закончилась. Ваши данные на месте. Продлите в приложении за минуту.",
    "🌙 Срок подписки истёк. Вернуть доступ можно в один клик, выберите тариф в приложении.",
    "⚠️ VPN отключён, так как подписка закончилась. Продлите, и всё заработает снова.",
]


def _days_word(n):
    a, b = n % 100, n % 10
    if 10 < a < 20:
        return "дней"
    if b == 1:
        return "день"
    if 2 <= b <= 4:
        return "дня"
    return "дней"


async def _send(bot: Bot, uid: int, text: str):
    try:
        await bot.send_message(uid, text, reply_markup=open_app_kb())
    except TelegramAPIError as e:
        log.warning("Не удалось отправить уведомление %s: %s", uid, e)


async def check(bot: Bot):
    t = db.now()
    users = await db.fetchall(
        "SELECT id, first_name, sub_until, notified_soon, notified_expired "
        "FROM users WHERE sub_until IS NOT NULL AND banned=0"
    )
    for u in users:
        until = db.parse(u["sub_until"])
        name = u["first_name"] or "друг"

        if t < until <= t + timedelta(days=3) and not u["notified_soon"]:
            days = max(1, math.ceil((until - t).total_seconds() / 86400))
            text = random.choice(SOON).format(
                name=name, left=f"{days} {_days_word(days)}", date=until.strftime("%d.%m.%Y"))
            await _send(bot, u["id"], text)
            await db.execute("UPDATE users SET notified_soon=1 WHERE id=?", (u["id"],))

        elif until <= t and not u["notified_expired"]:
            await _send(bot, u["id"], random.choice(EXPIRED).format(name=name))
            await db.execute("UPDATE users SET notified_expired=1 WHERE id=?", (u["id"],))


async def notifier_loop(bot: Bot):
    while True:
        try:
            await check(bot)
        except Exception:
            log.exception("Ошибка в цикле уведомлений")
        await asyncio.sleep(600)
