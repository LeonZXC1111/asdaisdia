import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher

import db
import telegraph
from bot import router, set_profile
from config import BOT_TOKEN, WEB_PORT
from notifier import notifier_loop
from web import app


async def main():
    logging.basicConfig(level=logging.INFO)
    await db.init_db()
    await telegraph.ensure_pages()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    app.state.bot = bot
    await set_profile(bot)

    # 0.0.0.0 нужен хостингу, чтобы снаружи был доступ к веб-части
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=WEB_PORT, log_level="info"))

    await asyncio.gather(
        dp.start_polling(bot),
        server.serve(),
        notifier_loop(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
