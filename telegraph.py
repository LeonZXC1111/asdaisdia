import hashlib
import json
import logging

import httpx

import db
from faq import FAQ_TEXT
from terms import PRIVACY_TEXT, TERMS_TEXT

log = logging.getLogger(__name__)
API = "https://api.telegra.ph"


def _nodes(text):
    """Превращает обычный текст в узлы Telegraph: строки с CAPS — заголовки, абзацы через пустую строку."""
    nodes = []
    for block in text.strip().split("\n\n"):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].startswith("# "):
            nodes.append({"tag": "h4", "children": [lines[0][2:]]})
            lines = lines[1:]
            if not lines:
                continue
        elif len(lines) == 1 and lines[0].isupper():
            nodes.append({"tag": "h3", "children": [lines[0]]})
            continue
        children = []
        for i, line in enumerate(lines):
            if i:
                children.append({"tag": "br"})
            children.append(line)
        nodes.append({"tag": "p", "children": children})
    return nodes


async def _token():
    tok = await db.get_setting("tg_token")
    if tok:
        return tok
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/createAccount",
                         data={"short_name": "WispieVPN", "author_name": "Wispie VPN"})
        tok = r.json()["result"]["access_token"]
    await db.set_setting("tg_token", tok)
    return tok


async def _create(title, text):
    tok = await _token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/createPage", data={
            "access_token": tok,
            "title": title,
            "content": json.dumps(_nodes(text), ensure_ascii=False),
            "return_content": "false",
        })
        j = r.json()
    if not j.get("ok"):
        raise RuntimeError(j.get("error"))
    return j["result"]["url"]


async def ensure_pages():
    """Создаёт страницы соглашения и политики. Если текст изменился, создаёт новые."""
    pages = [
        ("terms", "Пользовательское соглашение", TERMS_TEXT),
        ("privacy", "Политика конфиденциальности", PRIVACY_TEXT),
        ("faq", "Часто задаваемые вопросы", FAQ_TEXT),
    ]
    for key, title, text in pages:
        digest = hashlib.sha1(text.encode()).hexdigest()
        try:
            if await db.get_setting(f"tg_{key}_hash") == digest and await db.get_setting(f"tg_{key}_url"):
                continue
            url = await _create(title, text)
            await db.set_setting(f"tg_{key}_url", url)
            await db.set_setting(f"tg_{key}_hash", digest)
            log.info("Telegraph: %s -> %s", key, url)
        except Exception:
            log.exception("Не удалось создать страницу Telegraph: %s", key)
