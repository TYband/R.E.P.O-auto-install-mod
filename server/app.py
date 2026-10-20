"""
R.E.P.O. Mod 云端同步服务
=========================

给客户端提供：注册 / 登录 / 上传自己的 Mod 列表 / 搜索玩家 / 查看别人的列表。

设计取舍（都是为了让它在 1 核小机器上跑得稳）：
  * SQLite + 标准库，不引 redis / postgres
  * 令牌存库，不用 JWT —— 想踢人直接删行，不用等过期
  * 密码 PBKDF2-HMAC-SHA256 20 万轮，标准库实现，无额外依赖
  * 单文件，uvicorn 直接跑

启动：
    uvicorn app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

VERSION = "1.0.0"

BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("REPO_DB", BASE / "data" / "app.db"))
MAX_MODS = 400                 # 一个用户最多存多少条，防止被当网盘刷
TOKEN_TTL_DAYS = 180           # 令牌有效期，长期不用自动失效
PBKDF2_ROUNDS = 200_000

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-]{3,20}$")


# ============================================================
# 数据库
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    pass_hash   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    last_login  TEXT,
    is_public   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tokens (
    token       TEXT    PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    last_used   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS modlists (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    payload     TEXT    NOT NULL,
    mod_count   INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_users_name  ON users(username);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.executescript(SCHEMA)


# ============================================================
# 密码 / 令牌
# ============================================================

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(rounds))
        return secrets.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


# ============================================================
# 简易限流（单进程内存，够用；重启即清零）
# ============================================================

_hits: dict[str, list[float]] = {}


def rate_limit(key: str, limit: int, window: int) -> None:
    t = time.time()
    bucket = [x for x in _hits.get(key, []) if t - x < window]
    if len(bucket) >= limit:
        raise HTTPException(429, "操作太频繁，请稍后再试")
    bucket.append(t)
    _hits[key] = bucket
    if len(_hits) > 5000:                 # 防止字典无限增长
        for k in [k for k, v in _hits.items() if not v or t - v[-1] > 3600]:
            _hits.pop(k, None)


# ============================================================
# 请求模型
# ============================================================

class AuthIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6, max_length=64)


class ModIn(BaseModel):
    full_name: str = Field(..., max_length=120)
    version: str = Field("", max_length=40)
    name: str = Field("", max_length=120)


class PushIn(BaseModel):
    mods: list[ModIn]
    note: str = Field("", max_length=200)


class PrivacyIn(BaseModel):
    public: bool


# ============================================================
# 应用
# ============================================================

app = FastAPI(title="R.E.P.O. Mod 云端同步", version=VERSION,
              docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()


_FIELD_CN = {"username": "用户名", "password": "密码", "mods": "Mod 列表"}


@app.exception_handler(RequestValidationError)
def _valid_err(request: Request, exc: RequestValidationError):
    errs = exc.errors()
    if errs:
        loc = [x for x in errs[0].get("loc", []) if x not in ("body", "query", "path")]
        field = _FIELD_CN.get(loc[0] if loc else "", loc[0] if loc else "参数")
        msg = errs[0].get("msg", "")
        if "at least" in msg and "3" in msg:
            msg = "至少 3 个字符"
        elif "at least" in msg and "6" in msg:
            msg = "至少 6 个字符"
        elif "at most" in msg:
            msg = "太长了"
        return JSONResponse({"ok": False, "error": f"{field}：{msg}"},
                            status_code=422)
    return JSONResponse({"ok": False, "error": "参数不正确"}, status_code=422)


@app.exception_handler(HTTPException)
def _http_err(request: Request, exc: HTTPException):
    # 统一成 {ok:false, error:"..."}，客户端只解析一种形状
    return JSONResponse({"ok": False, "error": exc.detail},
                        status_code=exc.status_code)


def current_user(authorization: Optional[str] = Header(None)) -> sqlite3.Row:
    """从 Authorization: Bearer <token> 解析出用户"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "未登录")
    token = authorization.split(" ", 1)[1].strip()
    with db() as c:
        row = c.execute(
            "SELECT u.*, t.token AS token, t.created_at AS token_at "
            "FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token = ?",
            (token,)).fetchone()
        if not row:
            raise HTTPException(401, "登录已失效，请重新登录")
        # 过期检查
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.strptime(row["token_at"], "%Y-%m-%d %H:%M:%S")
                   .replace(tzinfo=timezone.utc)).days
        except Exception:
            age = 0
        if age > TOKEN_TTL_DAYS:
            c.execute("DELETE FROM tokens WHERE token = ?", (token,))
            raise HTTPException(401, "登录已过期，请重新登录")
        c.execute("UPDATE tokens SET last_used = ? WHERE token = ?",
                  (now_iso(), token))
        return row


