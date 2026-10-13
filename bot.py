import logging
import math
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton as Btn,
    BotCommand,
    InlineKeyboardMarkup as Kb,
    Message,
    WebAppInfo,
)

import db
from config import ADMIN_ID, BOT_USERNAME, REFERRAL_BONUS_DAYS, SUPPORT_URL, SUPPORT_USERNAME, WEBAPP_URL

router = Router()
log = logging.getLogger(__name__)
WELCOME_COOLDOWN = 30 * 60
_last_menu: dict[int, float] = {}
_last_unknown: dict[int, float] = {}

BOT_SHORT = "Быстрый VPN: доступ сразу после оплаты, оплата картой и СБП. Жмите /start"
BOT_DESCRIPTION = """🧛🏼 Wispie VPN

📱 Платформы:
└ Android · iOS · Windows · macOS · Linux · TV

⚡ Что внутри:
├ Доступ сразу после оплаты
├ Оплата картой и СБП
├ Баланс: пополняйте заранее
└ Бонусы: +7 дней за каждого друга

🚀 Подключение за пару минут.
Нажмите /start"""


async def set_profile(bot):
    """Описание бота, которое видно в профиле и на пустом экране чата."""
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT)
        await bot.set_my_description(description=BOT_DESCRIPTION)
        await bot.set_my_commands([
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="help", description="Что умеет бот"),
            BotCommand(command="support", description="Связаться с поддержкой"),
        ])
    except Exception:
        log.exception("Не удалось обновить описание бота")


def _app(path=""):
    return WebAppInfo(url=f"{WEBAPP_URL}{path}")


def open_app_kb():
    return Kb(inline_keyboard=[[Btn(text="🚀 Открыть приложение", web_app=_app())]])


def _money(n):
    return f"{n:,}".replace(",", " ")


def _days_word(n):
    a, b = n % 100, n % 10
    if 10 < a < 20:
        return "дней"
    if b == 1:
        return "день"
    if 2 <= b <= 4:
        return "дня"
    return "дней"


def _nick(user):
    return f"@{user['username']}" if user.get("username") else (user.get("first_name") or "друг")


def _sub_line(user):
    until = db.parse(user["sub_until"])
    if not until or until <= db.now():
        return "не активна"
    days = math.ceil((until - db.now()).total_seconds() / 86400)
    return f"{days} {_days_word(days)}, до {until:%d.%m.%Y}"


async def menu_text(uid):
    user = await db.get_user(uid)
    return (
        f"Добро пожаловать, {_nick(user)} в Wispie VPN! 🧛🏼 ⚡\n\n"
        f"💰 Баланс: {_money(user['balance'] or 0)} ₽\n\n"
        f"📅 Подписка: {_sub_line(user)}\n\n"
        "Выберите раздел ниже 👇"
    )


def menu_kb():
    return Kb(inline_keyboard=[
        [Btn(text="💳 Купить подписку", web_app=_app("?tab=plans"))],
        [Btn(text="🔐 Моя подписка", web_app=_app("?tab=sub"))],
        [Btn(text="💰 Баланс", callback_data="bal"),
         Btn(text="🛍 Магазин", web_app=_app("?tab=more&s=shop"))],
        [Btn(text="👥 Пригласить", callback_data="inv"),
         Btn(text="✨ Открыть профиль", web_app=_app())],
        [Btn(text="ℹ️ О сервисе", callback_data="about")],
        [Btn(text="📄 Документы и поддержка", callback_data="docs")],
    ])


def _back():
    return [Btn(text="‹ Назад", callback_data="home")]


async def docs_kb():
    rows = []
    terms = await db.get_setting("tg_terms_url")
    privacy = await db.get_setting("tg_privacy_url")
    faq = await db.get_setting("tg_faq_url")
    if terms:
        rows.append([Btn(text="📜 Пользовательское соглашение", url=terms)])
    if privacy:
        rows.append([Btn(text="🔒 Политика конфиденциальности", url=privacy)])
    if faq:
        rows.append([Btn(text="❓ Часто задаваемые вопросы", url=faq)])
    rows.append([Btn(text="💬 Написать в поддержку", url=SUPPORT_URL)])
    rows.append(_back())
    return Kb(inline_keyboard=rows)


async def send_menu(m: Message, force=False):
    uid = m.from_user.id
    if not force and time.time() - _last_menu.get(uid, 0) < WELCOME_COOLDOWN:
        return
    _last_menu[uid] = time.time()
    await m.answer(await menu_text(uid), reply_markup=menu_kb())


@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject):
    referrer = None
    if command.args and command.args.startswith("ref"):
        try:
            referrer = int(command.args[3:])
        except ValueError:
            pass
    user, _ = await db.ensure_user(m.from_user.id, m.from_user.username,
                                   m.from_user.first_name, referrer)
    if user["banned"]:
        return
    await send_menu(m, force=True)


