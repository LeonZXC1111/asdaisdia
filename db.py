from datetime import datetime, timedelta, timezone
import aiosqlite
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    referrer_id INTEGER,
    sub_until TEXT,
    sub_url TEXT,
    banned INTEGER DEFAULT 0,
    ref_rewarded INTEGER DEFAULT 0,
    notified_soon INTEGER DEFAULT 0,
    notified_expired INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    amount INTEGER,
    days INTEGER,
    status TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS coupons (
    code TEXT PRIMARY KEY,
    percent INTEGER,
    max_uses INTEGER DEFAULT 0,
    used INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS coupon_uses (
    user_id INTEGER,
    code TEXT,
    PRIMARY KEY (user_id, code)
);
CREATE TABLE IF NOT EXISTS coin_tx (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    delta INTEGER,
    reason TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Колонки, добавленные после первого запуска. Старая база получит их автоматически.
MIGRATIONS = {
    "users": {
        "coins": "INTEGER DEFAULT 0",
        "discount_pct": "INTEGER DEFAULT 0",
        "pending_coupon": "TEXT",
        "purchases": "INTEGER DEFAULT 0",
        "balance": "INTEGER DEFAULT 0",
    },
    "payments": {
        "discount": "INTEGER DEFAULT 0",
        "coupon": "TEXT",
        "kind": "TEXT DEFAULT 'plan'",
    },
}


def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def parse(s):
    return datetime.fromisoformat(s) if s else None


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            cur = await db.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in await cur.fetchall()}
            for name, ddl in cols.items():
                if name not in existing:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        await db.commit()


async def fetchone(sql, args=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, args)
        row = await cur.fetchone()
        return dict(row) if row else None


async def fetchall(sql, args=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]


async def execute(sql, args=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, args)
        await db.commit()


async def execute_rc(sql, args=()):
    """Выполняет запрос и возвращает количество изменённых строк."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, args)
        await db.commit()
        return cur.rowcount


async def get_user(uid):
    return await fetchone("SELECT * FROM users WHERE id=?", (uid,))


async def ensure_user(uid, username, first_name, referrer_id=None):
    user = await get_user(uid)
    if user:
        await execute("UPDATE users SET username=?, first_name=? WHERE id=?", (username, first_name, uid))
        return user, False
    if referrer_id == uid or (referrer_id and not await get_user(referrer_id)):
        referrer_id = None
    await execute(
        "INSERT INTO users (id, username, first_name, referrer_id, created_at) VALUES (?,?,?,?,?)",
        (uid, username, first_name, referrer_id, iso(now())),
    )
    return await get_user(uid), True


async def add_days(uid, days):
    user = await get_user(uid)
    base = parse(user["sub_until"]) if user and user["sub_until"] else None
    if not base or base < now():
        base = now()
    new_until = base + timedelta(days=days)
    await execute(
        "UPDATE users SET sub_until=?, notified_soon=0, notified_expired=0 WHERE id=?",
        (iso(new_until), uid),
    )
    return new_until


async def get_setting(key):
    row = await fetchone("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else None


async def set_setting(key, value):
    await execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


async def add_coins(uid, delta, reason):
    await execute("UPDATE users SET coins = coins + ? WHERE id=?", (delta, uid))
    await execute(
        "INSERT INTO coin_tx (user_id, delta, reason, created_at) VALUES (?,?,?,?)",
        (uid, delta, reason, iso(now())),
    )


async def try_spend(uid, amount, reason):
    """Списывает койны, только если их хватает. Возвращает True при успехе."""
    rc = await execute_rc(
        "UPDATE users SET coins = coins - ? WHERE id=? AND coins >= ?", (amount, uid, amount)
    )
    if rc != 1:
        return False
    await execute(
        "INSERT INTO coin_tx (user_id, delta, reason, created_at) VALUES (?,?,?,?)",
        (uid, -amount, reason, iso(now())),
    )
    return True


async def add_balance(uid, amount):
    await execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, uid))


async def try_spend_balance(uid, amount):
    """Списывает рубли с баланса, только если их хватает."""
    return await execute_rc(
        "UPDATE users SET balance = balance - ? WHERE id=? AND balance >= ?", (amount, uid, amount)
    ) == 1


async def claim_payment(pid):
    """Отмечает платёж успешным. Возвращает False, если он уже обработан."""
    rc = await execute_rc(
        "UPDATE payments SET status='succeeded' WHERE id=? AND status!='succeeded'", (pid,)
    )
    return rc == 1


async def valid_coupon(code, uid):
    c = await fetchone("SELECT * FROM coupons WHERE code=? AND active=1", (code,))
    if not c:
        return None
    if c["max_uses"] and c["used"] >= c["max_uses"]:
        return None
    used = await fetchone("SELECT 1 AS x FROM coupon_uses WHERE user_id=? AND code=?", (uid, code))
    return None if used else c


async def use_coupon(uid, code):
    await execute("INSERT OR IGNORE INTO coupon_uses (user_id, code) VALUES (?,?)", (uid, code))
    await execute("UPDATE coupons SET used = used + 1 WHERE code=?", (code,))