def user_public(row, mod_count: int = 0, updated_at: str = "") -> dict:
    return {
        "username": row["username"],
        "created_at": row["created_at"],
        "is_public": bool(row["is_public"]),
        "mod_count": mod_count,
        "updated_at": updated_at,
    }


# ------------------------------------------------------------
# 健康检查
# ------------------------------------------------------------

@app.get("/api/health")
def health():
    with db() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        lists = c.execute("SELECT COUNT(*) FROM modlists").fetchone()[0]
    return {"ok": True, "version": VERSION, "users": users, "lists": lists,
            "time": now_iso()}


# ------------------------------------------------------------
# 注册 / 登录 / 登出
# ------------------------------------------------------------

@app.post("/api/register")
def register(body: AuthIn, request: Request):
    rate_limit(f"reg:{request.client.host}", 8, 3600)

    name = body.username.strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(400, "用户名只能用 3-20 位字母、数字、下划线或短横线")

    with db() as c:
        exists = c.execute("SELECT 1 FROM users WHERE username = ?",
                           (name,)).fetchone()
        if exists:
            raise HTTPException(409, "这个用户名已经被注册了")

        cur = c.execute(
            "INSERT INTO users (username, pass_hash, created_at) VALUES (?,?,?)",
            (name, hash_password(body.password), now_iso()))
        uid = cur.lastrowid
        token = new_token()
        c.execute("INSERT INTO tokens (token, user_id, created_at, last_used) "
                  "VALUES (?,?,?,?)", (token, uid, now_iso(), now_iso()))
        row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    return {"ok": True, "token": token, "user": user_public(row)}


@app.post("/api/login")
def login(body: AuthIn, request: Request):
    rate_limit(f"login:{request.client.host}", 20, 3600)

    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?",
                        (body.username.strip(),)).fetchone()
        # 用户不存在也走一遍哈希，避免用响应时间探测用户名
        stored = row["pass_hash"] if row else (
            "pbkdf2_sha256$200000$" + "00" * 16 + "$" + "00" * 32)
        if not verify_password(body.password, stored) or not row:
            raise HTTPException(401, "用户名或密码不对")

        token = new_token()
        c.execute("INSERT INTO tokens (token, user_id, created_at, last_used) "
                  "VALUES (?,?,?,?)", (token, row["id"], now_iso(), now_iso()))
        c.execute("UPDATE users SET last_login = ? WHERE id = ?",
                  (now_iso(), row["id"]))
        ml = c.execute("SELECT mod_count, updated_at FROM modlists "
                       "WHERE user_id = ?", (row["id"],)).fetchone()

    return {"ok": True, "token": token,
            "user": user_public(row,
                                ml["mod_count"] if ml else 0,
                                ml["updated_at"] if ml else "")}


@app.post("/api/logout")
def logout(user=Depends(current_user)):
    with db() as c:
        c.execute("DELETE FROM tokens WHERE token = ?", (user["token"],))
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    with db() as c:
        ml = c.execute("SELECT mod_count, updated_at FROM modlists "
                       "WHERE user_id = ?", (user["id"],)).fetchone()
    return {"ok": True,
            "user": user_public(user,
                                ml["mod_count"] if ml else 0,
                                ml["updated_at"] if ml else "")}