@router.message(Command("help"))
async def cmd_help(m: Message):
    await db.ensure_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    text = (
        "🧛🏼 Wispie VPN: команды бота\n\n"
        "/start — главное меню: баланс, подписка и ваш ID\n"
        "/help — эта справка\n"
        f"/support — связь с поддержкой ({SUPPORT_USERNAME})\n\n"
        "Подписку и подключение удобнее оформить в мини-приложении: кнопка «Купить подписку» в меню."
    )
    faq = await db.get_setting("tg_faq_url")
    rows = []
    if faq:
        rows.append([Btn(text="❓ Часто задаваемые вопросы", url=faq)])
    rows.append([Btn(text="💬 Написать в поддержку", url=SUPPORT_URL)])
    await m.answer(text, reply_markup=Kb(inline_keyboard=rows))


@router.message(Command("support"))
async def cmd_support(m: Message):
    await db.ensure_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    text = (
        f"💬 Поддержка Wispie VPN: {SUPPORT_USERNAME}\n\n"
        "Опишите проблему, и мы поможем лично."
    )
    await m.answer(text, reply_markup=Kb(inline_keyboard=[
        [Btn(text="💬 Написать в поддержку", url=SUPPORT_URL)],
    ]))


@router.message(F.reply_to_message, F.from_user.id == ADMIN_ID)
async def admin_reply(m: Message):
    """Ответ администратора реплаем на обращение из мини-приложения."""
    user_id = await db.support_user_by_admin_msg(m.reply_to_message.message_id)
    text = (m.text or m.caption or "").strip()
    if not user_id or not text:
        return
    await db.add_support(user_id, "out", text)
    try:
        await m.bot.send_message(user_id, f"💬 Поддержка:\n\n{text}")
        await m.answer("✅ Отправлено пользователю")
    except Exception:
        await m.answer("Не удалось доставить сообщение пользователю")


@router.message()
async def any_message(m: Message):
    """Всё, что не является командой бота."""
    user, _ = await db.ensure_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    if user["banned"]:
        return
    uid = m.from_user.id
    if time.time() - _last_unknown.get(uid, 0) < 20:
        return
    _last_unknown[uid] = time.time()
    text = (
        "🤔 Извините, я не понимаю эту команду.\n\n"
        f"Если у вас есть вопросы, посмотрите FAQ или напишите в поддержку: {SUPPORT_USERNAME}"
    )
    faq = await db.get_setting("tg_faq_url")
    rows = []
    if faq:
        rows.append([Btn(text="❓ Часто задаваемые вопросы", url=faq)])
    rows.append([Btn(text="💬 Написать в поддержку", url=SUPPORT_URL)])
    await m.answer(text, reply_markup=Kb(inline_keyboard=rows))


async def _show(cb: CallbackQuery, text, kb):
    """Меняет текст текущего сообщения, а не отправляет новое."""
    await cb.answer()
    try:
        await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery):
    await _show(cb, await menu_text(cb.from_user.id), menu_kb())


@router.callback_query(F.data == "bal")
async def cb_bal(cb: CallbackQuery):
    user = await db.get_user(cb.from_user.id)
    text = (f"💰 Баланс: {_money(user['balance'] or 0)} ₽\n\n"
            "Пополняйте баланс заранее: оплата картой и СБП.\n"
            "Подписку можно оплатить с баланса в один клик.")
    kb = Kb(inline_keyboard=[
        [Btn(text="➕ Пополнить", web_app=_app("?tab=plans"))],
        _back(),
    ])
    await _show(cb, text, kb)


@router.callback_query(F.data == "inv")
async def cb_inv(cb: CallbackQuery):
    text = (f"👥 Приглашайте друзей и получайте +{REFERRAL_BONUS_DAYS} дн. за каждого, "
            "кто впервые оплатит подписку.\n\n"
            f"Ваша ссылка:\nhttps://t.me/{BOT_USERNAME}?start=ref{cb.from_user.id}")
    await _show(cb, text, Kb(inline_keyboard=[_back()]))


@router.callback_query(F.data == "about")
async def cb_about(cb: CallbackQuery):
    text = ("🧛🏼 О сервисе Wispie VPN\n\n"
            "Простой VPN, который не мешает жить: оплатили — подключились.\n\n"
            "Что внутри:\n"
            "— доступ выдаётся сразу после оплаты;\n"
            "— ссылка и QR-код для любого устройства;\n"
            "— оплата картой или СБП;\n"
            "— койны за каждую покупку и магазин;\n"
            "— бонусы за друзей: +7 дней за каждого.\n\n"
            "Не работает что-то? Напишите в поддержку, ответим лично и быстро.")
    kb = Kb(inline_keyboard=[[Btn(text="💬 Написать в поддержку", url=SUPPORT_URL)], _back()])
    await _show(cb, text, kb)


@router.callback_query(F.data == "docs")
async def cb_docs(cb: CallbackQuery):
    text = "📄 Документы и поддержка\n\nПравовые документы сервиса и способы связи с поддержкой."
    await _show(cb, text, await docs_kb())
