import logging

import httpx

from config import MARZBAN_URL, MARZBAN_USER, MARZBAN_PASS, VPN_INBOUNDS

log = logging.getLogger(__name__)


class Marzban:
    """Адаптер для панели Marzban. Каждое устройство — отдельный пользователь панели."""

    def __init__(self):
        self.http = httpx.AsyncClient(base_url=MARZBAN_URL, timeout=20)
        self.token = None

    async def _login(self):
        r = await self.http.post("/api/admin/token",
                                 data={"username": MARZBAN_USER, "password": MARZBAN_PASS})
        r.raise_for_status()
        self.token = r.json()["access_token"]

    async def _request(self, method, path, **kw):
        if not self.token:
            await self._login()
        for attempt in range(2):
            r = await self.http.request(method, path,
                                        headers={"Authorization": f"Bearer {self.token}"}, **kw)
            if r.status_code == 401 and attempt == 0:
                await self._login()
                continue
            return r
        return r

    async def sync_name(self, name, until):
        """Создаёт или продлевает пользователя панели, возвращает ссылку подписки."""
        expire = int(until.timestamp())
        r = await self._request("GET", f"/api/user/{name}")
        if r.status_code == 404:
            body = {
                "username": name,
                "proxies": {"vless": {}},
                "inbounds": {"vless": VPN_INBOUNDS},
                "expire": expire,
                "data_limit": 0,
                "status": "active",
            }
            r = await self._request("POST", "/api/user", json=body)
        else:
            r = await self._request("PUT", f"/api/user/{name}",
                                    json={"expire": expire, "status": "active"})
        r.raise_for_status()
        sub = r.json().get("subscription_url", "")
        if sub.startswith("/"):
            sub = MARZBAN_URL.rstrip("/") + sub
        return sub

    async def sync(self, uid, until):
        return await self.sync_name(f"tg{uid}", until)

    async def disable_name(self, name):
        r = await self._request("PUT", f"/api/user/{name}", json={"status": "disabled"})
        r.raise_for_status()

    async def online_at(self, name):
        r = await self._request("GET", f"/api/user/{name}")
        if r.status_code != 200:
            return None
        return r.json().get("online_at")


vpn = Marzban()