@app.post("/api/privacy")
def set_privacy(body: PrivacyIn, user=Depends(current_user)):
    with db() as c:
        c.execute("UPDATE users SET is_public = ? WHERE id = ?",
                  (1 if body.public else 0, user["id"]))
    return {"ok": True, "is_public": body.public}


# ------------------------------------------------------------
# 我的 Mod 列表
# ------------------------------------------------------------

@app.put("/api/mods")
def push_mods(body: PushIn, user=Depends(current_user)):
    if len(body.mods) > MAX_MODS:
        raise HTTPException(400, f"一次最多 {MAX_MODS} 个 Mod")

    # 去重 + 去空，服务端不信任客户端
    seen, clean = set(), []
    for m in body.mods:
        key = m.full_name.strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        clean.append({"full_name": key,
                      "version": m.version.strip(),
                      "name": m.name.strip() or key.split("-", 1)[-1]})
    clean.sort(key=lambda x: x["full_name"].lower())

    payload = {"mods": clean, "note": body.note.strip(),
               "updated_at": now_iso(), "client": "REPO-Mod-Installer"}
    ts = now_iso()

    with db() as c:
        c.execute(
            "INSERT INTO modlists (user_id, payload, mod_count, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "payload=excluded.payload, mod_count=excluded.mod_count, "
            "updated_at=excluded.updated_at",
            (user["id"], json.dumps(payload, ensure_ascii=False),
             len(clean), ts))

    return {"ok": True, "count": len(clean), "updated_at": ts}


@app.get("/api/mods")
def my_mods(user=Depends(current_user)):
    with db() as c:
        row = c.execute("SELECT payload, updated_at FROM modlists "
                        "WHERE user_id = ?", (user["id"],)).fetchone()
    if not row:
        return {"ok": True, "mods": [], "updated_at": ""}
    data = json.loads(row["payload"])
    return {"ok": True, "mods": data.get("mods", []),
            "note": data.get("note", ""), "updated_at": row["updated_at"]}


@app.delete("/api/mods")
def clear_mods(user=Depends(current_user)):
    with db() as c:
        c.execute("DELETE FROM modlists WHERE user_id = ?", (user["id"],))
    return {"ok": True}


# ------------------------------------------------------------
# 搜索玩家 / 看别人的列表
# ------------------------------------------------------------

@app.get("/api/users/search")
def search_users(q: str = "", limit: int = 20, user=Depends(current_user)):
    limit = max(1, min(limit, 50))
    like = f"%{q.strip()}%"

    with db() as c:
        rows = c.execute(
            "SELECT u.*, COALESCE(m.mod_count,0) AS mod_count, "
            "       COALESCE(m.updated_at,'') AS updated_at "
            "FROM users u LEFT JOIN modlists m ON m.user_id = u.id "
            "WHERE u.id != ? AND u.username LIKE ? "
            "ORDER BY mod_count DESC, u.username ASC LIMIT ?",
            (user["id"], like, limit)).fetchall()

    return {"ok": True, "users": [user_public(r, r["mod_count"], r["updated_at"])
                                  for r in rows]}


@app.get("/api/users/{username}")
def view_user(username: str, user=Depends(current_user)):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?",
                        (username,)).fetchone()
        if not row:
            raise HTTPException(404, "没有这个用户")
        if not row["is_public"] and row["id"] != user["id"]:
            raise HTTPException(403, "对方把 Mod 列表设成私密了")
        ml = c.execute("SELECT payload, updated_at FROM modlists "
                       "WHERE user_id = ?", (row["id"],)).fetchone()

    data = json.loads(ml["payload"]) if ml else {}
    return {"ok": True,
            "user": user_public(row, len(data.get("mods", [])),
                                ml["updated_at"] if ml else ""),
            "mods": data.get("mods", []),
            "note": data.get("note", "")}
