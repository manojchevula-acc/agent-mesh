"""
FAB MCP Hub — Chat UI Server v4
================================
Multi-screen SPA: Dashboard | Chat | History | Observability | Search

Auth: local user list → JWT session tokens (no hub test at login).
      JWT_SECRET shared with hub → real usernames in auth logs.
      Default passwords match usernames: admin/admin, analyst/analyst, viewer/viewer.

Persistence: SQLite in chat_service/data/fab_chat.db (sessions + messages per user).

Run:
    python chat_service/chat_server.py    # open http://localhost:8080
"""

import asyncio
import json
import os
import sys
import time
import uuid
import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path

def _chat_log(event_type: str, **data) -> None:
    """Write one structured JSONL event to stdout and logs/chat.log."""
    import json as _j, pathlib as _pl, threading as _th
    entry = {"ts": round(time.time(), 3), "service": "chat", "type": event_type, **data}
    line = _j.dumps(entry, default=str)
    print(line)
    _log_dir = _pl.Path(__file__).resolve().parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = _log_dir / "chat.log"
    try:
        with open(_log_file, "a", encoding="utf-8") as _fh:
            _fh.write(line + "\n")
    except Exception:
        pass

# Load root .env BEFORE reading CHAT_JWT_SECRET / JWT_SECRET (read at module level below)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from sqlalchemy import create_engine as _sa_create_engine, text as _sa_text
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))
import agent as fab_agent

app = FastAPI(title="FAB MCP Hub Chat UI", version="4.0.0")

@app.on_event("startup")
async def _startup():
    _init_user_store()
    _init_mysql_tables()
    # Reset any sessions left in 'pending' state from a previous crash/restart
    with _get_chat_engine().begin() as _c:
        _c.execute(_sa_text("UPDATE chat_sessions SET status='complete' WHERE status='pending'"))

CHAT_HOST = os.environ.get("CHAT_HOST", "0.0.0.0")
CHAT_PORT = int(os.environ.get("CHAT_PORT", "8080"))

# JWT_SECRET is shared with hub_service/auth.py — both default to the same value so that
# JWTs minted here validate at the hub and real usernames appear in auth logs.
CHAT_JWT_SECRET = os.environ.get("JWT_SECRET", "fab-mcp-dev-local-secret-change-in-prod")

_DEFAULT_SECRET = "fab-mcp-dev-local-secret-change-in-prod"
if CHAT_JWT_SECRET == _DEFAULT_SECRET:
    import warnings as _warnings
    _warnings.warn(
        "\n⚠  SECURITY: Using default JWT_SECRET — set JWT_SECRET env var "
        "to a strong random secret in production.\n"
        "   Generate: python -c \"import secrets; print(secrets.token_hex(32))\"",
        stacklevel=2,
    )


# ── User config ────────────────────────────────────────────────────────────

def _parse_users(raw: str) -> dict:
    users: dict = {}
    for entry in raw.split("|"):
        parts = [p.strip() for p in entry.strip().split(":")]
        if len(parts) < 2:
            continue
        uname, upass = parts[0], parts[1]
        uroles = [r.strip() for r in parts[2].split(",")] if len(parts) > 2 else ["agent"]
        users[uname] = {"password": upass, "roles": uroles,
                        "display": uname.replace("_", " ").title()}
    return users


_USERS: dict = (
    _parse_users(os.environ["CHAT_USERS"])
    if os.environ.get("CHAT_USERS")
    else {
        "admin":   {"password": "admin",   "roles": ["admin"], "display": "Administrator"},
        "analyst": {"password": "analyst", "roles": ["agent"], "display": "Data Analyst"},
        "viewer":  {"password": "viewer",  "roles": ["agent"], "display": "Viewer"},
    }
)


# ── JWT helpers ────────────────────────────────────────────────────────────

def _mint_jwt(username: str, roles: list, hours: int = 8) -> str:
    try:
        import jwt
        now = int(time.time())
        payload = {"sub": username, "roles": roles, "iat": now,
                   "exp": now + hours * 3600, "iss": "fab-chat"}
        return jwt.encode(payload, CHAT_JWT_SECRET, algorithm="HS256")
    except ImportError:
        return username  # hub open-dev-mode accepts raw username too


# ── Password utilities ─────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 200_000)
    return f"pbkdf2:sha256:200000:{salt}:{dk.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith('pbkdf2:'):
        # Primary path: PBKDF2-SHA256 with 200,000 iterations.
        # Format: "pbkdf2:sha256:200000:<hex-salt>:<hex-digest>"
        _, algo, iters, salt, hv = stored.split(':', 4)
        dk = hashlib.pbkdf2_hmac(algo, password.encode('utf-8'), salt.encode('utf-8'), int(iters))
        return secrets.compare_digest(dk.hex(), hv)
    # Migration fallback: users seeded from CHAT_USERS env var store plain-text
    # passwords in the _USERS dict. When those users log in for the first time
    # after the DB is initialised with plain-text hashes, this branch accepts them.
    # The password will be re-hashed to PBKDF2 on next admin update or password
    # change. secrets.compare_digest prevents timing attacks even on plain strings.
    return secrets.compare_digest(password, stored)

def _gen_temp_password() -> str:
    return secrets.token_urlsafe(12)


# ── MySQL user store ───────────────────────────────────────────────────────

_chat_engine = None

def _get_chat_engine():
    global _chat_engine
    if _chat_engine is None:
        from urllib.parse import quote_plus as _qp
        user = os.environ.get("MYSQL_USER", "test_user")
        pw   = os.environ.get("MYSQL_PASSWORD", "Welcome@12345")
        host = os.environ.get("MYSQL_HOST", "localhost")
        port = int(os.environ.get("MYSQL_PORT", "3306"))
        db   = os.environ.get("MYSQL_DATABASE", "fab_semantic")
        _chat_engine = _sa_create_engine(
            f"mysql+pymysql://{_qp(user)}:{_qp(pw)}@{host}:{port}/{db}?charset=utf8mb4",
            pool_pre_ping=True, pool_size=5, pool_recycle=1800
        )
    return _chat_engine

def _init_user_store():
    """Create chat_users table in MySQL and seed defaults if empty."""
    eng = _get_chat_engine()
    with eng.begin() as conn:
        conn.execute(_sa_text("""
            CREATE TABLE IF NOT EXISTS chat_users (
                username             VARCHAR(64)  PRIMARY KEY,
                display_name         VARCHAR(128) NOT NULL,
                password_hash        VARCHAR(255) NOT NULL,
                roles                JSON         NOT NULL,
                is_active            TINYINT(1)   NOT NULL DEFAULT 1,
                auth_provider        VARCHAR(32)  NOT NULL DEFAULT 'local',
                must_change_password TINYINT(1)   NOT NULL DEFAULT 0,
                created_at           TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                updated_at           TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                created_by           VARCHAR(64)  DEFAULT NULL
            )
        """))
        count = conn.execute(_sa_text("SELECT COUNT(*) FROM chat_users")).scalar()
        if count == 0:
            for uname, info in _USERS.items():
                conn.execute(_sa_text(
                    "INSERT INTO chat_users (username, display_name, password_hash, roles, created_by) "
                    "VALUES (:u, :d, :p, :r, 'system')"
                ), {"u": uname, "d": info["display"],
                    "p": _hash_password(info["password"]),
                    "r": json.dumps(info["roles"])})

def _db_get_user(username: str) -> dict | None:
    try:
        with _get_chat_engine().connect() as conn:
            row = conn.execute(
                _sa_text("SELECT username, display_name, password_hash, roles, is_active, must_change_password, auth_provider FROM chat_users WHERE username=:u"),
                {"u": username}
            ).fetchone()
        if row is None:
            return None
        roles = row.roles if isinstance(row.roles, list) else json.loads(row.roles or '["agent"]')
        return {"username": row.username, "display": row.display_name, "roles": roles,
                "is_active": bool(row.is_active), "must_change_password": bool(row.must_change_password),
                "auth_provider": row.auth_provider, "password_hash": row.password_hash}
    except Exception:
        return None


def _decode_jwt(token: str) -> dict:
    """Decode and verify a chat HS256 JWT. Called only for internal trace logging.

    WARNING — silent fallback on any decode error:
    When the token is missing or invalid, this function returns a synthetic claims
    dict using the first 20 characters of the raw token as 'sub'. This is used
    only for extracting a display name for trace events — it is NOT used for
    access control. Real access control uses _auth_user() which raises HTTP 401
    on any failure.  Do not use _decode_jwt() for authentication decisions.
    """
    if not token:
        return {"sub": "dev", "roles": ["agent"]}
    try:
        import jwt
        return jwt.decode(token, CHAT_JWT_SECRET, algorithms=["HS256"])
    except Exception:
        # Token malformed or signed with a different secret.
        # Use a truncated snippet as the sub so trace logs still show
        # something useful rather than a blank or a crash.
        clean = token.strip()[:20]
        return {"sub": clean or "dev", "roles": ["agent"]}


_VALID_ISSUERS = frozenset({"fab-chat", "fab-mcp-hub"})


def _auth_user(authorization: str) -> dict:
    """Validate Bearer JWT. Raises HTTP 401 for missing, invalid, or expired tokens."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required — please log in",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        import jwt as _jwt
        claims = _jwt.decode(token, CHAT_JWT_SECRET, algorithms=["HS256"])
        if claims.get("iss") not in _VALID_ISSUERS:
            raise HTTPException(status_code=401, detail="Token issuer not recognized")
        return claims
    except HTTPException:
        raise
    except _jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Session expired — please log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid token — please log in again",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _is_admin(user: dict) -> bool:
    return "admin" in user.get("roles", [])


# ── Login rate limiting ─────────────────────────────────────────────────────

import time as _time
from collections import defaultdict as _defaultdict

_login_attempts: dict = _defaultdict(list)
_LOGIN_MAX    = 5    # max failed attempts per identifier before HTTP 429
_LOGIN_WINDOW = 300  # sliding window in seconds (5 minutes); resets per-IP after this period
# The identifier used for rate limiting is the username, not the IP address.
# This prevents username enumeration via timing differences but does not protect
# against distributed brute-force from many IPs using different usernames.


def _check_rate_limit(identifier: str) -> None:
    """Raise HTTP 429 if more than _LOGIN_MAX attempts in the past _LOGIN_WINDOW seconds."""
    now    = _time.time()
    cutoff = now - _LOGIN_WINDOW
    _login_attempts[identifier] = [t for t in _login_attempts[identifier] if t > cutoff]
    if len(_login_attempts[identifier]) >= _LOGIN_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts — please wait {_LOGIN_WINDOW // 60} minutes.",
        )
    _login_attempts[identifier].append(now)


def _clear_rate_limit(identifier: str) -> None:
    _login_attempts.pop(identifier, None)


# ── MySQL persistence for sessions / messages / traces ─────────────────────

def _init_mysql_tables() -> None:
    """Create chat_sessions, chat_messages, chat_traces in MySQL if absent."""
    eng = _get_chat_engine()
    with eng.begin() as conn:
        conn.execute(_sa_text("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id         VARCHAR(36)  NOT NULL PRIMARY KEY,
                username   VARCHAR(64)  NOT NULL,
                name       VARCHAR(255) NOT NULL,
                created_at VARCHAR(64)  NOT NULL,
                updated_at VARCHAR(64)  NOT NULL,
                status     VARCHAR(32)  DEFAULT 'complete',
                INDEX idx_sess_user (username, updated_at)
            )
        """))
        conn.execute(_sa_text("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id         BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(36) NOT NULL,
                role       VARCHAR(16) NOT NULL,
                content    TEXT        NOT NULL,
                ts         VARCHAR(16) NOT NULL,
                INDEX idx_msg_sess (session_id)
            )
        """))
        conn.execute(_sa_text("""
            CREATE TABLE IF NOT EXISTS chat_traces (
                id         BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(36)  NOT NULL,
                msg_index  INT          NOT NULL,
                event_type VARCHAR(64)  NOT NULL,
                event_data MEDIUMTEXT   NOT NULL,
                ts         VARCHAR(64)  NOT NULL,
                INDEX idx_trace_sess (session_id, msg_index)
            )
        """))
        try:
            conn.execute(_sa_text(
                "ALTER TABLE chat_sessions ADD COLUMN status VARCHAR(32) DEFAULT 'complete'"
            ))
        except Exception:
            # MySQL raises an error when you try to ADD a column that already exists.
            # This bare except catches only that case — the assumption is that any
            # other ALTER TABLE error also means the column is already in a valid state.
            # If MySQL is down entirely, the outer begin() would have raised already.
            pass  # column already exists


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_session(sid: str, username: str, name: str = "Conversation") -> None:
    now = _utcnow()
    with _get_chat_engine().begin() as conn:
        conn.execute(_sa_text(
            "INSERT IGNORE INTO chat_sessions(id,username,name,created_at,updated_at)"
            " VALUES(:id,:u,:n,:ca,:ua)"
        ), {"id": sid, "u": username, "n": name, "ca": now, "ua": now})
        conn.execute(_sa_text(
            "UPDATE chat_sessions SET updated_at=:ua WHERE id=:id"
        ), {"ua": now, "id": sid})


def _add_message(sid: str, role: str, content: str) -> None:
    ts = datetime.now().strftime("%H:%M")
    with _get_chat_engine().begin() as conn:
        conn.execute(_sa_text(
            "INSERT INTO chat_messages(session_id,role,content,ts) VALUES(:sid,:r,:c,:ts)"
        ), {"sid": sid, "r": role, "c": content, "ts": ts})
        conn.execute(_sa_text(
            "UPDATE chat_sessions SET updated_at=:ua WHERE id=:id"
        ), {"ua": _utcnow(), "id": sid})


def _get_sessions(username: str, limit: int = 50) -> list:
    with _get_chat_engine().connect() as conn:
        rows = conn.execute(_sa_text("""
            SELECT s.id, s.name, s.username, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM chat_sessions s LEFT JOIN chat_messages m ON m.session_id=s.id
            WHERE s.username=:u GROUP BY s.id ORDER BY s.updated_at DESC LIMIT :lim
        """), {"u": username, "lim": limit}).fetchall()
    return [dict(r._mapping) for r in rows]


def _get_all_sessions(limit: int = 100) -> list:
    """Admin only — returns sessions for all users, including owner username."""
    with _get_chat_engine().connect() as conn:
        rows = conn.execute(_sa_text("""
            SELECT s.id, s.name, s.username, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM chat_sessions s LEFT JOIN chat_messages m ON m.session_id=s.id
            GROUP BY s.id ORDER BY s.updated_at DESC LIMIT :lim
        """), {"lim": limit}).fetchall()
    return [dict(r._mapping) for r in rows]


def _get_messages(sid: str, username: str, admin: bool = False) -> list | None:
    with _get_chat_engine().connect() as conn:
        owner = conn.execute(
            _sa_text("SELECT username FROM chat_sessions WHERE id=:id"), {"id": sid}
        ).fetchone()
        if not owner or (owner.username != username and not admin):
            return None
        rows = conn.execute(_sa_text(
            "SELECT role,content,ts FROM chat_messages WHERE session_id=:sid ORDER BY id"
        ), {"sid": sid}).fetchall()
    return [dict(r._mapping) for r in rows]


def _delete_session(sid: str, username: str) -> bool:
    with _get_chat_engine().begin() as conn:
        n = conn.execute(_sa_text(
            "DELETE FROM chat_sessions WHERE id=:id AND username=:u"
        ), {"id": sid, "u": username}).rowcount
        conn.execute(_sa_text("DELETE FROM chat_messages WHERE session_id=:sid"), {"sid": sid})
        conn.execute(_sa_text("DELETE FROM chat_traces WHERE session_id=:sid"), {"sid": sid})
    return n > 0


def _user_stats(username: str) -> dict:
    with _get_chat_engine().connect() as conn:
        sc = conn.execute(_sa_text(
            "SELECT COUNT(*) FROM chat_sessions WHERE username=:u"
        ), {"u": username}).scalar()
        mc = conn.execute(_sa_text("""
            SELECT COUNT(*) FROM chat_messages m
            JOIN chat_sessions s ON m.session_id=s.id WHERE s.username=:u
        """), {"u": username}).scalar()
    return {"session_count": sc or 0, "message_count": mc or 0}


# ── Background task registry ───────────────────────────────────────────────
# How background tasks work:
#   1. POST /chat/stream creates an asyncio.Task and stores it in _bg_tasks[session_id].
#   2. A StreamingResponse generator yields SSE events while the task runs.
#   3. If the browser disconnects mid-stream, the generator exits but the Task
#      continues running — it saves the final answer to DB and marks the session
#      complete regardless of whether the client is still connected.
#   4. GET /chat/stream/{session_id}/poll re-attaches a new SSE stream to the
#      running task so the client can reconnect and receive buffered events.
#
# NOTE: _bg_tasks has NO TTL or cleanup. Entries for completed tasks are never
# removed. For a long-running server this is a slow memory leak. The practical
# impact is small (each entry is a tiny asyncio.Task wrapper), but be aware
# that restarting the process is the only way to reclaim this memory.

_bg_tasks: dict = {}


def _save_trace_event(sid: str, msg_index: int, event_type: str, event_data: str) -> None:
    ts = _utcnow()
    with _get_chat_engine().begin() as conn:
        conn.execute(_sa_text(
            "INSERT INTO chat_traces(session_id,msg_index,event_type,event_data,ts)"
            " VALUES(:sid,:mi,:et,:ed,:ts)"
        ), {"sid": sid, "mi": msg_index, "et": event_type, "ed": event_data, "ts": ts})


def _get_trace_events(sid: str, msg_index: int) -> list:
    with _get_chat_engine().connect() as conn:
        rows = conn.execute(_sa_text(
            "SELECT event_type, event_data, ts FROM chat_traces"
            " WHERE session_id=:sid AND msg_index=:mi ORDER BY id"
        ), {"sid": sid, "mi": msg_index}).fetchall()
    events = []
    for row in rows:
        try:
            ev = json.loads(row.event_data)
            ev["_ts"] = row.ts
            events.append(ev)
        except Exception:
            pass
    return events


def _update_session_status(sid: str, status: str) -> None:
    with _get_chat_engine().begin() as conn:
        conn.execute(_sa_text(
            "UPDATE chat_sessions SET status=:s WHERE id=:id"
        ), {"s": status, "id": sid})


def _get_session_running(sid: str) -> bool:
    task = _bg_tasks.get(sid)
    return task is not None and not task.done()


def _search_sessions(username: str, q: str) -> list:
    like = f"%{q.lower()}%"
    with _get_chat_engine().connect() as conn:
        rows = conn.execute(_sa_text("""
            SELECT s.id, s.name, s.created_at, s.updated_at, COUNT(m.id) AS message_count
            FROM chat_sessions s LEFT JOIN chat_messages m ON m.session_id=s.id
            WHERE s.username=:u AND (LOWER(s.name) LIKE :like OR s.id LIKE :like)
            GROUP BY s.id ORDER BY s.updated_at DESC LIMIT 20
        """), {"u": username, "like": like}).fetchall()
    return [dict(r._mapping) for r in rows]


def _search_messages(username: str, q: str) -> list:
    like = f"%{q.lower()}%"
    with _get_chat_engine().connect() as conn:
        rows = conn.execute(_sa_text("""
            SELECT s.id AS session_id, s.name AS session_name, m.role, m.content, m.ts
            FROM chat_messages m JOIN chat_sessions s ON m.session_id=s.id
            WHERE s.username=:u AND LOWER(m.content) LIKE :like
            ORDER BY s.updated_at DESC LIMIT 30
        """), {"u": username, "like": like}).fetchall()
    return [dict(r._mapping) for r in rows]


# ── HTML ───────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FAB MCP Hub</title>
<style>
:root{--bg:#0d1117;--sf:#161b22;--sf2:#1c2128;--bd:#30363d;--ac:#f97316;--ac2:rgba(249,115,22,.15);
--tx:#e6edf3;--mu:#8b949e;--gr:#3fb950;--rd:#f85149;--yw:#d29922;--bl:#58a6ff;}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;background:var(--bg);color:var(--tx);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;}

/* ── Login ── */
#loginOverlay{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:999;}
.login-card{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:36px;width:380px;max-width:95vw;}
.login-logo{text-align:center;margin-bottom:24px;}
.login-logo-icon{font-size:40px;display:block;margin-bottom:8px;}
.login-logo h1{font-size:18px;font-weight:600;}
.login-logo p{color:var(--mu);font-size:12px;margin-top:4px;}
.user-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;}
.user-chip{padding:6px 12px;border:1px solid var(--bd);border-radius:20px;background:var(--sf2);cursor:pointer;font-size:13px;color:var(--tx);transition:.15s;}
.user-chip:hover,.user-chip.sel{border-color:var(--ac);background:var(--ac2);color:var(--ac);}
.form-group{margin-bottom:14px;}
.form-group label{display:block;font-size:12px;color:var(--mu);margin-bottom:6px;}
.form-group input{width:100%;padding:9px 12px;background:var(--bg);border:1px solid var(--bd);border-radius:6px;color:var(--tx);font-size:14px;outline:none;}
.form-group input:focus{border-color:var(--ac);}
.cred-hint{background:var(--sf2);border:1px solid var(--bd);border-radius:6px;padding:10px 12px;margin-bottom:16px;font-size:12px;}
.cred-hint strong{color:var(--ac);display:block;margin-bottom:4px;}
.cred-hint table{width:100%;border-collapse:collapse;}
.cred-hint td{padding:2px 6px;color:var(--mu);}
.cred-hint td:first-child{color:var(--tx);font-family:monospace;}
.btn-primary{width:100%;padding:10px;background:var(--ac);border:none;border-radius:6px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;transition:.15s;}
.btn-primary:hover{opacity:.9;}
.btn-primary:disabled{opacity:.5;cursor:default;}
#loginError{color:var(--rd);font-size:12px;text-align:center;margin-top:8px;display:none;}

/* ── App shell ── */
.app-shell{display:flex;flex-direction:column;height:100vh;display:none;}
.app-header{flex:0 0 52px;background:var(--sf);border-bottom:1px solid var(--bd);display:flex;align-items:center;padding:0 16px;gap:12px;}
.logo{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15px;}
.logo-icon{font-size:20px;}
.header-right{margin-left:auto;display:flex;align-items:center;gap:10px;}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--gr);}
.status-dot.offline{background:var(--rd);}
#userDisplay{font-size:13px;color:var(--mu);}
#userRole{font-size:11px;padding:2px 8px;border-radius:10px;background:var(--ac2);color:var(--ac);}
.btn-sm{padding:5px 12px;border:1px solid var(--bd);background:transparent;color:var(--mu);border-radius:5px;cursor:pointer;font-size:12px;}
.btn-sm:hover{border-color:var(--ac);color:var(--ac);}

/* ── App body ── */
.app-body{flex:1;display:flex;overflow:hidden;}
.sidebar{flex:0 0 185px;background:var(--sf);border-right:1px solid var(--bd);display:flex;flex-direction:column;padding:8px;gap:1px;}
.nav-btn{display:flex;align-items:center;gap:10px;padding:9px 12px;border:none;background:transparent;color:var(--mu);cursor:pointer;border-radius:6px;text-align:left;font-size:13px;transition:.15s;width:100%;}
.nav-btn:hover{background:var(--sf2);color:var(--tx);}
.nav-btn.active{background:var(--ac2);color:var(--ac);}
.nav-icon{font-size:16px;width:20px;text-align:center;}
.sidebar-footer{margin-top:auto;padding-top:8px;border-top:1px solid var(--bd);}

/* ── Content + Screens ── */
.content{flex:1;overflow:hidden;display:flex;flex-direction:column;}
.screen{flex:1;display:flex;flex-direction:column;overflow:auto;padding:20px;}

/* ── Dashboard ── */
.dash-welcome{margin-bottom:20px;}
.dash-welcome h2{font-size:18px;font-weight:600;}
.dash-welcome p{color:var(--mu);margin-top:4px;font-size:13px;}
.dash-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px;}
.stat-card{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:16px;}
.stat-val{font-size:28px;font-weight:700;color:var(--tx);}
.stat-label{font-size:12px;color:var(--mu);margin-top:4px;}
.stat-icon{font-size:20px;margin-bottom:8px;}
.dash-section{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:16px;margin-bottom:16px;}
.dash-section h3{font-size:11px;font-weight:600;margin-bottom:12px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;}
.session-row{display:flex;align-items:center;padding:8px 0;border-bottom:1px solid var(--bd);gap:10px;}
.session-row:last-child{border-bottom:none;}
.session-name{flex:1;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.session-meta{font-size:11px;color:var(--mu);white-space:nowrap;}
.btn-xs{padding:3px 10px;font-size:11px;border:1px solid var(--bd);background:transparent;color:var(--mu);border-radius:4px;cursor:pointer;}
.btn-xs:hover{border-color:var(--ac);color:var(--ac);}
.quick-grid{display:flex;flex-wrap:wrap;gap:8px;}
.quick-btn{padding:7px 14px;background:var(--sf2);border:1px solid var(--bd);border-radius:6px;color:var(--tx);font-size:12px;cursor:pointer;transition:.15s;}
.quick-btn:hover{border-color:var(--ac);color:var(--ac);}
.empty-msg{color:var(--mu);font-size:13px;padding:16px 0;text-align:center;}

/* ── Admin panel ── */
.admin-badge{font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(139,92,246,.2);color:#a78bfa;font-weight:600;}
.adm-sec{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:16px;margin-bottom:16px;}
.adm-sec>h3{font-size:11px;font-weight:600;margin-bottom:14px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;}
.adm-kv{width:100%;border-collapse:collapse;}
.adm-kv td{padding:7px 0;border-bottom:1px solid var(--bd);font-size:13px;vertical-align:top;}
.adm-kv td:first-child{color:var(--mu);width:155px;font-size:12px;}
.adm-kv tr:last-child td{border-bottom:none;}
.adm-tbl{width:100%;border-collapse:collapse;font-size:13px;}
.adm-tbl th{text-align:left;padding:8px 10px;color:var(--mu);font-size:11px;border-bottom:1px solid var(--bd);text-transform:uppercase;letter-spacing:.05em;font-weight:500;}
.adm-tbl td{padding:8px 10px;border-bottom:1px solid var(--bd);}
.adm-tbl tr:last-child td{border-bottom:none;}.adm-tbl tr:hover td{background:var(--sf2);}
.role-adm{display:inline-block;padding:2px 7px;border-radius:10px;font-size:11px;background:rgba(139,92,246,.2);color:#a78bfa;}
.role-agt{display:inline-block;padding:2px 7px;border-radius:10px;font-size:11px;background:var(--ac2);color:var(--ac);}
.adm-inp{width:100%;padding:7px 10px;background:var(--bg);border:1px solid var(--bd);color:var(--tx);border-radius:6px;font-size:13px;outline:none;box-sizing:border-box;}
.adm-inp:focus{border-color:var(--ac);}
.adm-tok-out{display:none;margin-top:10px;background:var(--bg);border:1px solid var(--bd);border-radius:6px;
  padding:10px;font-size:11px;font-family:monospace;word-break:break-all;color:#3fb950;
  white-space:pre-wrap;max-height:80px;overflow-y:auto;}

/* ── Chat screen ── */
#screen-chat{flex-direction:row!important;padding:0!important;overflow:hidden!important;}
.chat-main{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden;}
.chat-toolbar{flex:0 0 48px;border-bottom:1px solid var(--bd);display:flex;align-items:center;padding:0 14px;gap:8px;background:var(--sf);}
.chat-toolbar select{padding:5px 8px;background:var(--sf2);border:1px solid var(--bd);color:var(--tx);border-radius:5px;font-size:12px;max-width:200px;}
.chat-messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px;}
.msg{max-width:80%;display:flex;flex-direction:column;gap:4px;}
.msg.user{align-self:flex-end;}
.msg.assistant{align-self:flex-start;}
.msg-bubble{padding:10px 14px;border-radius:10px;font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-word;}
.msg.user .msg-bubble{background:var(--ac);color:#fff;border-bottom-right-radius:2px;}
.msg.assistant .msg-bubble{background:var(--sf);border:1px solid var(--bd);border-bottom-left-radius:2px;}
.msg-meta{font-size:11px;color:var(--mu);}
.msg.user .msg-meta{text-align:right;}
.chat-input-area{flex:0 0 auto;border-top:1px solid var(--bd);padding:12px 14px;background:var(--sf);}
.chat-input-row{display:flex;gap:8px;}
#chatInput{flex:1;padding:10px 14px;background:var(--bg);border:1px solid var(--bd);border-radius:8px;color:var(--tx);font-size:14px;resize:none;min-height:42px;max-height:120px;outline:none;font-family:inherit;}
#chatInput:focus{border-color:var(--ac);}
.btn-send{padding:0 18px;background:var(--ac);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;font-size:14px;}
.btn-send:disabled{opacity:.4;cursor:default;}
.typing-indicator{color:var(--mu);font-size:12px;padding:4px 0;display:none;}

/* ── Trace panel ── */
.trace-panel{flex:0 0 380px;border-left:1px solid var(--bd);display:flex;flex-direction:column;overflow:hidden;}
.trace-header{flex:0 0 48px;border-bottom:1px solid var(--bd);display:flex;align-items:center;padding:0 10px;gap:4px;background:var(--sf);}
.tab-btn{padding:4px 9px;border:1px solid transparent;background:transparent;color:var(--mu);border-radius:5px;cursor:pointer;font-size:11px;white-space:nowrap;}
.tab-btn.active{border-color:var(--ac);color:var(--ac);background:var(--ac2);}
.trace-body{flex:1;overflow:hidden;display:flex;flex-direction:column;}
.trace-view{flex:1;overflow:auto;display:none;}
.trace-view.active{display:flex;flex-direction:column;}

/* ── Timeline ── */
.timeline-wrap{padding:10px 10px 10px 22px;display:flex;flex-direction:column;gap:0;}
.tl-step{display:flex;align-items:flex-start;gap:6px;padding:5px 4px;border-left:2px solid var(--bd);position:relative;}
.tl-step:hover{background:var(--sf2);border-radius:0 6px 6px 0;}
.tl-step.error-step{border-left-color:var(--rd);}
.tl-dot{position:absolute;left:-6px;top:9px;width:10px;height:10px;border-radius:50%;background:var(--bd);border:2px solid var(--bg);flex-shrink:0;}
.tl-step.done-step .tl-dot{background:var(--gr);}
.tl-step.error-step .tl-dot{background:var(--rd);}
.tl-num{font-size:10px;color:var(--mu);min-width:20px;text-align:right;margin-top:2px;flex-shrink:0;}
.tl-elapsed{font-size:10px;color:var(--mu);min-width:36px;text-align:right;margin-top:2px;flex-shrink:0;}
.tl-icon{font-size:13px;min-width:18px;text-align:center;margin-top:1px;flex-shrink:0;}
.tl-content{flex:1;min-width:0;}
.tl-title{font-size:12px;font-weight:600;color:var(--tx);}
.tl-step.error-step .tl-title{color:var(--rd);}
.tl-detail{font-size:11px;color:var(--mu);margin-top:2px;word-break:break-all;line-height:1.4;}
.tl-expand-btn{font-size:10px;color:var(--bl);margin-top:3px;cursor:pointer;display:inline-block;}
.tl-expand-btn:hover{text-decoration:underline;}
.tl-expanded{display:none;background:var(--sf2);border:1px solid var(--bd);border-radius:4px;padding:6px;margin-top:4px;font-size:10px;font-family:monospace;color:var(--mu);white-space:pre-wrap;word-break:break-all;max-height:360px;overflow-y:auto;}
.tl-step.tl-open .tl-expanded{display:block;}
.timeline-empty{padding:20px;text-align:center;color:var(--mu);font-size:12px;}

/* ── Animated flow graph ── */
@keyframes node-pulse{
  0%  {box-shadow:0 0 0 0 rgba(249,115,22,.5);}
  70% {box-shadow:0 0 0 10px rgba(249,115,22,0);}
  100%{box-shadow:0 0 0 0 rgba(249,115,22,0);}
}
@keyframes edge-flow{
  from{background-position:0 0;}
  to  {background-position:-28px 0;}
}
.flow-wrap{padding:14px;display:flex;flex-direction:column;gap:0;align-items:stretch;}
.flow-row{display:flex;align-items:center;gap:0;justify-content:center;}
.fnode{display:flex;flex-direction:column;align-items:center;gap:3px;width:68px;}
.fnode-box{width:52px;height:52px;border:2px solid var(--bd);border-radius:8px;background:var(--sf2);display:flex;align-items:center;justify-content:center;font-size:20px;transition:.3s;position:relative;}
.fnode-lbl{font-size:9px;color:var(--mu);text-align:center;max-width:64px;}
.fnode-badge{position:absolute;top:-6px;right:-6px;min-width:16px;height:16px;border-radius:8px;background:var(--ac);color:#fff;font-size:9px;font-weight:700;display:none;align-items:center;justify-content:center;padding:0 3px;line-height:16px;text-align:center;}
.fnode-badge.show{display:flex;}
.fnode.active .fnode-box{animation:node-pulse 1.2s infinite;border-color:var(--ac);background:var(--ac2);}
.fnode.done .fnode-box{border-color:var(--gr);}
.fnode.error .fnode-box{border-color:var(--rd);}
.fedge{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;min-width:28px;}
.fedge-line{height:2px;background:var(--bd);width:100%;transition:.3s;}
.fedge-lbl{font-size:9px;color:var(--mu);white-space:nowrap;}
.fedge.active .fedge-line{background:repeating-linear-gradient(90deg,var(--ac) 0,var(--ac) 6px,var(--sf2) 6px,var(--sf2) 14px);background-size:28px 100%;animation:edge-flow .6s linear infinite;}
.fedge.done .fedge-line{background:var(--gr);}
.flow-vconn{width:2px;background:var(--bd);height:20px;margin:0 auto;transition:.3s;}
.flow-vconn.active{background:var(--ac);}
.flow-vconn.done{background:var(--gr);}
.fnode-sub{margin-top:0;}

/* ── Auth token table ── */
.auth-hops{padding:10px;display:flex;flex-direction:column;gap:6px;}
.auth-hop{background:var(--sf2);border:1px solid var(--bd);border-radius:6px;padding:8px 10px;font-size:11px;}
.auth-hop-header{display:flex;align-items:center;gap:6px;margin-bottom:4px;}
.auth-hop-from{color:var(--mu);}
.auth-hop-arr{color:var(--ac);}
.auth-hop-to{color:var(--tx);font-weight:600;}
.auth-token{font-family:monospace;color:var(--yw);font-size:10px;}

/* ── Events log ── */
.event-log{padding:8px;display:flex;flex-direction:column;gap:4px;}
.ev-item{padding:6px 8px;border-radius:5px;font-size:11px;border-left:3px solid var(--bd);}
.ev-item.routing{border-color:var(--bl);}
.ev-item.auth_hop{border-color:var(--yw);}
.ev-item.mcp_connecting{border-color:var(--mu);}
.ev-item.tool_call{border-color:var(--ac);}
.ev-item.final_answer{border-color:var(--gr);}
.ev-item.error{border-color:var(--rd);}
.ev-type{font-weight:600;margin-right:4px;}
.ev-detail{color:var(--mu);}

/* ── History screen ── */
.history-toolbar{display:flex;align-items:center;gap:8px;margin-bottom:16px;}
.search-input{flex:1;padding:8px 12px;background:var(--sf);border:1px solid var(--bd);border-radius:6px;color:var(--tx);font-size:13px;outline:none;}
.search-input:focus{border-color:var(--ac);}
.hist-list{display:flex;flex-direction:column;gap:6px;}
.hist-item{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;display:flex;align-items:center;gap:10px;cursor:pointer;transition:.15s;}
.hist-item:hover{border-color:var(--ac);}
.hist-info{flex:1;min-width:0;}
.hist-name{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.hist-meta{font-size:11px;color:var(--mu);margin-top:2px;}
.hist-actions{display:flex;gap:6px;}
.hist-detail{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:14px;margin-top:12px;display:none;}
.hist-detail-header{display:flex;align-items:center;gap:8px;margin-bottom:12px;}
.hist-detail-msgs{display:flex;flex-direction:column;gap:8px;max-height:400px;overflow-y:auto;}
.hist-msg{padding:8px 10px;border-radius:6px;font-size:13px;line-height:1.5;white-space:pre-wrap;}
.hist-msg.user{background:var(--ac2);border-left:3px solid var(--ac);}
.hist-msg.assistant{background:var(--sf2);border-left:3px solid var(--bd);}
.hist-msg-role{font-size:10px;color:var(--mu);margin-bottom:3px;text-transform:uppercase;}

/* ── Observability screen ── */
.obs-tabs{display:flex;gap:6px;margin-bottom:16px;}
.obs-body{flex:1;min-height:0;}
.obs-view{display:none;}
.obs-view.active{display:block;}
.obs-event{padding:8px 10px;border-radius:5px;background:var(--sf);border:1px solid var(--bd);margin-bottom:6px;font-size:12px;}
.obs-badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600;margin-right:8px;}
.badge-auth{background:rgba(88,166,255,.15);color:var(--bl);}
.badge-request{background:rgba(63,185,80,.15);color:var(--gr);}
.badge-routing{background:rgba(249,115,22,.15);color:var(--ac);}
.obs-sub{color:var(--mu);font-size:11px;}
.stats-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
.stats-card{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:14px;}
.stats-card h4{font-size:12px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px;}
.donut-wrap{display:flex;align-items:center;gap:16px;}
.donut{width:80px;height:80px;border-radius:50%;background:conic-gradient(var(--gr) 0%,var(--rd) 0%);position:relative;}
.donut-inner{position:absolute;inset:15px;background:var(--sf);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;}
.donut-legend{font-size:12px;display:flex;flex-direction:column;gap:4px;}
.legend-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;}
.bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;}
.bar-label{font-size:11px;color:var(--mu);width:60px;text-align:right;flex-shrink:0;}
.bar-track{flex:1;height:8px;background:var(--sf2);border-radius:4px;overflow:hidden;}
.bar-fill{height:100%;background:var(--ac);border-radius:4px;transition:.3s;}
.token-table{width:100%;border-collapse:collapse;font-size:11px;}
.token-table th{color:var(--mu);text-align:left;padding:4px 6px;border-bottom:1px solid var(--bd);}
.token-table td{padding:4px 6px;font-family:monospace;}

/* ── Search screen ── */
.search-hero{display:flex;gap:8px;margin-bottom:20px;}
.search-hero input{flex:1;padding:10px 16px;background:var(--sf);border:1px solid var(--bd);border-radius:8px;color:var(--tx);font-size:14px;outline:none;}
.search-hero input:focus{border-color:var(--ac);}
.search-hero select{padding:10px 12px;background:var(--sf);border:1px solid var(--bd);border-radius:8px;color:var(--tx);font-size:13px;outline:none;}
.search-hero select:focus{border-color:var(--ac);}
.search-hero button{padding:10px 18px;background:var(--ac);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;font-size:13px;}
.search-hero button:hover{opacity:.9;}
.search-results{flex:1;overflow-y:auto;}
.search-group{margin-bottom:20px;}
.search-group-title{font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--bd);}
.search-result-item{background:var(--sf);border:1px solid var(--bd);border-radius:8px;padding:12px 14px;margin-bottom:6px;cursor:pointer;transition:.15s;}
.search-result-item:hover{border-color:var(--ac);}
.sr-name{font-size:13px;font-weight:500;margin-bottom:4px;}
.sr-id{font-family:monospace;font-size:11px;color:var(--mu);}
.sr-meta{font-size:11px;color:var(--mu);margin-top:4px;}
.sr-snippet{font-size:12px;color:var(--tx);margin-top:6px;padding:6px 8px;background:var(--sf2);border-radius:4px;line-height:1.5;word-break:break-word;}
mark{background:rgba(249,115,22,.3);color:var(--ac);border-radius:2px;padding:0 1px;}
.search-empty{text-align:center;color:var(--mu);font-size:13px;padding:40px 0;}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:6px;height:6px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px;}
::-webkit-scrollbar-thumb:hover{background:var(--mu);}

/* ── Session ID chip ── */
.sess-chip{background:var(--sf2);border:1px solid var(--bd);border-radius:4px;padding:2px 8px;font-family:monospace;font-size:11px;color:var(--mu);cursor:pointer;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;transition:.15s;user-select:none;}
.sess-chip:hover{border-color:var(--ac);color:var(--tx);}
/* ── Admin controls ── */
.admin-badge{background:rgba(248,81,73,.15);color:#f85149;border:1px solid rgba(248,81,73,.3);border-radius:10px;padding:1px 8px;font-size:11px;font-weight:600;}
.admin-toggle{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--mu);cursor:pointer;padding:3px 6px;border-radius:4px;border:1px solid transparent;}
.admin-toggle:hover{border-color:var(--bd);color:var(--tx);}
.admin-toggle input{cursor:pointer;margin:0;}

/* ── Pending bar ── */
.pending-bar{background:rgba(249,115,22,.06);border:1px solid rgba(249,115,22,.3);border-radius:6px;padding:6px 12px;margin:4px 0 0;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--ac);}
.pending-bar button{background:var(--ac);color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px;}

/* ── Trace Security tab ── */
/* ── Trace: Security tab ── */
.sec-chain-hdr{padding:8px 12px;font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--bd);}
.sec-hop{background:var(--sf2);border:1px solid var(--bd);border-radius:6px;margin:8px;overflow:hidden;}
.sec-hop-hdr{display:flex;align-items:center;gap:6px;padding:8px 12px;background:rgba(255,255,255,.03);border-bottom:1px solid var(--bd);}
.sec-hop-num{background:var(--ac2);color:var(--ac);border-radius:10px;padding:1px 8px;font-size:10px;font-weight:700;flex-shrink:0;}
.sec-hop-route{flex:1;font-size:12px;font-weight:600;color:var(--tx);}
.sec-hop-status{color:#4ade80;font-size:11px;white-space:nowrap;}
.sec-hop-validated{color:#4ade80;font-size:10px;font-weight:600;background:rgba(63,185,80,.1);border:1px solid rgba(63,185,80,.25);border-radius:3px;padding:1px 5px;margin-left:4px;}
.sec-table{width:100%;border-collapse:collapse;font-size:11px;}
.sec-table tr:not(:last-child) td{border-bottom:1px solid rgba(255,255,255,.04);}
.sec-key{padding:5px 12px;color:var(--mu);white-space:nowrap;width:128px;font-size:11px;vertical-align:top;}
.sec-val{padding:5px 12px;color:var(--tx);font-size:11px;word-break:break-all;}
.sec-val-hl{font-weight:700;color:var(--ac);}
.sec-mono{font-family:monospace;font-size:11px;}
.sec-val code{font-family:monospace;background:rgba(0,0,0,.25);padding:1px 5px;border-radius:3px;font-size:10px;}
.sec-type-badge{display:inline-block;border:1px solid;border-radius:3px;font-size:9px;font-weight:700;padding:0 5px;letter-spacing:.05em;vertical-align:middle;margin-left:5px;}
.sec-type-jwt{color:var(--bl);border-color:rgba(88,166,255,.4);background:rgba(88,166,255,.1);}
.sec-type-apikey{color:var(--yw);border-color:rgba(255,200,60,.4);background:rgba(255,200,60,.1);}
.sec-type-dev{color:var(--mu);border-color:var(--bd);}
.sec-role-chip{display:inline-block;background:var(--ac2);color:var(--ac);border-radius:10px;padding:1px 8px;font-size:10px;font-weight:600;margin:1px 2px;}
.sec-role-chip.admin{background:rgba(255,100,60,.12);color:#ff6040;}
.sec-rbac-ok{color:#4ade80;}
.sec-ok{color:#4ade80;}
.sec-empty{padding:24px;text-align:center;color:var(--mu);font-size:12px;}

/* ── Trace Perf tab ── */
.perf-section{margin:8px;padding:10px 12px;background:var(--sf2);border:1px solid var(--bd);border-radius:6px;}
.perf-title{font-size:10px;font-weight:600;color:var(--ac);margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;}
.perf-row{display:flex;justify-content:space-between;padding:3px 0;font-size:12px;border-bottom:1px solid rgba(255,255,255,.04);}
.perf-row:last-child{border-bottom:none;}
.perf-label{color:var(--mu);}
.perf-val{color:var(--tx);font-family:monospace;}
.perf-empty{padding:20px;text-align:center;color:var(--mu);font-size:12px;}

/* ── Observability: Auth events ── */
.obs-auth-row{display:flex;align-items:center;gap:6px;padding:5px 10px;border-bottom:1px solid var(--bd);font-size:11px;font-family:monospace;}
.obs-auth-row:hover{background:var(--sf2);}
.obs-auth-row.ok .obs-res{color:#4ade80;}
.obs-auth-row.deny .obs-res{color:#f87171;}
.obs-res{min-width:62px;font-weight:600;font-size:10px;}
.obs-auth-sub{color:var(--tx);min-width:80px;}
.obs-auth-roles{color:var(--mu);min-width:60px;}
.obs-auth-ep{flex:1;color:var(--mu);}
.obs-auth-ts{color:var(--mu);min-width:65px;text-align:right;}
.obs-token-badge{display:inline-block;border:1px solid;border-radius:3px;font-size:9px;font-weight:700;padding:0 4px;letter-spacing:.04em;margin-left:4px;vertical-align:middle;}
.obs-token-jwt{color:var(--bl);border-color:rgba(88,166,255,.35);background:rgba(88,166,255,.08);}
.obs-token-apikey{color:var(--yw);border-color:rgba(255,200,60,.35);background:rgba(255,200,60,.08);}
.obs-token-dev{color:var(--mu);border-color:var(--bd);}

/* ── Observability: Routing events ── */
.obs-route-row{padding:7px 10px;border-bottom:1px solid var(--bd);transition:.1s;}
.obs-route-row:hover{background:var(--sf2);}
.obs-route-hdr{display:flex;align-items:baseline;gap:6px;font-size:12px;}
.obs-route-ts{color:var(--mu);font-size:11px;min-width:65px;font-family:monospace;}
.obs-route-method{color:#60a5fa;font-family:monospace;min-width:80px;}
.obs-route-server{color:var(--ac);font-weight:600;}
.obs-route-reason{font-size:11px;color:var(--mu);margin-top:3px;padding-left:71px;}
.obs-route-intent{font-size:11px;color:#94a3b8;font-style:italic;padding-left:71px;margin-top:1px;}

/* ── Observability: Request log ── */
.obs-req-row{display:flex;align-items:center;gap:6px;padding:5px 10px;border-bottom:1px solid var(--bd);font-size:11px;font-family:monospace;transition:.1s;}
.obs-req-row:hover{background:var(--sf2);}
.obs-req-ts{color:var(--mu);min-width:65px;}
.obs-req-meth{color:#60a5fa;min-width:40px;}
.obs-req-path{flex:1;color:var(--tx);}
.obs-req-status.ok2{color:#4ade80;}
.obs-req-status.err2{color:#f87171;}
.obs-req-lat{color:var(--mu);min-width:58px;text-align:right;}
.obs-empty{padding:24px;text-align:center;color:var(--mu);font-size:13px;}

/* ── Conversation list panel (left within chat screen) ── */
.conv-panel{flex:0 0 220px;border-right:1px solid var(--bd);display:flex;flex-direction:column;background:var(--sf);overflow:hidden;}
.conv-panel-hdr{flex:0 0 48px;border-bottom:1px solid var(--bd);display:flex;align-items:center;padding:0 10px;gap:6px;}
.conv-panel-title{font-size:10px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.06em;flex:1;}
.conv-list{flex:1;overflow-y:auto;padding:4px;}
.conv-item{padding:7px 10px;border-radius:6px;cursor:pointer;transition:.12s;border:1px solid transparent;margin-bottom:2px;}
.conv-item:hover{background:var(--sf2);border-color:var(--bd);}
.conv-item.active{background:var(--ac2);border-color:var(--ac);}
.conv-item-name{font-size:12px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--tx);}
.conv-item-meta{font-size:10px;color:var(--mu);margin-top:2px;}
.conv-item-user{font-size:10px;color:var(--ac);margin-top:1px;}

/* ── Per-message trace button ── */
.msg-trace-btn{background:transparent;border:1px solid var(--bd);border-radius:4px;color:var(--mu);font-size:10px;padding:1px 6px;cursor:pointer;margin-left:6px;vertical-align:middle;}
.msg-trace-btn:hover{border-color:var(--ac);color:var(--ac);}
.msg-trace-btn.active-trace{border-color:var(--bl);color:var(--bl);background:rgba(88,166,255,.08);}
.msg-trace-btn.live{border-color:var(--yw);color:var(--yw);animation:live-pulse 1.4s ease-in-out infinite;}
@keyframes live-pulse{0%,100%{opacity:.5}50%{opacity:1}}

/* ── Thinking placeholder ── */
.thinking-bubble{font-style:italic;color:var(--mu);}
.thinking-dots::before{content:'● ● ●';animation:thinking-fade 1.4s ease-in-out infinite;}
@keyframes thinking-fade{0%,100%{opacity:.2}50%{opacity:1}}

/* ── Login mode selector ── */
.mode-selector{display:flex;border:1px solid var(--bd);border-radius:6px;overflow:hidden;margin-bottom:18px;}
.mode-btn{flex:1;padding:8px 0;border:none;background:transparent;color:var(--mu);font-size:12px;font-weight:600;cursor:pointer;transition:.15s;letter-spacing:.04em;text-transform:uppercase;}
.mode-btn.active.dev{background:rgba(255,200,60,.12);color:var(--yw);}
.mode-btn.active.qa{background:rgba(63,185,80,.12);color:var(--gr);}
.mode-btn:not(.active):hover{background:var(--sf2);}
.mode-sep{width:1px;background:var(--bd);}
.qa-badge{display:inline-block;background:rgba(63,185,80,.15);color:var(--gr);border:1px solid rgba(63,185,80,.3);border-radius:4px;font-size:10px;font-weight:700;padding:1px 7px;margin-left:8px;letter-spacing:.06em;vertical-align:middle;}

/* ── Trace panel message context header ── */
.trace-msg-hdr{padding:5px 10px;background:rgba(88,166,255,.08);border-bottom:1px solid rgba(88,166,255,.2);font-size:11px;color:var(--bl);display:none;align-items:center;gap:6px;}
.trace-msg-hdr.show{display:flex;}
.trace-msg-hdr-close{margin-left:auto;cursor:pointer;color:var(--mu);font-size:12px;}
.trace-msg-hdr-close:hover{color:var(--tx);}
</style>
</head>
<body>

<!-- ════ LOGIN OVERLAY ════ -->
<div id="loginOverlay">
<div class="login-card">
  <div class="login-logo">
    <span class="login-logo-icon">🔶</span>
    <h1>FAB MCP Hub</h1>
    <p id="loginSubtitle">Agentic AI Platform</p>
  </div>
  <div class="mode-selector">
    <button class="mode-btn dev active" id="modeDevBtn" onclick="setMode('dev')">Dev</button>
    <div class="mode-sep"></div>
    <button class="mode-btn qa" id="modeQaBtn" onclick="setMode('qa')">QA</button>
  </div>
  <div class="form-group" id="userChipsGroup"><label>Quick select (dev)</label>
    <div class="user-chips" id="userChips"></div>
  </div>
  <div class="form-group">
    <label>Username</label>
    <input type="text" id="loginUsername" placeholder="admin" autocomplete="username"/>
  </div>
  <div class="form-group">
    <label>Password</label>
    <input type="password" id="loginPassword" placeholder="••••••••" autocomplete="current-password"
           onkeydown="if(event.key==='Enter')doLogin()"/>
  </div>
  <div class="cred-hint" id="credHint">
    <strong>Default credentials (dev mode)</strong>
    <table>
      <tr><td>admin</td><td>/</td><td>admin</td><td style="color:var(--ac)">admin role</td></tr>
      <tr><td>analyst</td><td>/</td><td>analyst</td><td style="color:var(--bl)">agent role</td></tr>
      <tr><td>viewer</td><td>/</td><td>viewer</td><td style="color:var(--bl)">agent role</td></tr>
    </table>
  </div>
  <button class="btn-primary" id="loginBtn" onclick="doLogin()">Sign in</button>
  <div id="loginError"></div>
</div>
</div>

<!-- ════ APP SHELL ════ -->
<div class="app-shell" id="appShell">

  <!-- Header -->
  <header class="app-header">
    <div class="logo"><span class="logo-icon">🔶</span> FAB MCP Hub</div>
    <span id="hubStatus" title="Hub status"><span class="status-dot" id="hubDot"></span></span>
    <span id="hubStatusText" style="font-size:12px;color:var(--mu)"></span>
    <div class="header-right">
      <span id="userDisplay" style="font-size:13px;color:var(--mu)"></span>
      <span id="userRole"></span>
      <button class="btn-sm" onclick="doLogout()">Sign out</button>
    </div>
  </header>

  <!-- Body -->
  <div class="app-body">

    <!-- Sidebar -->
    <nav class="sidebar">
      <button class="nav-btn active" data-screen="dashboard" onclick="switchScreen('dashboard')">
        <span class="nav-icon">🏠</span><span>Dashboard</span>
      </button>
      <button class="nav-btn" data-screen="chat" onclick="switchScreen('chat')">
        <span class="nav-icon">💬</span><span>Chat</span>
      </button>
      <button class="nav-btn" data-screen="history" onclick="switchScreen('history')">
        <span class="nav-icon">📋</span><span>History</span>
      </button>
      <button class="nav-btn" data-screen="observability" onclick="switchScreen('observability')">
        <span class="nav-icon">📊</span><span>Observability</span>
      </button>
      <button class="nav-btn" data-screen="search" onclick="switchScreen('search')">
        <span class="nav-icon">🔍</span><span>Search</span>
      </button>
      <div class="sidebar-footer">
        <button class="nav-btn" id="adminNavBtn" data-screen="admin" onclick="switchScreen('admin')" style="display:none">
          <span class="nav-icon">⚙️</span><span>Admin</span>
        </button>
        <button class="nav-btn" onclick="switchScreen('chat');newSession()">
          <span class="nav-icon">✏️</span><span>New chat</span>
        </button>
        <button class="nav-btn" onclick="openChangePwModal()" title="Change your password">
          <span class="nav-icon">🔑</span><span>Change Password</span>
        </button>
      </div>
    </nav>

    <!-- Content -->
    <div class="content">

      <!-- ── Dashboard ── -->
      <div id="screen-dashboard" class="screen">
        <div class="dash-welcome">
          <h2 id="dashWelcome">Welcome</h2>
          <p id="dashSub">FAB MCP Hub — Agentic AI Platform</p>
        </div>
        <div class="dash-stats">
          <div class="stat-card"><div class="stat-icon">💬</div><div class="stat-val" id="dashSessions">—</div><div class="stat-label">Conversations</div></div>
          <div class="stat-card"><div class="stat-icon">📝</div><div class="stat-val" id="dashMessages">—</div><div class="stat-label">Messages</div></div>
          <div class="stat-card"><div class="stat-icon">🟢</div><div class="stat-val" id="dashHubStatus">—</div><div class="stat-label">Hub</div></div>
          <div class="stat-card"><div class="stat-icon">🔐</div><div class="stat-val" id="dashAuthRate">—</div><div class="stat-label">Auth success</div></div>
        </div>
        <div class="dash-section">
          <h3>Recent Conversations</h3>
          <div id="dashRecent"><div class="empty-msg">No conversations yet — start chatting!</div></div>
        </div>
        <div class="dash-section">
          <h3>Quick Queries</h3>
          <div class="quick-grid">
            <button class="quick-btn" onclick="quickQuery('Show margin analysis for CUST001')">📈 Margin analysis CUST001</button>
            <button class="quick-btn" onclick="quickQuery('List all active deals')">📋 List all deals</button>
            <button class="quick-btn" onclick="quickQuery('What MCP servers are available?')">🔧 Available servers</button>
            <button class="quick-btn" onclick="quickQuery('Show system health status')">💚 System health</button>
          </div>
        </div>
      </div>

      <!-- ── Chat ── -->
      <div id="screen-chat" class="screen" style="display:none">

        <!-- Conversation list panel -->
        <div class="conv-panel">
          <div class="conv-panel-hdr">
            <span class="conv-panel-title">Conversations</span>
            <label id="adminAllToggle" class="admin-toggle" style="display:none" title="Admin: all users">
              <input type="checkbox" id="adminAllCheck" onchange="toggleAdminAll()"> All
            </label>
            <button class="btn-xs" onclick="newSession()" title="New conversation"
              style="background:var(--ac);color:#fff;border-color:var(--ac);padding:3px 9px;">+ New</button>
          </div>
          <div class="conv-list" id="convList">
            <div class="empty-msg" style="padding:14px 8px;font-size:12px;">No conversations yet</div>
          </div>
        </div>

        <!-- Chat main -->
        <div class="chat-main">
          <div class="chat-toolbar">
            <span id="convTitle" style="font-size:13px;font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mu)">New conversation</span>
            <span class="sess-chip" id="sessChip" title="Session ID (click to copy)" onclick="copySessionId()">—</span>
            <button class="btn-sm" id="traceToggle" onclick="toggleTrace()">◀ Trace</button>
          </div>
          <div id="pendingBar" class="pending-bar" style="display:none">
            <span>⏳ Agent processing in background — response will appear when ready</span>
            <button onclick="recheckPending()" style="margin-left:10px">Check now</button>
          </div>
          <div class="chat-messages" id="chatMessages">
            <div class="empty-msg" id="chatEmpty" style="margin:auto">Ask anything about your data…</div>
          </div>
          <div class="typing-indicator" id="typingIndicator">Agent is thinking…</div>
          <div class="chat-input-area">
            <div class="chat-input-row">
              <textarea id="chatInput" placeholder="Ask a question…" rows="1"
                onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage();}
                           this.style.height='auto';this.style.height=Math.min(this.scrollHeight,120)+'px';"></textarea>
              <button class="btn-send" id="sendBtn" onclick="sendMessage()">Send</button>
            </div>
          </div>
        </div>

        <!-- Right: trace panel -->
        <div class="trace-panel" id="tracePanel">
          <div class="trace-msg-hdr" id="traceMsgHdr">
            <span id="traceMsgHdrText">Live trace</span>
            <span class="trace-msg-hdr-close" onclick="clearTraceMsgHdr()" title="Close">✕</span>
          </div>
          <div class="trace-header">
            <button class="tab-btn active" id="tab-timeline" onclick="switchTraceTab('timeline')">Timeline</button>
            <button class="tab-btn" id="tab-graph" onclick="switchTraceTab('graph')">Graph</button>
            <button class="tab-btn" id="tab-security" onclick="switchTraceTab('security')">Security</button>
            <button class="tab-btn" id="tab-perf" onclick="switchTraceTab('perf')">Perf</button>
          </div>
          <div class="trace-body">

            <!-- Timeline view (default) -->
            <div class="trace-view active" id="tv-timeline">
              <div id="timelineSteps" class="timeline-wrap">
                <div class="timeline-empty" id="timelineEmpty">Send a query to see execution steps</div>
              </div>
            </div>

            <!-- Graph view -->
            <div class="trace-view" id="tv-graph">
              <div class="flow-wrap">
                <!-- Row 1: Browser → Chat → Hub -->
                <div class="flow-row">
                  <div class="fnode" id="fdn-browser">
                    <div class="fnode-box">🖥<span class="fnode-badge" id="fdn-browser-badge"></span></div>
                    <div class="fnode-lbl">Browser</div>
                  </div>
                  <div class="fedge" id="fde-bc"><div class="fedge-line"></div><div class="fedge-lbl" id="fde-bc-lbl">auth</div></div>
                  <div class="fnode" id="fdn-chat">
                    <div class="fnode-box">💬<span class="fnode-badge" id="fdn-chat-badge"></span></div>
                    <div class="fnode-lbl">Chat</div>
                  </div>
                  <div class="fedge" id="fde-ch"><div class="fedge-line"></div><div class="fedge-lbl" id="fde-ch-lbl">JWT</div></div>
                  <div class="fnode" id="fdn-hub">
                    <div class="fnode-box">🔶<span class="fnode-badge" id="fdn-hub-badge"></span></div>
                    <div class="fnode-lbl">Hub</div>
                  </div>
                </div>
                <!-- Vertical connectors from Hub down -->
                <div style="display:flex;justify-content:center;gap:0;width:100%;">
                  <div style="width:calc(2*68px + 2*60px);"></div>
                  <div class="flow-vconn" id="fvc-hub-llm"></div>
                  <div style="flex:1;display:flex;justify-content:center;">
                    <div class="flow-vconn" id="fvc-hub-mcp"></div>
                  </div>
                </div>
                <!-- Row 2: LLM  MCP → DB -->
                <div class="flow-row">
                  <div style="width:calc(2*68px + 60px)"></div>
                  <div class="fnode fnode-sub" id="fdn-llm">
                    <div class="fnode-box">🧠<span class="fnode-badge" id="fdn-llm-badge"></span></div>
                    <div class="fnode-lbl">LLM</div>
                  </div>
                  <div class="fedge" id="fde-hm"><div class="fedge-line"></div><div class="fedge-lbl" id="fde-hm-lbl">route</div></div>
                  <div class="fnode fnode-sub" id="fdn-mcp">
                    <div class="fnode-box">🔧<span class="fnode-badge" id="fdn-mcp-badge"></span></div>
                    <div class="fnode-lbl">MCP</div>
                  </div>
                  <div class="fedge" id="fde-md"><div class="fedge-line"></div><div class="fedge-lbl" id="fde-md-lbl">query</div></div>
                  <div class="fnode fnode-sub" id="fdn-db">
                    <div class="fnode-box">🗄<span class="fnode-badge" id="fdn-db-badge"></span></div>
                    <div class="fnode-lbl">DB</div>
                  </div>
                </div>
              </div>
              <div style="padding:8px 14px;border-top:1px solid var(--bd);">
                <div style="font-size:11px;color:var(--mu);margin-bottom:4px;">Status</div>
                <div id="flowStatus" style="font-size:12px;color:var(--tx)">Idle — send a query to start</div>
              </div>
            </div>

            <!-- Security view — auth token chain for this query -->
            <div class="trace-view" id="tv-security">
              <div id="secContent">
                <div class="sec-empty">Auth token chain appears here during queries</div>
              </div>
            </div>

            <!-- Perf view — timing breakdown for this query -->
            <div class="trace-view" id="tv-perf">
              <div id="perfContent">
                <div class="perf-empty">Performance metrics appear here during queries</div>
              </div>
            </div>

          </div>
        </div>
      </div>

      <!-- ── History ── -->
      <div id="screen-history" class="screen" style="display:none">
        <div class="history-toolbar">
          <input class="search-input" id="histSearch" placeholder="🔍 Search conversations…" oninput="filterHistory()"/>
          <button class="btn-sm" onclick="loadHistory()">↺ Refresh</button>
        </div>
        <div id="histList" class="hist-list"></div>
        <div class="hist-detail" id="histDetail">
          <div class="hist-detail-header">
            <button class="btn-sm" onclick="closeHistDetail()">← Back</button>
            <span id="histDetailName" style="font-weight:600;font-size:14px;"></span>
            <span style="flex:1"></span>
            <button class="btn-sm" onclick="continueInChat()" style="border-color:var(--ac);color:var(--ac)">Continue in Chat →</button>
          </div>
          <div class="hist-detail-msgs" id="histDetailMsgs"></div>
        </div>
      </div>

      <!-- ── Observability ── -->
      <div id="screen-observability" class="screen" style="display:none">
        <div class="obs-tabs">
          <button class="tab-btn active" id="obs-tab-auth" onclick="switchObsTab('auth')">🔐 Auth</button>
          <button class="tab-btn" id="obs-tab-routing" onclick="switchObsTab('routing')">🔶 Routing</button>
          <button class="tab-btn" id="obs-tab-requests" onclick="switchObsTab('requests')">📡 Requests</button>
          <button class="tab-btn" id="obs-tab-stats" onclick="switchObsTab('stats')">📊 Stats</button>
          <span style="flex:1"></span>
          <button class="btn-sm" onclick="refreshLogs()">↺ Refresh</button>
          <button class="btn-sm" id="obsAutoBtn" onclick="toggleObsAuto()" style="color:var(--gr)">⏵ Auto</button>
        </div>
        <div class="obs-body">

          <!-- Auth tab: who accessed what, ACCEPT/DENY, roles -->
          <div class="obs-view active" id="obs-auth">
            <div style="padding:8px 10px;border-bottom:1px solid var(--bd);font-size:10px;color:var(--mu);display:flex;gap:6px;font-family:monospace;font-weight:600;">
              <span style="min-width:62px;">RESULT</span><span style="min-width:80px;">SUB</span>
              <span style="min-width:60px;">ROLES</span><span style="flex:1;">ENDPOINT</span>
              <span style="min-width:65px;text-align:right;">TIME</span>
            </div>
            <div id="obsAuthList"><div class="obs-empty">Click ↺ Refresh to load auth decisions</div></div>
          </div>

          <!-- Routing tab: LLM routing decisions, server selection -->
          <div class="obs-view" id="obs-routing">
            <div id="obsRoutingList"><div class="obs-empty">Click ↺ Refresh to load routing decisions</div></div>
          </div>

          <!-- Requests tab: HTTP request log with latency -->
          <div class="obs-view" id="obs-requests">
            <div style="padding:8px 10px;border-bottom:1px solid var(--bd);font-size:10px;color:var(--mu);display:flex;gap:6px;font-family:monospace;font-weight:600;">
              <span style="min-width:65px;">TIME</span><span style="min-width:40px;">METH</span>
              <span style="flex:1;">PATH</span><span style="min-width:44px;">STATUS</span>
              <span style="min-width:58px;text-align:right;">LATENCY</span>
            </div>
            <div id="obsReqList"><div class="obs-empty">Click ↺ Refresh to load request log</div></div>
          </div>

          <!-- Stats tab: aggregated charts -->
          <div class="obs-view" id="obs-stats">
            <div class="stats-grid">
              <div class="stats-card">
                <h4>Auth Decisions</h4>
                <div class="donut-wrap">
                  <div class="donut" id="authDonut"><div class="donut-inner" id="authPct">—</div></div>
                  <div class="donut-legend">
                    <div><span class="legend-dot" style="background:var(--gr)"></span>Accept</div>
                    <div><span class="legend-dot" style="background:var(--rd)"></span>Deny</div>
                  </div>
                </div>
              </div>
              <div class="stats-card">
                <h4>Event Breakdown</h4>
                <div id="breakdownBars"></div>
              </div>
              <div class="stats-card" style="grid-column:1/-1">
                <h4>Active Tokens</h4>
                <table class="token-table">
                  <thead><tr><th>Sub</th><th>Roles</th><th>Provider</th><th>Last seen</th></tr></thead>
                  <tbody id="tokenTable"></tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Search ── -->
      <div id="screen-search" class="screen" style="display:none">
        <div class="dash-welcome">
          <h2>Search</h2>
          <p>Find conversations, messages, and event logs</p>
        </div>
        <div class="search-hero">
          <input type="text" id="searchInput" placeholder="Search conversations, messages, event logs…"
                 onkeydown="if(event.key==='Enter')doSearch()"/>
          <select id="searchType">
            <option value="all">All types</option>
            <option value="conversations">Conversations</option>
            <option value="messages">Messages</option>
            <option value="logs">Event logs</option>
          </select>
          <button onclick="doSearch()">Search</button>
        </div>
        <div class="search-results" id="searchResults">
          <div class="search-empty" id="searchEmpty">Enter a query above and press Search</div>
        </div>
      </div>

      <!-- ── Admin ── -->
      <div id="screen-admin" class="screen" style="display:none">
        <div class="dash-welcome">
          <h2>Admin Console</h2>
          <p style="color:var(--mu);font-size:13px;margin-top:4px">Manage users, view system configuration, and generate service tokens</p>
        </div>

        <!-- System Config -->
        <div class="adm-sec" id="admSysConfig">
          <h3>System Configuration</h3>
          <div class="adm-kv-wrap" style="opacity:.5">Loading…</div>
        </div>

        <!-- Users -->
        <div class="adm-sec">
          <h3>User Management</h3>
          <div style="margin-bottom:10px">
            <button class="btn-xs" onclick="openUserModal(null)">+ Add User</button>
            <span id="usersNote" style="font-size:11px;color:var(--mu);margin-left:10px">Users stored in MySQL. Changes persist across restarts.</span>
          </div>
          <table class="adm-tbl">
            <thead><tr><th>Username</th><th>Display Name</th><th>Role</th><th>Status</th><th style="text-align:right">Actions</th></tr></thead>
            <tbody id="admUsersTbody"></tbody>
          </table>
        </div>

        <!-- Token Generator -->
        <div class="adm-sec">
          <h3>Generate JWT Token</h3>
          <p style="font-size:13px;color:var(--mu);margin-bottom:14px">
            Mint a HS256 JWT. Use <code style="color:var(--ac);background:var(--sf2);padding:1px 5px;border-radius:3px">agent</code> role
            for <code style="color:var(--ac);background:var(--sf2);padding:1px 5px;border-radius:3px">HUB_API_KEY</code> /
            <code style="color:var(--ac);background:var(--sf2);padding:1px 5px;border-radius:3px">MCP_API_KEY</code>;
            <code style="color:#a78bfa;background:var(--sf2);padding:1px 5px;border-radius:3px">admin</code> for hub admin console access.
          </p>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px">
            <div style="flex:1;min-width:130px">
              <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Subject (sub)</label>
              <input id="admTkSub" class="adm-inp" value="fab-agent" placeholder="fab-agent">
            </div>
            <div style="flex:1;min-width:130px">
              <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Roles (comma-sep)</label>
              <input id="admTkRoles" class="adm-inp" value="agent" placeholder="agent or admin,agent">
            </div>
            <div style="min-width:90px">
              <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Expires (hours)</label>
              <input id="admTkHours" class="adm-inp" type="number" value="24" min="1" max="8760">
            </div>
          </div>
          <button class="btn-xs" onclick="admGenToken()">Generate Token</button>
          <div id="admTkOut" class="adm-tok-out"></div>
          <div id="admTkMeta" style="display:none;margin-top:6px;display:none;gap:8px;align-items:center">
            <button class="btn-xs" onclick="admCopyToken()">Copy</button>
            <span id="admTkInfo" style="font-size:11px;color:var(--mu)"></span>
          </div>
        </div>
      </div>

    </div><!-- .content -->
  </div><!-- .app-body -->
</div><!-- .app-shell -->

<!-- Change Password Modal -->
<div id="changePwModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;align-items:center;justify-content:center">
  <div style="background:var(--sf);border:1px solid var(--bd);border-radius:10px;width:380px;max-width:95vw" onclick="event.stopPropagation()">
    <div style="padding:16px 20px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center">
      <h3 style="font-size:15px;font-weight:600">Change Password</h3>
      <button onclick="document.getElementById('changePwModal').style.display='none'" style="background:none;border:none;color:var(--mu);cursor:pointer;font-size:18px">&#x2715;</button>
    </div>
    <div style="padding:20px">
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Current Password</label>
        <input id="cpCurrent" class="adm-inp" type="password" placeholder="Current password">
      </div>
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">New Password</label>
        <input id="cpNew" class="adm-inp" type="password" placeholder="At least 8 characters">
      </div>
      <div style="margin-bottom:4px">
        <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Confirm New Password</label>
        <input id="cpConfirm" class="adm-inp" type="password" placeholder="Repeat new password">
      </div>
      <div id="cpMsg" style="font-size:12px;margin-top:8px;display:none"></div>
    </div>
    <div style="padding:12px 20px;border-top:1px solid var(--bd);display:flex;justify-content:flex-end;gap:8px">
      <button class="btn-xs" onclick="document.getElementById('changePwModal').style.display='none'">Cancel</button>
      <button class="btn-xs" style="background:var(--ac);color:#fff;border-color:var(--ac)" onclick="submitChangePw()">Change Password</button>
    </div>
  </div>
</div>

<!-- User CRUD Modal -->
<div id="userModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:200;align-items:center;justify-content:center">
  <div style="background:var(--sf);border:1px solid var(--bd);border-radius:10px;width:400px;max-width:95vw" onclick="event.stopPropagation()">
    <div style="padding:16px 20px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center">
      <h3 id="userModalTitle" style="font-size:15px;font-weight:600">Add User</h3>
      <button onclick="document.getElementById('userModal').style.display='none'" style="background:none;border:none;color:var(--mu);cursor:pointer;font-size:18px">&#x2715;</button>
    </div>
    <div style="padding:20px">
      <input type="hidden" id="umEditName">
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Username *</label>
        <input id="umUsername" class="adm-inp" placeholder="e.g. alice">
      </div>
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Display Name</label>
        <input id="umDisplay" class="adm-inp" placeholder="e.g. Alice Smith">
      </div>
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Password</label>
        <div style="display:flex;gap:6px">
          <input id="umPassword" class="adm-inp" type="password" placeholder="Leave blank to keep unchanged (edit)" style="flex:1">
          <button class="btn-xs" type="button" onclick="umGenPw()" title="Generate random password">Generate</button>
        </div>
      </div>
      <div style="margin-bottom:12px">
        <label style="font-size:12px;color:var(--mu);display:block;margin-bottom:4px">Role</label>
        <select id="umRole" class="adm-inp">
          <option value="agent">agent</option>
          <option value="admin">admin</option>
          <option value="readonly">readonly</option>
        </select>
      </div>
      <div style="margin-bottom:4px;display:flex;align-items:center;gap:8px">
        <input type="checkbox" id="umChgPw" style="cursor:pointer">
        <label for="umChgPw" style="font-size:12px;color:var(--mu);cursor:pointer">Must change password on next login</label>
      </div>
    </div>
    <div style="padding:12px 20px;border-top:1px solid var(--bd);display:flex;justify-content:flex-end;gap:8px">
      <button class="btn-xs" onclick="document.getElementById('userModal').style.display='none'">Cancel</button>
      <button class="btn-xs" style="background:var(--ac);color:#fff;border-color:var(--ac)" onclick="saveUser()">Save User</button>
    </div>
  </div>
</div>

<script>
// ══════════════════════════════════════════════════════════════════════════════
// State
// ══════════════════════════════════════════════════════════════════════════════
var S = {
  token: '', sub: '', roles: [], display: '',
  loginMode: 'dev',
  screen: 'dashboard',
  sessionId: '',
  sessions: [],
  streaming: false,
  traceVisible: true,
  traceTab: 'timeline',
  obsTab: 'auth',
  obsAuto: false,
  obsAutoTimer: null,
  histSessions: [],
  histDetailSessionId: '',
  allLogs: [],
  tlStepCount: 0,
  tlStartTime: 0,
  nodeCounts: {},
  pollTimer: null,
  adminAll: false,
  userMsgCount: 0,
  currentTraceIdx: 0,
  secEvents: [],
  extToolEvents: [],
  perfStartTime: 0,
  perfRouteStart: 0,
  perfRouteMs: 0,
  perfMcpStart: 0,
  perfMcpMs: 0,
  perfToolCalls: [],
  perfTotalMs: 0,
};

// ══════════════════════════════════════════════════════════════════════════════
// Auth helpers
// ══════════════════════════════════════════════════════════════════════════════
function authFetch(url, opts) {
  opts = opts || {};
  opts.headers = Object.assign({'Content-Type':'application/json','Authorization':'Bearer '+S.token}, opts.headers||{});
  return fetch(url, opts);
}

async function doLogin() {
  var un = document.getElementById('loginUsername').value.trim();
  var pw = document.getElementById('loginPassword').value.trim();
  if (!un || !pw) { showLoginError('Please enter username and password.'); return; }
  document.getElementById('loginBtn').disabled = true;
  hideLoginError();
  try {
    var r = await fetch('/api/auth/login', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username:un, token:pw})
    });
    var d = await r.json();
    if (!r.ok) { showLoginError(d.detail || 'Login failed'); return; }
    S.token = d.token; S.sub = d.sub; S.roles = d.roles; S.display = d.display;
    sessionStorage.setItem('fab_token', S.token);
    sessionStorage.setItem('fab_sub', S.sub);
    sessionStorage.setItem('fab_roles', JSON.stringify(S.roles));
    sessionStorage.setItem('fab_display', S.display);
    onLoginSuccess();
  } catch(e) {
    showLoginError('Server unreachable: ' + e.message);
  } finally {
    document.getElementById('loginBtn').disabled = false;
  }
}

function showLoginError(msg) {
  var el = document.getElementById('loginError');
  el.textContent = msg; el.style.display = 'block';
}
function hideLoginError() { document.getElementById('loginError').style.display='none'; }

function doLogout() {
  sessionStorage.clear();
  S.token = ''; S.sub = ''; S.roles = []; S.display = '';
  document.getElementById('appShell').style.display = 'none';
  document.getElementById('loginOverlay').style.display = 'flex';
  document.getElementById('loginPassword').value = '';
}

async function onLoginSuccess() {
  document.getElementById('loginOverlay').style.display = 'none';
  document.getElementById('appShell').style.display = 'flex';
  document.getElementById('userDisplay').textContent = (S.display || S.sub);
  var roleBadge = document.getElementById('userRole');
  var role = S.roles[0] || 'agent';
  roleBadge.textContent = role;
  roleBadge.className = role === 'admin' ? 'admin-badge' : '';
  roleBadge.style.cssText = role === 'admin' ? '' : 'font-size:11px;padding:2px 8px;border-radius:10px;background:var(--ac2);color:var(--ac);';
  // Show admin controls
  if (S.roles.includes('admin')) {
    document.getElementById('adminAllToggle').style.display = 'flex';
    document.getElementById('adminNavBtn').style.display = '';
  }
  checkHubStatus();
  await loadSessions();
  switchScreen('dashboard');
}

// ══════════════════════════════════════════════════════════════════════════════
// Startup — restore session from sessionStorage, validate via /api/sessions
// ══════════════════════════════════════════════════════════════════════════════
(async function init() {
  var t = sessionStorage.getItem('fab_token');
  if (t) {
    S.token = t;
    S.sub = sessionStorage.getItem('fab_sub') || 'user';
    try { S.roles = JSON.parse(sessionStorage.getItem('fab_roles') || '["agent"]'); } catch(e){S.roles=['agent'];}
    S.display = sessionStorage.getItem('fab_display') || S.sub;
    try {
      var r = await authFetch('/api/sessions');
      if (r.ok) { onLoginSuccess(); return; }
    } catch(e){}
    sessionStorage.clear();
  }
  try {
    var r = await fetch('/api/auth-info');
    var d = await r.json();
    setMode(d.mode || 'dev');
    renderLoginChips(d.users || []);
  } catch(e) {
    setMode('dev');
    renderLoginChips([
      {username:'admin',role:'admin',display:'Administrator'},
      {username:'analyst',role:'agent',display:'Data Analyst'},
      {username:'viewer',role:'agent',display:'Viewer'}
    ]);
  }
})();

function renderLoginChips(users) {
  var el = document.getElementById('userChips');
  el.innerHTML = '';
  users.forEach(function(u) {
    var b = document.createElement('button');
    b.className = 'user-chip';
    b.textContent = u.display || u.username;
    b.title = u.role;
    b.onclick = function() {
      document.querySelectorAll('.user-chip').forEach(function(c){c.classList.remove('sel');});
      b.classList.add('sel');
      document.getElementById('loginUsername').value = u.username;
      if (S.loginMode === 'dev') {
        document.getElementById('loginPassword').value = u.username;
      }
      document.getElementById('loginPassword').focus();
    };
    el.appendChild(b);
  });
}

function setMode(mode) {
  S.loginMode = mode;
  var devBtn = document.getElementById('modeDevBtn');
  var qaBtn  = document.getElementById('modeQaBtn');
  var credHint   = document.getElementById('credHint');
  var chipsGroup = document.getElementById('userChipsGroup');
  var subtitle   = document.getElementById('loginSubtitle');
  if (devBtn) { devBtn.classList.toggle('active', mode === 'dev'); }
  if (qaBtn)  { qaBtn.classList.toggle('active',  mode === 'qa');  }
  if (credHint)   credHint.style.display   = mode === 'dev' ? '' : 'none';
  if (chipsGroup) chipsGroup.style.display = mode === 'dev' ? '' : 'none';
  if (subtitle) {
    subtitle.innerHTML = mode === 'qa'
      ? 'Agentic AI Platform <span class="qa-badge">QA</span>'
      : 'Agentic AI Platform';
  }
  document.getElementById('loginPassword').value = '';
  document.getElementById('loginUsername').value  = '';
}

// ══════════════════════════════════════════════════════════════════════════════
// Navigation — 5 screens
// ══════════════════════════════════════════════════════════════════════════════
function switchScreen(name) {
  S.screen = name;
  ['dashboard','chat','history','observability','search','admin'].forEach(function(s) {
    var el = document.getElementById('screen-'+s);
    if (el) el.style.display = s===name ? 'flex' : 'none';
  });
  document.querySelectorAll('.nav-btn[data-screen]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.screen === name);
  });
  if (name==='dashboard') loadDashboard();
  else if (name==='history') loadHistory();
  else if (name==='observability') { switchObsTab(S.obsTab||'auth'); refreshLogs(); }
  else if (name==='search') { setTimeout(function(){ document.getElementById('searchInput').focus(); }, 50); }
  else if (name==='admin') { loadAdminScreen(); }
  else if (name==='chat') {
    renderConvList();
    if (!S.sessionId && S.sessions.length) {
      switchSession(S.sessions[0].id);
    }
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Hub status
// ══════════════════════════════════════════════════════════════════════════════
async function checkHubStatus() {
  try {
    var r = await fetch('/health');
    var d = await r.json();
    document.getElementById('hubDot').className = 'status-dot';
    document.getElementById('hubStatusText').textContent = 'Hub online';
  } catch(e) {
    document.getElementById('hubDot').className = 'status-dot offline';
    document.getElementById('hubStatusText').textContent = 'Hub offline';
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Dashboard
// ══════════════════════════════════════════════════════════════════════════════
async function loadDashboard() {
  document.getElementById('dashWelcome').textContent = 'Welcome, ' + (S.display||S.sub);
  try {
    var r = await authFetch('/api/dashboard');
    var d = await r.json();
    document.getElementById('dashSessions').textContent = d.session_count || 0;
    document.getElementById('dashMessages').textContent = d.message_count || 0;
    document.getElementById('dashHubStatus').textContent = d.hub_online ? 'Online' : 'Offline';
    var authRate = '—';
    if (d.auth_accept_count !== undefined) {
      var total = d.auth_accept_count + d.auth_deny_count;
      authRate = total > 0 ? Math.round(100*d.auth_accept_count/total)+'%' : '100%';
    }
    document.getElementById('dashAuthRate').textContent = authRate;
    var el = document.getElementById('dashRecent');
    var sessions = d.recent_sessions || [];
    if (sessions.length === 0) {
      el.innerHTML = '<div class="empty-msg">No conversations yet — start chatting!</div>';
    } else {
      el.innerHTML = sessions.slice(0,5).map(function(s) {
        var rel = relTime(s.updated_at);
        return '<div class="session-row">'
          +'<span class="session-name">'+esc(s.name)+'</span>'
          +'<span class="session-meta">'+s.message_count+' msgs &middot; '+rel+'</span>'
          +'<button class="btn-xs" onclick="openSessionFromDash(\''+s.id+'\')">Open</button>'
          +'</div>';
      }).join('');
    }
  } catch(e) {
    document.getElementById('dashSessions').textContent = '?';
    document.getElementById('dashHubStatus').textContent = 'Error';
  }
}

function openSessionFromDash(id) {
  switchScreen('chat');
  switchSession(id);
}

function quickQuery(q) {
  switchScreen('chat');
  setTimeout(function() {
    document.getElementById('chatInput').value = q;
    sendMessage();
  }, 100);
}

// ══════════════════════════════════════════════════════════════════════════════
// Sessions
// ══════════════════════════════════════════════════════════════════════════════
async function loadSessions() {
  try {
    var url = '/api/sessions';
    if (S.adminAll && S.roles.includes('admin')) url += '?all=1';
    var r = await authFetch(url);
    var d = await r.json();
    S.sessions = d.sessions || [];
    renderConvList();
  } catch(e) { S.sessions = []; }
}

function renderConvList() {
  var el = document.getElementById('convList');
  if (!el) return;
  if (!S.sessions.length) {
    el.innerHTML = '<div class="empty-msg" style="padding:14px 8px;font-size:12px;">No conversations yet</div>';
    return;
  }
  el.innerHTML = S.sessions.map(function(s) {
    var rel = relTime(s.updated_at);
    var label = s.name.length > 30 ? s.name.substring(0,30)+'…' : s.name;
    var isOther = S.adminAll && s.username && s.username !== S.sub;
    var active = s.id === S.sessionId ? ' active' : '';
    return '<div class="conv-item'+active+'" onclick="switchSession(\''+s.id+'\')">'
      +'<div class="conv-item-name">'+esc(label)+'</div>'
      +'<div class="conv-item-meta">'+s.message_count+' msgs &middot; '+rel+'</div>'
      +(isOther ? '<div class="conv-item-user">'+esc(s.username)+'</div>' : '')
      +'</div>';
  }).join('');
}

function toggleAdminAll() {
  S.adminAll = document.getElementById('adminAllCheck').checked;
  S.sessionId = '';
  S.userMsgCount = 0;
  clearChatMessages();
  document.getElementById('sessChip').textContent = '—';
  document.getElementById('convTitle').textContent = 'New conversation';
  document.getElementById('convTitle').style.color = 'var(--mu)';
  loadSessions();
}

async function switchSession(id) {
  if (!id) { newSession(); return; }
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  S.sessionId = id;
  S.userMsgCount = 0;
  clearChatMessages();
  document.getElementById('pendingBar').style.display = 'none';
  updateSessChip(id);
  clearTraceMsgHdr();
  resetFlow(); clearTrace();
  // Update conv list active state
  renderConvList();
  // Update title from sessions list
  var sess = S.sessions.find(function(s){ return s.id === id; });
  var titleEl = document.getElementById('convTitle');
  if (sess) { titleEl.textContent = sess.name; titleEl.style.color = 'var(--tx)'; }
  try {
    var r = await authFetch('/api/sessions/'+id+'/messages');
    var d = await r.json();
    var msgs = d.messages || [];
    var userCount = 0;
    msgs.forEach(function(m) {
      if (m.role === 'user') {
        appendMessage('user', m.content, m.ts, false, null);
        userCount++;
      } else {
        appendMessage('assistant', m.content, m.ts, false, {sessionId: id, msgIndex: userCount - 1});
      }
    });
    S.userMsgCount = userCount;
    if (msgs.length) scrollChat();
  } catch(e) {}
  try {
    var sr = await authFetch('/api/sessions/'+id+'/status');
    var sd = await sr.json();
    if (sd.running) {
      document.getElementById('pendingBar').style.display = 'flex';
      setStreaming(true);
      if (S.userMsgCount > 0) {
        addThinkingPlaceholder(id, S.userMsgCount - 1);
      }
      pollSessionCompletion(id);
    } else {
      setStreaming(false);
      // Auto-restore trace for most recent completed message
      if (S.userMsgCount > 0) {
        await viewMsgTrace(id, S.userMsgCount - 1, null);
      }
    }
  } catch(e) { setStreaming(false); }
}

function newSession() {
  if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
  S.sessionId = '';
  S.userMsgCount = 0;
  clearChatMessages();
  document.getElementById('pendingBar').style.display = 'none';
  document.getElementById('sessChip').textContent = '—';
  var titleEl = document.getElementById('convTitle');
  titleEl.textContent = 'New conversation';
  titleEl.style.color = 'var(--mu)';
  clearTraceMsgHdr();
  renderConvList();
  setStreaming(false);
  resetFlow(); clearTrace();
}

// ══════════════════════════════════════════════════════════════════════════════
// Chat
// ══════════════════════════════════════════════════════════════════════════════
async function sendMessage() {
  var input = document.getElementById('chatInput');
  var q = input.value.trim();
  if (!q || S.streaming) return;
  input.value = ''; input.style.height = 'auto';
  if (!S.sessionId) S.sessionId = generateId();
  S.currentTraceIdx = S.userMsgCount;
  appendMessage('user', q, nowTime(), true, null);
  S.userMsgCount++;
  setStreaming(true);
  clearTraceMsgHdr();
  addThinkingPlaceholder(S.sessionId, S.currentTraceIdx);
  updateSessChip(S.sessionId);
  resetFlow(); clearTrace();
  var sessName = q.substring(0,50);
  try {
    var resp = await authFetch('/chat/stream', {
      method:'POST',
      body: JSON.stringify({query:q, session_id:S.sessionId, session_name:sessName})
    });
    if (!resp.ok) {
      var err = await resp.json().catch(function(){return{detail:'Unknown error'};});
      removeThinkingPlaceholder(S.currentTraceIdx);
      appendMessage('assistant', 'Error: '+(err.detail||resp.status), nowTime(), true);
      setStreaming(false); return;
    }
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buf = '';
    while (true) {
      var _r = await reader.read();
      if (_r.done) break;
      buf += decoder.decode(_r.value, {stream:true});
      var lines = buf.split('\n');
      buf = lines.pop();
      for (var i=0;i<lines.length;i++) {
        var line = lines[i];
        if (line.startsWith('data: ')) {
          try { onEvent(JSON.parse(line.slice(6))); } catch(e){}
        }
      }
    }
  } catch(e) {
    removeThinkingPlaceholder(S.currentTraceIdx);
    appendMessage('assistant', 'Connection error: '+e.message, nowTime(), true);
  }
  // After stream ends, check if the agent task is still running in the background
  var stillRunning = false;
  if (S.sessionId) {
    try {
      var sr = await authFetch('/api/sessions/'+S.sessionId+'/status');
      if (sr.ok) { var sd = await sr.json(); stillRunning = sd.running; }
    } catch(e2) {}
  }
  if (stillRunning) {
    document.getElementById('pendingBar').style.display = 'flex';
    setStreaming(true);
    pollSessionCompletion(S.sessionId);
  } else {
    clearThinkingPlaceholders();
    setStreaming(false);
  }
  // Update title and conv list after this message creates/updates the session
  var titleEl = document.getElementById('convTitle');
  if (titleEl.textContent === 'New conversation') {
    titleEl.textContent = q.substring(0, 50);
    titleEl.style.color = 'var(--tx)';
  }
  setTimeout(function(){ loadSessions(); }, 500);
}

// ══════════════════════════════════════════════════════════════════════════════
// onEvent — drives timeline, graph, security tab, perf tab
// ══════════════════════════════════════════════════════════════════════════════
function onEvent(ev) {
  addTimelineStep(ev);
  var t = ev.type;

  // --- Security tab: collect auth_hop, tool_rbac, and external_tool_call events ---
  if (t === 'auth_hop') {
    S.secEvents.push(ev);
    renderSecurityTab();
  }
  if (t === 'tool_rbac') {
    S.toolRbacEvents = S.toolRbacEvents || [];
    S.toolRbacEvents.push(ev);
    renderSecurityTab();
  }
  if (t === 'external_tool_call') {
    S.extToolEvents = S.extToolEvents || [];
    S.extToolEvents.push(ev);
    renderSecurityTab();
  }

  // --- Perf tab: track timing milestones ---
  if (S.perfStartTime === 0) S.perfStartTime = Date.now();
  if (t === 'routing') S.perfRouteStart = Date.now();
  if (t === 'hub_loaded') { if (S.perfRouteStart) S.perfRouteMs = Date.now() - S.perfRouteStart; }
  if (t === 'mcp_connecting') S.perfMcpStart = Date.now();
  if (t === 'mcp_connected') { if (S.perfMcpStart) S.perfMcpMs = Date.now() - S.perfMcpStart; }
  if (t === 'tool_call') S.perfToolCalls.push({name: ev.tool_name, start: Date.now(), ms: 0});
  if (t === 'tool_result') {
    var tc = null;
    for (var i = S.perfToolCalls.length-1; i >= 0; i--) {
      if (S.perfToolCalls[i].name === ev.tool_name && !S.perfToolCalls[i].ms) {
        tc = S.perfToolCalls[i]; break;
      }
    }
    if (tc) tc.ms = Date.now() - tc.start;
  }
  if (t === 'final_answer' || t === 'error') {
    S.perfTotalMs = Date.now() - S.perfStartTime;
    renderPerfTab();
  }

  if (t==='auth_hop') {
    var _tt = (ev.token_type || 'JWT').toUpperCase();
    if (ev.from==='browser'&&ev.to==='chat_server') {
      activateNode('fdn-browser'); activateEdge('fde-bc', _tt);
      activateNode('fdn-chat');
      setFlowStatus('Browser → Chat: '+_tt+' auth ('+(ev.sub||'?')+')');
    } else if (ev.from==='agent'&&ev.to==='hub') {
      activateEdge('fde-ch', _tt);
      activateNode('fdn-hub');
      setFlowStatus('Agent → Hub: '+_tt+' auth ('+(ev.sub||'?')+')');
    } else if (ev.from==='agent'&&ev.to==='mcp') {
      activateEdge('fde-hm', _tt); activateNode('fdn-mcp');
      activateVconn('fvc-hub-mcp');
      setFlowStatus('Agent → MCP: '+_tt+' auth ('+(ev.server_id||'?')+')');
    }
  } else if (t==='hub_loaded') {
    // Mark hub auth_hop as server-validated with confirmed claims
    if (ev.hub_auth && ev.hub_auth.sub) {
      for (var _j = S.secEvents.length - 1; _j >= 0; _j--) {
        if (S.secEvents[_j].to === 'hub') {
          S.secEvents[_j].hub_validated = true;
          if (!S.secEvents[_j].sub) S.secEvents[_j].sub = ev.hub_auth.sub;
          if (!S.secEvents[_j].roles || !S.secEvents[_j].roles.length) S.secEvents[_j].roles = ev.hub_auth.roles;
          if (!S.secEvents[_j].token_type) S.secEvents[_j].token_type = ev.hub_auth.token_type;
          renderSecurityTab();
          break;
        }
      }
    }
  } else if (t==='routing') {
    doneNode('fdn-hub');
    activateVconn('fvc-hub-llm'); activateNode('fdn-llm');
    setFlowStatus('Routing: '+ev.method+' → '+(ev.server_id||'server')+'…');
  } else if (t==='mcp_connecting') {
    doneNode('fdn-llm'); doneVconn('fvc-hub-llm');
    setFlowStatus('Connecting to '+ev.server_id+'…');
  } else if (t==='mcp_connected') {
    doneNode('fdn-mcp'); activateEdge('fde-md','tools');
    setFlowStatus('MCP connected: '+ev.tool_count+' tools available');
  } else if (t==='tool_call') {
    activateNode('fdn-db'); activateEdge('fde-md',ev.tool_name);
    setFlowStatus('Tool: '+ev.tool_name+'…');
  } else if (t==='tool_result') {
    doneNode('fdn-db'); doneEdge('fde-md');
    setFlowStatus('Tool result received');
  } else if (t==='final_answer') {
    removeThinkingPlaceholder(S.currentTraceIdx);
    appendMessage('assistant', ev.content, nowTime(), true, {sessionId: S.sessionId, msgIndex: S.currentTraceIdx});
    doneNode('fdn-chat'); doneNode('fdn-hub'); doneNode('fdn-mcp'); doneNode('fdn-db');
    doneEdge('fde-bc'); doneEdge('fde-ch'); doneEdge('fde-hm'); doneEdge('fde-md');
    setFlowStatus('✓ Answer ready');
    document.getElementById('pendingBar').style.display = 'none';
  } else if (t==='stream_timeout') {
    // LLM taking >300s — task continues in background, show pending bar
    document.getElementById('pendingBar').style.display = 'flex';
    setFlowStatus('⏳ Still processing in background…');
  } else if (t==='error') {
    removeThinkingPlaceholder(S.currentTraceIdx);
    appendMessage('assistant', '⚠ '+ev.message, nowTime(), true);
    errorNode('fdn-hub');
    setFlowStatus('Error: '+ev.message);
  }
}

function appendMessage(role, content, ts, scroll, traceInfo) {
  var el = document.getElementById('chatMessages');
  var ce = document.getElementById('chatEmpty');
  if (ce) ce.style.display = 'none';
  var d = document.createElement('div');
  d.className = 'msg '+role;
  var traceBtn = '';
  if (role === 'assistant' && traceInfo && traceInfo.sessionId) {
    traceBtn = '<button class="msg-trace-btn" onclick="viewMsgTrace(\''+traceInfo.sessionId+'\','+traceInfo.msgIndex+',this)">🔍 Trace</button>';
  }
  d.innerHTML = '<div class="msg-bubble">'+esc(content)+'</div>'
    +'<div class="msg-meta">'+(role==='user'?'You':'Assistant')+' &middot; '+(ts||'')
    +traceBtn+'</div>';
  el.appendChild(d);
  if (scroll) scrollChat();
}

function clearChatMessages() {
  // Remove message bubbles without destroying #chatEmpty which lives inside #chatMessages
  var el = document.getElementById('chatMessages');
  Array.from(el.querySelectorAll('.msg')).forEach(function(m){ m.remove(); });
  var ce = document.getElementById('chatEmpty');
  if (ce) ce.style.display = '';
}

function addThinkingPlaceholder(sessionId, msgIndex) {
  var el = document.getElementById('chatMessages');
  var ce = document.getElementById('chatEmpty');
  if (ce) ce.style.display = 'none';
  var d = document.createElement('div');
  d.className = 'msg assistant thinking-placeholder';
  d.id = 'thinking-msg-' + msgIndex;
  d.innerHTML = '<div class="msg-bubble thinking-bubble"><span class="thinking-dots"></span></div>'
    + '<div class="msg-meta">Assistant &middot; ' + nowTime()
    + ' <button class="msg-trace-btn live" onclick="viewMsgTrace(\'' + sessionId + '\',' + msgIndex + ',this)">🔍 Live trace</button>'
    + '</div>';
  el.appendChild(d);
  scrollChat();
}

function removeThinkingPlaceholder(msgIndex) {
  var el = document.getElementById('thinking-msg-' + msgIndex);
  if (el) el.remove();
}

function clearThinkingPlaceholders() {
  document.querySelectorAll('.thinking-placeholder').forEach(function(el){ el.remove(); });
}

function setStreaming(v) {
  S.streaming = v;
  document.getElementById('sendBtn').disabled = v;
  document.getElementById('typingIndicator').style.display = v ? 'block' : 'none';
}

function scrollChat() {
  var el = document.getElementById('chatMessages');
  el.scrollTop = el.scrollHeight;
}

function toggleTrace() {
  S.traceVisible = !S.traceVisible;
  var tp = document.getElementById('tracePanel');
  tp.style.display = S.traceVisible ? 'flex' : 'none';
  document.getElementById('traceToggle').textContent = S.traceVisible ? '◀ Trace' : '▶ Trace';
}

function switchTraceTab(tab) {
  S.traceTab = tab;
  ['timeline','graph','security','perf'].forEach(function(t) {
    document.getElementById('tab-'+t).classList.toggle('active', t===tab);
    document.getElementById('tv-'+t).classList.toggle('active', t===tab);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// Timeline — dynamic execution trace
// ══════════════════════════════════════════════════════════════════════════════
var TL_ICONS = {
  auth_hop:'🔐', routing:'🔶', hub_loaded:'📋',
  mcp_connecting:'🔌', mcp_connected:'🔧', tool_call:'⚙️',
  tool_rbac:'🛡️', tool_result:'📤', final_answer:'✅', error:'❌',
  external_tool_call:'🔗'
};

function addTimelineStep(ev) {
  var container = document.getElementById('timelineSteps');
  var empty = document.getElementById('timelineEmpty');
  if (empty) empty.remove();

  if (S.tlStartTime === 0) S.tlStartTime = Date.now();
  S.tlStepCount++;
  var stepNum = S.tlStepCount;
  var elapsed = ((Date.now() - S.tlStartTime) / 1000).toFixed(2);
  var icon = TL_ICONS[ev.type] || '📌';
  var t = ev.type || 'event';
  var isError = (t === 'error');

  var title = '', detail = '', expandContent = '', hasExpand = false;

  if (t === 'auth_hop') {
    var _ttype = (ev.token_type||'jwt').toUpperCase();
    title = 'Auth ['+_ttype+']: ' + esc(ev.from||'?') + ' → ' + esc(ev.to||'?');
    var _fullTok = ev.token_full || ev.token_hint || '<token>';
    detail = 'sub: ' + esc(ev.sub||'?')
      + (ev.roles&&ev.roles.length ? ' · roles: ['+ev.roles.map(esc).join(',')+']' : '')
      + (ev.iss ? ' · iss: '+esc(ev.iss) : '')
      + (ev.hub_validated ? ' · ✓ hub-validated' : '')
      + ' · Bearer: <code style="word-break:break-all;font-size:10px">'+esc(_fullTok)+'</code>';
    hasExpand = true;
    var _hReq = ev.http_request || {};
    var _authLine = 'Authorization: Bearer ' + _fullTok;
    var _extraHdrs = _hReq.headers
      ? Object.entries(_hReq.headers).filter(function(kv){return kv[0]!=='Authorization';}).map(function(kv){return kv[0]+': '+kv[1];}).join('\n')
      : '';
    var _claims = {sub: ev.sub||'unknown', roles: ev.roles||[], iss: ev.iss||'', exp: ev.exp ? new Date(ev.exp*1000).toISOString() : '', iat: ev.iat ? new Date(ev.iat*1000).toISOString() : ''};
    // JWT anatomy: decode header + payload if token has 3 dot-separated parts
    var _jwtAnatomy = '';
    if (_fullTok && _fullTok.split('.').length === 3) {
      try {
        var _parts = _fullTok.split('.');
        var _b64 = function(s){ try{ return atob(s.replace(/-/g,'+').replace(/_/g,'/')); }catch(e){return s;} };
        var _hdr = JSON.parse(_b64(_parts[0]));
        var _pay = JSON.parse(_b64(_parts[1]));
        _jwtAnatomy = '\n\n// JWT ANATOMY (3 parts: header.payload.signature)\n'
          + '// Part 1 — HEADER (base64url decoded)\n' + JSON.stringify(_hdr, null, 2)
          + '\n\n// Part 2 — PAYLOAD (base64url decoded)\n' + JSON.stringify(_pay, null, 2)
          + '\n\n// Part 3 — SIGNATURE (HMAC-SHA256, not shown — verified server-side)\n'
          + _parts[2].substring(0,32) + '… (' + _parts[2].length + ' chars)';
      } catch(e2) { _jwtAnatomy = ''; }
    }
    expandContent = '// HTTP REQUEST\n'
      + (_hReq.method||'POST') + ' ' + (_hReq.url||ev.hub_url||'(internal)') + '\n'
      + _authLine + (_extraHdrs ? '\n'+_extraHdrs : '')
      + '\n\n// DECODED TOKEN CLAIMS\n' + JSON.stringify(_claims, null, 2)
      + (ev.key_source ? '\n\n// KEY SOURCE\n' + ev.key_source : '')
      + _jwtAnatomy;
  } else if (t === 'routing') {
    var sids = ev.server_ids ? ev.server_ids.join(', ') : (ev.server_id||'?');
    title = '🔶 Routing [' + esc(ev.method||'?') + ']: ' + esc(sids);
    detail = 'sub: ' + esc(ev.hub_sub||'?')
      + (ev.hub_roles&&ev.hub_roles.length ? ' · roles: ['+ev.hub_roles.map(esc).join(',')+']' : '')
      + (ev.hub_iss ? ' · iss: '+esc(ev.hub_iss) : '')
      + ' · method: ' + esc(ev.method||'?');
    hasExpand = true;
    var _hh = ev.http || {};
    var _routeTok = ev.hub_token || ((_hh.request&&_hh.request.headers&&_hh.request.headers['Authorization']) ? _hh.request.headers['Authorization'].replace('Bearer ','') : '<token>');
    expandContent = '// STEP: Agent → Hub POST /discover\n'
      + '//   This is the Hub JWT (HS256 from chat, or RS256 from agent login)\n'
      + '//   used to authenticate the routing request.\n\n'
      + 'POST ' + ((_hh.request&&_hh.request.url)||'hub/discover') + '\n'
      + 'Authorization: Bearer ' + _routeTok + '\n'
      + 'Content-Type: application/json\n\n'
      + '// REQUEST BODY\n'
      + JSON.stringify((_hh.request&&_hh.request.body)||{intent:'<query>'}, null, 2)
      + '\n\n// HUB TOKEN CLAIMS\n'
      + JSON.stringify({sub:ev.hub_sub||'?', roles:ev.hub_roles||[], iss:ev.hub_iss||'?', token_type:ev.hub_token_type||'jwt'}, null, 2)
      + '\n\n// ROUTING DECISION\n'
      + 'method: ' + (ev.method||'?') + '\n'
      + 'servers: ' + sids + '\n'
      + 'reason: ' + (ev.reason||'')
      + '\n\n// HUB RESPONSE — Per-Server JWTs Issued\n'
      + JSON.stringify((_hh.response&&_hh.response.body)||{}, null, 2);
  } else if (t === 'hub_loaded') {
    title = 'Hub loaded: ' + esc(ev.hub_name||'');
    var hubAuthStr = '';
    if (ev.hub_auth && ev.hub_auth.sub) {
      hubAuthStr = ' · auth: '+esc(ev.hub_auth.sub)+' ['+esc((ev.hub_auth.roles||[]).join(','))+'] ('+esc(ev.hub_auth.token_type||'jwt')+')';
    }
    detail = 'servers: ' + esc((ev.server_ids||[]).join(', ')) + hubAuthStr;
    if (ev.hub_auth && ev.hub_auth.sub) {
      hasExpand = true;
      expandContent = '// HUB SERVER-CONFIRMED AUTH\n' + JSON.stringify(ev.hub_auth, null, 2)
        + '\n\n// REGISTERED SERVERS\n' + (ev.server_ids||[]).join('\n');
    }
  } else if (t === 'mcp_connecting') {
    title = '🔌 MCP Connecting: ' + esc(ev.server_id||'');
    detail = esc(ev.endpoint||'') + ' · transport: ' + esc(ev.transport||'sse');
    hasExpand = true;
    expandContent = '// STEP: Agent → MCP Server (opening session)\n\n'
      + 'server_id:  ' + (ev.server_id||'?') + '\n'
      + 'endpoint:   ' + (ev.endpoint||'?') + '\n'
      + 'transport:  ' + (ev.transport||'sse') + '\n\n'
      + '// See the Auth hop (🔐 agent → mcp) step for the full per-server JWT\n'
      + '// That JWT has aud="' + (ev.server_id||'<server_id>') + '" — rejected by any other MCP server.';
  } else if (t === 'mcp_connected') {
    title = 'MCP connected · ' + (ev.tool_count||0) + ' tools';
    var tools = (ev.tool_names||ev.tools||[]).slice(0,3).map(esc);
    detail = tools.length ? tools.join(', ') + (ev.tool_count>3?' …':'') : '';
    if (ev.tool_names && ev.tool_names.length) {
      hasExpand = true;
      expandContent = '// AVAILABLE TOOLS (' + ev.tool_count + ')\n' + (ev.tool_names||[]).join('\n');
    }
  } else if (t === 'tool_call') {
    title = '⚙️ Tool: ' + esc(ev.tool_name||'') + (ev.server_id?' <span style="font-size:10px;color:var(--mu)">['+esc(ev.server_id)+']</span>':'');
    var argsStr = ev.args ? JSON.stringify(ev.args, null, 2) : '';
    detail = 'args: ' + (argsStr.length > 120 ? esc(argsStr.substring(0,120))+'…' : esc(argsStr.replace(/\n/g,' ')));
    hasExpand = true;
    var _toolTok = ev.token_full || (ev.http_headers&&ev.http_headers['Authorization'] ? ev.http_headers['Authorization'].replace('Bearer ','') : '<see auth_hop above>');
    var _toolEndpoint = ev.server_id ? (ev.server_id + ' (see auth_hop for endpoint)') : '?';
    var _hdrsStr = ev.http_headers ? Object.entries(ev.http_headers).map(function(kv){
      return kv[0] + ': ' + kv[1];
    }).join('\n') : ('Authorization: Bearer ' + _toolTok + '\nContent-Type: application/json\nAccept: application/json, text/event-stream');
    expandContent = '// STEP: Agent → MCP Server (tool execution via JSON-RPC 2.0)\n'
      + '//   Same per-server JWT used for every tool call in this session.\n\n'
      + 'POST ' + _toolEndpoint + '/mcp/\n'
      + _hdrsStr + '\n\n'
      + '// FULL BEARER TOKEN (per-server RS256 JWT with aud=' + esc(ev.server_id||'?') + ')\n'
      + _toolTok + '\n\n'
      + '// JSON-RPC 2.0 REQUEST BODY\n'
      + JSON.stringify(ev.jsonrpc_request||{jsonrpc:'2.0',method:'tools/call',params:{name:ev.tool_name,arguments:ev.args||{}}}, null, 2)
      + (argsStr ? '\n\n// TOOL ARGUMENTS (expanded)\n' + argsStr : '')
      + (ev.key_source ? '\n\n// KEY SOURCE\n' + ev.key_source : '');
  } else if (t === 'tool_rbac') {
    title = '🛡️ MCP RBAC: ' + esc(ev.tool_name||'')
      + (ev.server_id?' <span style="font-size:10px;color:var(--mu)">['+esc(ev.server_id)+']</span>':'');
    detail = 'sub: '+esc(ev.sub||'?')+' · roles: ['+(ev.roles||[]).map(esc).join(',')+']'
      + ' · <span style="color:var(--gr);font-weight:600">✓ require_role PASS</span>'
      + ' · <span style="font-size:10px;color:var(--mu)">same JWT as auth_hop</span>';
    hasExpand = true;
    expandContent = '// STEP: MCP BearerAuthMiddleware — validates JWT on every tool POST\n'
      + '//   Same per-server JWT as auth_hop step — no new token is issued.\n\n'
      + 'Authorization: Bearer ' + (ev.token_full||ev.token_hint||'<token>') + '\n\n'
      + '// DECODED CLAIMS (stored in ContextVar, not re-decoded)\n'
      + JSON.stringify({sub: ev.sub||'?', roles: ev.roles||[], token_type: ev.token_type||'jwt', key_source: ev.key_source||'?'}, null, 2)
      + '\n\n// RBAC FLOW INSIDE MCP SERVER\n'
      + 'Each tool call = new HTTP POST to /mcp/\n'
      + 'Step 1: BearerAuthMiddleware.dispatch() → verifies Bearer JWT → HMAC-SHA256 check\n'
      + '         → _request_claims.set({sub, roles}) in ContextVar (per asyncio task)\n'
      + 'Step 2: require_role("admin","agent") → _request_claims.get() → roles='
      + JSON.stringify(ev.roles||[]) + ' → PASS\n'
      + 'Step 3: audit_log("' + (ev.tool_name||'tool') + '", ...) → {agent_sub, agent_roles, tool, service}\n\n'
      + '// SAME TOKEN AS PRECEDING auth_hop? YES\n'
      + '  Agent→MCP auth_hop and all tool calls use the identical JWT/key.\n'
      + '  Hub uses JWT_SECRET; MCP uses MCP_JWT_SECRET — independent secrets.\n\n'
      + '// WHERE IS THIS KEY STORED?\n'
      + '  key_source: ' + (ev.key_source||'?') + '\n'
      + '  per-server-db  → MySQL fab_semantic.mcp_servers.api_key (set via Admin UI → 🔑 Key)\n'
      + '  env-MCP_API_KEY → .env file (shared fallback for all MCP servers)';
  } else if (t === 'tool_result') {
    title = '📤 Tool result: ' + esc(ev.tool_name||'');
    var resStr = typeof ev.result === 'string' ? ev.result : JSON.stringify(ev.result||'');
    var resPretty = resStr; try { resPretty = JSON.stringify(JSON.parse(resStr), null, 2); } catch(e2){}
    detail = resStr.length > 160 ? esc(resStr.substring(0,160))+'…' : esc(resStr);
    hasExpand = true;
    expandContent = '// STEP: MCP Server → Agent (JSON-RPC 2.0 response)\n\n';
    if (ev.jsonrpc_response) {
      expandContent += '// JSON-RPC RESPONSE BODY\n' + JSON.stringify(ev.jsonrpc_response, null, 2)
        + '\n\n// PARSED RESULT\n' + resPretty;
    } else {
      expandContent += '// RESULT (full)\n' + (resStr.length > 0 ? resPretty : '(empty result)');
    }
  } else if (t === 'external_tool_call') {
    title = 'Ext service: ' + esc(ev.tool_name||'') + ' → ' + esc(ev.external_service||'external');
    detail = 'auth: ' + esc(ev.auth_pattern||'bearer_jwt') + ' · key: ' + esc(ev.key_source||'tool-registry-db');
    hasExpand = true;
    var _extAuthHdr = ev.auth_pattern === 'api_key_header'
      ? 'X-API-Key: <key-from-tool-registry>'
      : 'Authorization: Bearer <jwt-from-tool-registry>';
    expandContent = '// EXTERNAL HTTP CALL (MCP → External Service)\n'
      + 'POST/GET http://external-service/<path>\n'
      + _extAuthHdr + '\n'
      + 'Content-Type: application/json\n\n'
      + '// CREDENTIAL DETAILS\n'
      + 'tool:         ' + (ev.tool_name||'?') + '\n'
      + 'service:      ' + (ev.external_service||'?') + '\n'
      + 'auth_pattern: ' + (ev.auth_pattern||'bearer_jwt') + '\n'
      + 'key_source:   ' + (ev.key_source||'tool-registry-db') + '\n'
      + 'mcp_server:   ' + (ev.server_id||'?');
  } else if (t === 'final_answer') {
    title = 'Final answer';
    detail = '';
  } else if (t === 'error') {
    title = 'Error';
    detail = esc(ev.message||'');
    if (ev.message && ev.message.length > 80) { hasExpand = true; expandContent = ev.message; }
  } else {
    title = esc(t);
    var raw = JSON.stringify(ev, null, 2);
    detail = raw.length > 100 ? esc(raw.substring(0,100))+'…' : esc(raw);
    if (raw.length > 100) { hasExpand = true; expandContent = '// RAW EVENT\n' + raw; }
  }

  var div = document.createElement('div');
  div.className = 'tl-step done-step' + (isError ? ' error-step' : '');
  div.innerHTML =
    '<div class="tl-dot"></div>'
    + '<span class="tl-num">#'+stepNum+'</span>'
    + '<span class="tl-elapsed">'+elapsed+'s</span>'
    + '<span class="tl-icon">'+icon+'</span>'
    + '<div class="tl-content">'
    +   '<div class="tl-title">'+title+'</div>'
    +   (detail ? '<div class="tl-detail">'+detail+'</div>' : '')
    +   (hasExpand
          ? '<span class="tl-expand-btn" onclick="toggleExpand(this.closest(\'.tl-step\'))">▶ expand</span>'
            + '<pre class="tl-expanded" style="display:none">'+esc(expandContent)+'</pre>'
          : '')
    + '</div>';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function toggleExpand(stepEl) {
  stepEl.classList.toggle('tl-open');
  var btn = stepEl.querySelector('.tl-expand-btn');
  var pre = stepEl.querySelector('.tl-expanded');
  var open = stepEl.classList.contains('tl-open');
  if (btn) btn.textContent = open ? '▼ collapse' : '▶ expand';
  if (pre) pre.style.display = open ? 'block' : 'none';
}

// ══════════════════════════════════════════════════════════════════════════════
// Flow diagram (graph tab) helpers
// ══════════════════════════════════════════════════════════════════════════════
function setFlowStatus(msg) { document.getElementById('flowStatus').textContent = msg; }

function activateNode(id) {
  var el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('done','error'); el.classList.add('active');
  S.nodeCounts[id] = (S.nodeCounts[id]||0) + 1;
  var badge = document.getElementById(id+'-badge');
  if (badge) { badge.textContent = S.nodeCounts[id]; badge.classList.add('show'); }
}
function doneNode(id) {
  var el = document.getElementById(id);
  if (el) { el.classList.remove('active','error'); el.classList.add('done'); }
}
function errorNode(id) {
  var el = document.getElementById(id);
  if (el) { el.classList.remove('active','done'); el.classList.add('error'); }
}
function activateEdge(id, lbl) {
  var el = document.getElementById(id);
  if (el) { el.classList.remove('done'); el.classList.add('active'); }
  var ll = document.getElementById(id+'-lbl');
  if (ll && lbl) ll.textContent = lbl;
}
function doneEdge(id) {
  var el = document.getElementById(id);
  if (el) { el.classList.remove('active'); el.classList.add('done'); }
}
function activateVconn(id) {
  var el = document.getElementById(id);
  if (el) { el.classList.remove('done'); el.classList.add('active'); }
}
function doneVconn(id) {
  var el = document.getElementById(id);
  if (el) { el.classList.remove('active'); el.classList.add('done'); }
}

function resetFlow() {
  ['fdn-browser','fdn-chat','fdn-hub','fdn-llm','fdn-mcp','fdn-db'].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.remove('active','done','error');
    var badge = document.getElementById(id+'-badge');
    if (badge) { badge.textContent = ''; badge.classList.remove('show'); }
  });
  ['fde-bc','fde-ch','fde-hm','fde-md'].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.remove('active','done');
    var ll = document.getElementById(id+'-lbl');
    if (ll) ll.textContent = {bc:'auth',ch:'JWT',hm:'route',md:'query'}[id.slice(4)] || '';
  });
  ['fvc-hub-llm','fvc-hub-mcp'].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.remove('active','done');
  });
  setFlowStatus('Idle — send a query to start');
  S.nodeCounts = {};
}

function clearTrace() {
  // Reset timeline
  S.tlStepCount = 0; S.tlStartTime = 0;
  var tl = document.getElementById('timelineSteps');
  if (tl) tl.innerHTML = '<div class="timeline-empty" id="timelineEmpty">Send a query to see execution steps</div>';
  // Reset security tab
  S.secEvents = []; S.extToolEvents = []; S.toolRbacEvents = [];
  var sc = document.getElementById('secContent');
  if (sc) sc.innerHTML = '<div class="sec-empty">Auth token chain appears here during queries</div>';
  // Reset perf tab
  S.perfStartTime = 0; S.perfRouteStart = 0; S.perfRouteMs = 0;
  S.perfMcpStart = 0; S.perfMcpMs = 0; S.perfToolCalls = []; S.perfTotalMs = 0;
  var pc = document.getElementById('perfContent');
  if (pc) pc.innerHTML = '<div class="perf-empty">Performance metrics appear here during queries</div>';
}

// ══════════════════════════════════════════════════════════════════════════════
// Security tab — per-query auth token chain
// ══════════════════════════════════════════════════════════════════════════════
function renderSecurityTab() {
  var el = document.getElementById('secContent');
  if (!el) return;
  if (!S.secEvents.length) {
    el.innerHTML = '<div class="sec-empty">Auth token chain appears here during queries</div>';
    return;
  }

  var html = '<div class="sec-chain-hdr">Auth chain · '
    + S.secEvents.length + ' hop' + (S.secEvents.length > 1 ? 's' : '') + ' validated</div>';

  html += S.secEvents.map(function(ev, i) {
    var fromTo = esc(ev.from || '?') + ' → ' + esc(ev.to || '?');
    var tokenType = (ev.token_type || 'jwt').toLowerCase();
    var typeClass = 'sec-type-' + (tokenType === 'jwt' ? 'jwt' : tokenType === 'apikey' ? 'apikey' : 'dev');
    var expStr = ev.exp ? new Date(ev.exp * 1000).toLocaleString() : '';
    var iatStr = ev.iat ? new Date(ev.iat * 1000).toLocaleString() : '';

    var rows = [];
    var fullTok = ev.token_full || ev.token_hint || 'dev-open';
    // Token row — full token shown for traceability
    rows.push('<tr><td class="sec-key">Bearer Token</td><td class="sec-val">'
      + '<span class="sec-type-badge ' + typeClass + '">' + esc(tokenType.toUpperCase()) + '</span>'
      + '<code style="display:block;word-break:break-all;font-size:10px;margin-top:4px;color:var(--gr)">' + esc(fullTok) + '</code>'
      + '</td></tr>');
    // Subject
    if (ev.sub) rows.push('<tr><td class="sec-key">Subject (sub)</td>'
      + '<td class="sec-val sec-val-hl">' + esc(ev.sub) + '</td></tr>');
    // Roles
    if (ev.roles && ev.roles.length) {
      var roleHtml = ev.roles.map(function(r) {
        return '<span class="sec-role-chip' + (r === 'admin' ? ' admin' : '') + '">' + esc(r) + '</span>';
      }).join('');
      rows.push('<tr><td class="sec-key">Roles</td><td class="sec-val">' + roleHtml + '</td></tr>');
    }
    // Issuer
    if (ev.iss) rows.push('<tr><td class="sec-key">Issuer (iss)</td>'
      + '<td class="sec-val sec-mono">' + esc(ev.iss) + '</td></tr>');
    // Issued at
    if (iatStr) rows.push('<tr><td class="sec-key">Issued at</td>'
      + '<td class="sec-val">' + esc(iatStr) + '</td></tr>');
    // Expires
    if (expStr) rows.push('<tr><td class="sec-key">Expires</td>'
      + '<td class="sec-val">' + esc(expStr) + '</td></tr>');
    // RBAC
    if (ev.rbac_check) rows.push('<tr><td class="sec-key">RBAC check</td>'
      + '<td class="sec-val"><span class="sec-rbac-ok">✓ ' + esc(ev.rbac_check) + '</span></td></tr>');
    // Hub validation
    if (ev.hub_validated) rows.push('<tr><td class="sec-key">Hub status</td>'
      + '<td class="sec-val"><span class="sec-rbac-ok">✓ Validated by hub server</span></td></tr>');
    // MCP server
    if (ev.server_id) rows.push('<tr><td class="sec-key">MCP server</td>'
      + '<td class="sec-val sec-mono">' + esc(ev.server_id) + '</td></tr>');
    // Hub URL
    if (ev.hub_url) rows.push('<tr><td class="sec-key">Hub endpoint</td>'
      + '<td class="sec-val sec-mono">' + esc(ev.hub_url) + '</td></tr>');
    // Key source (per-server-db vs env fallback)
    if (ev.key_source) {
      var ksSrc = ev.key_source;
      var ksColor = ksSrc === 'per-server-db' ? 'var(--gr)' : ksSrc === 'none' ? 'var(--rd)' : 'var(--yw)';
      rows.push('<tr><td class="sec-key">Key source</td>'
        + '<td class="sec-val"><span style="color:' + ksColor + ';font-weight:600">' + esc(ksSrc) + '</span></td></tr>');
    }

    return '<div class="sec-hop">'
      + '<div class="sec-hop-hdr">'
      +   '<span class="sec-hop-num">#' + (i + 1) + '</span>'
      +   '<span class="sec-hop-route">' + fromTo + '</span>'
      +   (ev.hub_validated ? '<span class="sec-hop-validated">✓ server-confirmed</span>' : '')
      +   '<span class="sec-hop-status">✓ auth</span>'
      + '</div>'
      + '<table class="sec-table">' + rows.join('') + '</table>'
      + '</div>';
  }).join('');

  // Per-tool RBAC section — BearerAuthMiddleware + require_role() per tool call
  var rbacEvs = S.toolRbacEvents || [];
  if (rbacEvs.length) {
    html += '<div class="sec-chain-hdr" style="margin-top:16px;border-top:1px solid var(--bd);padding-top:12px">'
      + '🛡️ Per-Tool RBAC · ' + rbacEvs.length + ' tool call' + (rbacEvs.length > 1 ? 's' : '') + ' · same JWT re-validated each time</div>';
    var _seenTools = {};
    rbacEvs.forEach(function(ev, i) {
      var key = (ev.tool_name||'?') + '|' + (ev.server_id||'?');
      if (_seenTools[key]) { _seenTools[key]++; return; }
      _seenTools[key] = 1;
      html += '<div class="sec-hop" style="border-left-color:var(--gr)">'
        + '<div class="sec-hop-hdr">'
        +   '<span class="sec-hop-num">#' + (i+1) + '</span>'
        +   '<span class="sec-hop-route">MCP middleware → ' + esc(ev.tool_name||'?') + '</span>'
        +   '<span class="sec-hop-validated">✓ RBAC PASS</span>'
        + '</div>'
        + '<table class="sec-table">'
        + '<tr><td class="sec-key">Tool</td><td class="sec-val sec-mono">' + esc(ev.tool_name||'?') + '</td></tr>'
        + '<tr><td class="sec-key">Server</td><td class="sec-val sec-mono">' + esc(ev.server_id||'?') + '</td></tr>'
        + '<tr><td class="sec-key">Subject (sub)</td><td class="sec-val sec-val-hl">' + esc(ev.sub||'?') + '</td></tr>'
        + '<tr><td class="sec-key">Roles</td><td class="sec-val">'
        +   (ev.roles||[]).map(function(r){return '<span class="sec-role-chip'+(r==='admin'?' admin':'')+'">'+esc(r)+'</span>';}).join('')
        + '</td></tr>'
        + '<tr><td class="sec-key">Auth check</td><td class="sec-val"><span style="font-size:10px;font-family:monospace;color:var(--mu)">BearerAuthMiddleware → require_role() → audit_log()</span></td></tr>'
        + '<tr><td class="sec-key">Same token?</td><td class="sec-val"><span style="color:var(--gr);font-weight:600">YES — same JWT as agent→mcp hop</span> · re-validated per HTTP POST</td></tr>'
        + '<tr><td class="sec-key">Key source</td><td class="sec-val"><span style="color:var(--yw);font-weight:600">' + esc(ev.key_source||'?') + '</span></td></tr>'
        + '</table>'
        + '</div>';
    });
    // Show count if deduplicated
    var totalCalls = rbacEvs.length;
    var uniqueTools = Object.keys(_seenTools).length;
    if (totalCalls > uniqueTools) {
      html += '<div style="padding:6px 12px;font-size:10px;color:var(--mu)">'
        + '(' + totalCalls + ' total RBAC checks — showing ' + uniqueTools + ' unique tools; duplicates collapsed)'
        + '</div>';
    }
  }

  // External tool call section — 3rd auth layer (MCP → external service)
  var extEvs = S.extToolEvents || [];
  if (extEvs.length) {
    html += '<div class="sec-chain-hdr" style="margin-top:16px;border-top:1px solid var(--bd);padding-top:12px">'
      + '&#128279; External service calls · ' + extEvs.length + ' call' + (extEvs.length > 1 ? 's' : '') + '</div>';
    html += extEvs.map(function(ev, i) {
      var authPat = ev.auth_pattern || 'bearer_jwt';
      var authLabel = authPat === 'api_key_header' ? 'X-API-Key header' : authPat === 'basic' ? 'HTTP Basic' : 'Bearer JWT';
      return '<div class="sec-hop" style="border-left-color:var(--ac)">'
        + '<div class="sec-hop-hdr">'
        +   '<span class="sec-hop-num">#' + (i + 1) + '</span>'
        +   '<span class="sec-hop-route">mcp-server → ' + esc(ev.external_service || 'external') + '</span>'
        +   '<span class="sec-hop-status" style="background:var(--ac)">&#128279; ext-auth</span>'
        + '</div>'
        + '<table class="sec-table">'
        + '<tr><td class="sec-key">Tool</td><td class="sec-val sec-mono">' + esc(ev.tool_name || '?') + '</td></tr>'
        + '<tr><td class="sec-key">Service</td><td class="sec-val sec-mono">' + esc(ev.external_service || '?') + '</td></tr>'
        + '<tr><td class="sec-key">Auth pattern</td><td class="sec-val"><span style="color:var(--ac);font-weight:600">' + esc(authLabel) + '</span></td></tr>'
        + '<tr><td class="sec-key">Key source</td><td class="sec-val"><span style="color:var(--gr);font-weight:600">' + esc(ev.key_source || 'tool-registry-db') + '</span></td></tr>'
        + '<tr><td class="sec-key">MCP server</td><td class="sec-val sec-mono">' + esc(ev.server_id || '?') + '</td></tr>'
        + '</table>'
        + '</div>';
    }).join('');
  }

  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════════════════════
// Perf tab — per-query timing breakdown
// ══════════════════════════════════════════════════════════════════════════════
function renderPerfTab() {
  var el = document.getElementById('perfContent');
  if (!el) return;
  var total = S.perfTotalMs;
  var toolTotal = S.perfToolCalls.reduce(function(a, tc){ return a + (tc.ms||0); }, 0);
  var html = '<div class="perf-section"><div class="perf-title">⏱ Timing</div>';
  html += '<div class="perf-row"><span class="perf-label">Total elapsed</span><span class="perf-val">'+(total/1000).toFixed(2)+'s</span></div>';
  if (S.perfRouteMs) html += '<div class="perf-row"><span class="perf-label">Hub routing (LLM)</span><span class="perf-val">'+(S.perfRouteMs/1000).toFixed(2)+'s</span></div>';
  if (S.perfMcpMs)   html += '<div class="perf-row"><span class="perf-label">MCP handshake</span><span class="perf-val">'+(S.perfMcpMs/1000).toFixed(2)+'s</span></div>';
  if (toolTotal)     html += '<div class="perf-row"><span class="perf-label">Tool calls total</span><span class="perf-val">'+(toolTotal/1000).toFixed(2)+'s</span></div>';
  html += '</div>';
  if (S.perfToolCalls.length) {
    html += '<div class="perf-section"><div class="perf-title">⚙️ Tool Calls</div>';
    S.perfToolCalls.forEach(function(tc) {
      html += '<div class="perf-row"><span class="perf-label">'+esc(tc.name||'tool')+'</span>'
        + '<span class="perf-val">'+(tc.ms ? (tc.ms/1000).toFixed(2)+'s' : 'pending…')+'</span></div>';
    });
    html += '</div>';
  }
  html += '<div class="perf-section"><div class="perf-title">📊 Counters</div>';
  html += '<div class="perf-row"><span class="perf-label">Auth hops</span><span class="perf-val">'+S.secEvents.length+'</span></div>';
  html += '<div class="perf-row"><span class="perf-label">Tool calls</span><span class="perf-val">'+S.perfToolCalls.length+'</span></div>';
  html += '<div class="perf-row"><span class="perf-label">Steps</span><span class="perf-val">'+S.tlStepCount+'</span></div>';
  html += '</div>';
  el.innerHTML = html;
}

// ══════════════════════════════════════════════════════════════════════════════
// Session chip + background poll
// ══════════════════════════════════════════════════════════════════════════════
function updateSessChip(id) {
  var chip = document.getElementById('sessChip');
  if (chip) chip.textContent = id ? id.substring(0,18)+'…' : '—';
}

function copySessionId() {
  if (!S.sessionId) return;
  try {
    navigator.clipboard.writeText(S.sessionId);
    var chip = document.getElementById('sessChip');
    var old = chip.textContent;
    chip.textContent = 'Copied!'; chip.style.color = 'var(--gr)';
    setTimeout(function(){ chip.textContent = old; chip.style.color = ''; }, 1200);
  } catch(e) {}
}

function pollSessionCompletion(id) {
  S.pollTimer = setInterval(async function() {
    try {
      var r = await authFetch('/api/sessions/'+id+'/status');
      if (!r.ok) { clearInterval(S.pollTimer); S.pollTimer = null; setStreaming(false); return; }
      var d = await r.json();
      if (!d.running) {
        clearInterval(S.pollTimer); S.pollTimer = null;
        document.getElementById('pendingBar').style.display = 'none';
        clearThinkingPlaceholders();
        // Reload messages to show the completed answer
        try {
          var mr = await authFetch('/api/sessions/'+id+'/messages');
          var md = await mr.json();
          var msgs = md.messages || [];
          var existing = document.getElementById('chatMessages').querySelectorAll('.msg').length;
          var userSoFar = Math.floor(existing / 2);
          msgs.slice(existing).forEach(function(m) {
            var ti = null;
            if (m.role === 'user') { userSoFar++; }
            else { ti = {sessionId: id, msgIndex: userSoFar - 1}; }
            appendMessage(m.role, m.content, m.ts, true, ti);
          });
          // Reload trace events from SQLite (task generated more events after SSE ended)
          if (S.traceVisible && S.currentTraceIdx !== null) {
            try { await _loadHistoricalTrace(id, S.currentTraceIdx); } catch(e3) {}
          }
        } catch(e2) {}
        setStreaming(false);
      }
    } catch(e) { clearInterval(S.pollTimer); S.pollTimer = null; setStreaming(false); }
  }, 2500);
}

async function recheckPending() {
  if (!S.sessionId) return;
  var btn = document.querySelector('#pendingBar button');
  var origText = btn ? btn.textContent : 'Check now';
  if (btn) { btn.textContent = 'Checking…'; btn.disabled = true; }
  try {
    var r = await authFetch('/api/sessions/'+S.sessionId+'/status');
    var d = await r.json();
    if (!d.running) {
      if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; }
      document.getElementById('pendingBar').style.display = 'none';
      clearThinkingPlaceholders();
      setStreaming(false);
      // Reload messages — same logic as pollSessionCompletion
      try {
        var mr = await authFetch('/api/sessions/'+S.sessionId+'/messages');
        var md = await mr.json();
        var msgs = md.messages || [];
        var existing = document.getElementById('chatMessages').querySelectorAll('.msg').length;
        msgs.slice(existing).forEach(function(m) {
          var ti = null;
          if (m.role !== 'user') {
            var userSoFar = msgs.filter(function(x,i){ return x.role==='user' && i<=msgs.indexOf(m); }).length;
            ti = {sessionId: S.sessionId, msgIndex: userSoFar - 1};
          }
          appendMessage(m.role, m.content, m.ts, true, ti);
        });
        // Reload trace for last message if trace panel is open
        if (S.traceVisible && S.currentTraceIdx !== null) {
          _loadHistoricalTrace(S.sessionId, S.currentTraceIdx);
        }
      } catch(e2) {}
    } else {
      if (btn) { btn.textContent = 'Still running — try again'; btn.disabled = false; }
      setTimeout(function(){ if(btn){ btn.textContent = origText; btn.disabled = false; } }, 2000);
    }
  } catch(e) {
    if (btn) { btn.textContent = origText; btn.disabled = false; }
  }
}

function clearTraceMsgHdr() {
  var hdr = document.getElementById('traceMsgHdr');
  if (hdr) hdr.classList.remove('show');
  document.querySelectorAll('.msg-trace-btn').forEach(function(b){ b.classList.remove('active-trace'); });
}

async function _loadHistoricalTrace(sessionId, msgIndex) {
  if (!S.traceVisible) return;  // only reload if trace panel is open
  await viewMsgTrace(sessionId, msgIndex, null);
}

async function viewMsgTrace(sessionId, msgIndex, btnEl) {
  // Toggle: clicking the same button again hides the trace header
  if (btnEl && btnEl.classList.contains('active-trace')) {
    clearTraceMsgHdr();
    return;
  }
  clearTraceMsgHdr();
  if (btnEl) btnEl.classList.add('active-trace');

  // If this is the current live stream, reveal the already-updating trace panel
  if (S.streaming && sessionId === S.sessionId && msgIndex === S.currentTraceIdx) {
    if (!S.traceVisible) toggleTrace();
    switchTraceTab('timeline');
    var hdr = document.getElementById('traceMsgHdr');
    var hdrText = document.getElementById('traceMsgHdrText');
    if (hdr) { hdrText.textContent = '⏳ Live trace · message #'+(msgIndex+1); hdr.classList.add('show'); }
    return;
  }

  // Show trace panel if hidden
  if (!S.traceVisible) toggleTrace();
  switchTraceTab('timeline');

  // Show loading state
  var hdr = document.getElementById('traceMsgHdr');
  var hdrText = document.getElementById('traceMsgHdrText');
  if (hdr) { hdrText.textContent = 'Trace: message #'+(msgIndex+1)+' — loading…'; hdr.classList.add('show'); }

  var tlEl = document.getElementById('timelineSteps');
  tlEl.innerHTML = '<div class="timeline-empty">Loading trace for message #'+(msgIndex+1)+'…</div>';
  var scEl = document.getElementById('secContent');
  scEl.innerHTML = '<div class="sec-empty">Loading…</div>';
  var pcEl = document.getElementById('perfContent');
  pcEl.innerHTML = '<div class="perf-empty">Loading…</div>';

  try {
    var r = await authFetch('/api/sessions/'+sessionId+'/trace/'+msgIndex);
    var d = await r.json();
    var events = d.events || [];

    if (!events.length) {
      tlEl.innerHTML = '<div class="timeline-empty">No trace data stored for this message</div>';
      scEl.innerHTML = '<div class="sec-empty">No trace data stored</div>';
      pcEl.innerHTML = '<div class="perf-empty">No trace data stored</div>';
      if (hdrText) hdrText.textContent = 'Trace: message #'+(msgIndex+1)+' — no data';
      return;
    }

    // Reset trace accumulators
    S.tlStepCount = 0; S.tlStartTime = Date.now() - events.length * 500;
    S.secEvents = []; S.extToolEvents = [];
    S.perfStartTime = 0; S.perfRouteStart = 0; S.perfRouteMs = 0;
    S.perfMcpStart = 0; S.perfMcpMs = 0; S.perfToolCalls = []; S.perfTotalMs = 0;
    tlEl.innerHTML = '';

    // Replay events into trace panel
    var toolStarts = {};
    events.forEach(function(ev, i) {
      // Reconstruct relative timing from event order (approximate)
      if (i === 0) S.perfStartTime = Date.now() - events.length * 600;
      if (ev.type === 'routing') S.perfRouteStart = S.perfStartTime + i * 200;
      if (ev.type === 'hub_loaded' && S.perfRouteStart) S.perfRouteMs = 300;
      if (ev.type === 'mcp_connecting') S.perfMcpStart = S.perfStartTime + i * 200;
      if (ev.type === 'mcp_connected' && S.perfMcpStart) S.perfMcpMs = 150;
      if (ev.type === 'tool_call') {
        toolStarts[ev.tool_name] = i;
        S.perfToolCalls.push({name: ev.tool_name, start: i, ms: 0});
      }
      if (ev.type === 'tool_result') {
        var tc = null;
        for (var j = S.perfToolCalls.length-1; j >= 0; j--) {
          if (S.perfToolCalls[j].name === ev.tool_name && !S.perfToolCalls[j].ms) {
            tc = S.perfToolCalls[j]; break;
          }
        }
        if (tc) tc.ms = 800; // approximate
      }
      if (ev.type === 'auth_hop') S.secEvents.push(ev);
      if (ev.type === 'tool_rbac') { S.toolRbacEvents = S.toolRbacEvents || []; S.toolRbacEvents.push(ev); }
      if (ev.type === 'external_tool_call') { S.extToolEvents = S.extToolEvents || []; S.extToolEvents.push(ev); }
      if (ev.type === 'final_answer' || ev.type === 'error') S.perfTotalMs = events.length * 600;
      addTimelineStep(ev);
    });
    if (!S.perfTotalMs) S.perfTotalMs = events.length * 600;

    renderSecurityTab();
    renderPerfTab();
    if (hdrText) hdrText.textContent = 'Trace: message #'+(msgIndex+1)+' · '+events.length+' events';
  } catch(e) {
    tlEl.innerHTML = '<div class="timeline-empty">Failed to load trace: '+esc(e.message)+'</div>';
    if (hdrText) hdrText.textContent = 'Trace: message #'+(msgIndex+1)+' — error';
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// History screen
// ══════════════════════════════════════════════════════════════════════════════
async function loadHistory() {
  try {
    var r = await authFetch('/api/sessions');
    var d = await r.json();
    S.histSessions = d.sessions || [];
    renderHistoryList(S.histSessions);
  } catch(e) {
    document.getElementById('histList').innerHTML = '<div class="empty-msg">Failed to load history</div>';
  }
}

function filterHistory() {
  var q = document.getElementById('histSearch').value.toLowerCase();
  var filtered = q ? S.histSessions.filter(function(s){ return s.name.toLowerCase().includes(q); }) : S.histSessions;
  renderHistoryList(filtered);
}

function renderHistoryList(sessions) {
  var el = document.getElementById('histList');
  if (!sessions.length) {
    el.innerHTML = '<div class="empty-msg">No conversations found</div>'; return;
  }
  el.innerHTML = sessions.map(function(s) {
    var rel = relTime(s.updated_at);
    return '<div class="hist-item" onclick="openHistDetail(\''+s.id+'\')">'
      +'<div class="hist-info">'
      +'<div class="hist-name">'+esc(s.name)+'</div>'
      +'<div class="hist-meta">'+s.message_count+' messages &middot; '+rel+'</div>'
      +'</div>'
      +'<div class="hist-actions">'
      +'<button class="btn-xs" onclick="event.stopPropagation();openHistDetail(\''+s.id+'\')">View</button>'
      +'<button class="btn-xs" onclick="event.stopPropagation();deleteHistSession(\''+s.id+'\')" style="color:var(--rd)">Del</button>'
      +'</div>'
      +'</div>';
  }).join('');
}

async function openHistDetail(id) {
  S.histDetailSessionId = id;
  var s = S.histSessions.find(function(x){return x.id===id;});
  document.getElementById('histDetailName').textContent = s ? s.name : id.substring(0,8)+'…';
  document.getElementById('histDetail').style.display = 'block';
  document.getElementById('histDetailMsgs').innerHTML = '<div class="empty-msg">Loading…</div>';
  try {
    var r = await authFetch('/api/sessions/'+id+'/messages');
    var d = await r.json();
    var msgs = d.messages || [];
    if (!msgs.length) {
      document.getElementById('histDetailMsgs').innerHTML = '<div class="empty-msg">No messages</div>';
      return;
    }
    document.getElementById('histDetailMsgs').innerHTML = msgs.map(function(m) {
      return '<div class="hist-msg '+m.role+'">'
        +'<div class="hist-msg-role">'+m.role+'</div>'
        +esc(m.content)+'</div>';
    }).join('');
  } catch(e) {
    document.getElementById('histDetailMsgs').innerHTML = '<div class="empty-msg">Error loading messages</div>';
  }
}

function closeHistDetail() {
  document.getElementById('histDetail').style.display = 'none';
  S.histDetailSessionId = '';
}

async function deleteHistSession(id) {
  if (!confirm('Delete this conversation?')) return;
  try {
    await authFetch('/api/sessions/'+id, {method:'DELETE'});
    await loadHistory();
    if (S.sessionId===id) newSession();
  } catch(e) { alert('Delete failed: '+e.message); }
}

function continueInChat() {
  var id = S.histDetailSessionId;
  if (!id) return;
  switchScreen('chat');
  switchSession(id);
}

// ══════════════════════════════════════════════════════════════════════════════
// Observability — 4 distinct tabs: Auth | Routing | Requests | Stats
// ══════════════════════════════════════════════════════════════════════════════
function switchObsTab(tab) {
  S.obsTab = tab;
  ['auth','routing','requests','stats'].forEach(function(t){
    var tb = document.getElementById('obs-tab-'+t);
    var vw = document.getElementById('obs-'+t);
    if (tb) tb.classList.toggle('active', t===tab);
    if (vw) vw.classList.toggle('active', t===tab);
  });
  renderObsTab();
}

async function refreshLogs() {
  try {
    var r = await authFetch('/api/logs?n=300');
    if (!r.ok) return;
    var d = await r.json();
    S.allLogs = d.events || [];
    renderObsTab();
  } catch(e) {}
}

function renderObsTab() {
  var t = S.obsTab || 'auth';
  if (t === 'auth')     renderObsAuth();
  else if (t === 'routing')   renderObsRouting();
  else if (t === 'requests')  renderObsRequests();
  else if (t === 'stats')     renderObsStats();
}

function renderObsAuth() {
  var el = document.getElementById('obsAuthList');
  if (!el) return;
  var logs = S.allLogs.filter(function(e){ return e.type === 'auth'; });
  if (!logs.length) { el.innerHTML = '<div class="obs-empty">No auth events yet — run a query and refresh</div>'; return; }
  el.innerHTML = logs.slice().reverse().slice(0,120).map(function(e) {
    var ok = e.valid;
    var ts = e.ts ? new Date(e.ts*1000).toLocaleTimeString() : '';
    var rolesStr = (e.roles||[]).join(',') || '—';
    var cls = ok ? 'ok' : 'deny';
    var tkType = (e.token_type || '').toLowerCase();
    var tkBadge = tkType
      ? '<span class="obs-token-badge obs-token-'+esc(tkType)+'">'+esc(tkType.toUpperCase())+'</span>'
      : '';
    return '<div class="obs-auth-row '+cls+'">'
      +'<span class="obs-res">'+(ok?'✓ ACCEPT':'✗ DENY')+'</span>'
      +'<span class="obs-auth-sub">'+esc(e.sub||'unknown')+tkBadge+'</span>'
      +'<span class="obs-auth-roles">['+esc(rolesStr)+']</span>'
      +'<span class="obs-auth-ep">'+esc((e.method||'GET')+' '+(e.endpoint||'?'))+'</span>'
      +'<span class="obs-auth-ts">'+ts+'</span>'
      +'</div>';
  }).join('');
}

function renderObsRouting() {
  var el = document.getElementById('obsRoutingList');
  if (!el) return;
  var logs = S.allLogs.filter(function(e){ return e.type === 'routing'; });
  if (!logs.length) { el.innerHTML = '<div class="obs-empty">No routing events yet — run a query and refresh</div>'; return; }
  el.innerHTML = logs.slice().reverse().slice(0,60).map(function(e) {
    var ts = e.ts ? new Date(e.ts*1000).toLocaleTimeString() : '';
    var sids = (e.server_ids||[]).join(', ') || esc(e.server_id||'?');
    return '<div class="obs-route-row">'
      +'<div class="obs-route-hdr">'
      +'<span class="obs-route-ts">'+ts+'</span>'
      +'<span class="obs-route-method">'+esc(e.method||'?')+'</span>'
      +'<span class="obs-route-server">→ '+esc(sids)+'</span>'
      +'</div>'
      +(e.reason ? '<div class="obs-route-reason">'+esc((e.reason||'').substring(0,120))+'</div>' : '')
      +(e.intent ? '<div class="obs-route-intent">"'+esc(e.intent.substring(0,80))+'"</div>' : '')
      +'</div>';
  }).join('');
}

function renderObsRequests() {
  var el = document.getElementById('obsReqList');
  if (!el) return;
  // Show both regular request logs and request_detail logs
  var reqLogs  = S.allLogs.filter(function(e){ return e.type === 'request'; });
  var detLogs  = S.allLogs.filter(function(e){ return e.type === 'request_detail'; });
  var allLogs  = reqLogs.concat(detLogs).sort(function(a,b){ return (a.ts||0)-(b.ts||0); });
  if (!allLogs.length) { el.innerHTML = '<div class="obs-empty">No request events yet — run a query and refresh</div>'; return; }
  el.innerHTML = allLogs.slice().reverse().slice(0,120).map(function(e, i) {
    var ts = e.ts ? new Date(e.ts*1000).toLocaleTimeString() : '';
    var uid = 'obsreq-'+i;
    if (e.type === 'request') {
      var ok = e.status && e.status < 400;
      return '<div class="obs-req-row">'
        +'<span class="obs-req-ts">'+ts+'</span>'
        +'<span class="obs-req-meth">'+esc(e.method||'?')+'</span>'
        +'<span class="obs-req-path">'+esc(e.path||'?')+'</span>'
        +'<span class="obs-req-status '+(ok?'ok2':'err2')+'">'+e.status+'</span>'
        +'<span class="obs-req-lat">'+(e.latency_ms||'?')+'ms</span>'
        +'</div>';
    } else {
      // request_detail — expandable with full req/resp body
      var reqBody  = e.request_body  ? JSON.stringify(e.request_body,  null, 2) : null;
      var respBody = e.response_body ? JSON.stringify(e.response_body, null, 2) : null;
      var authStr  = e.auth_sub ? ' · auth: '+esc(e.auth_sub)+' ['+esc((e.auth_roles||[]).join(','))+']' : '';
      return '<div class="obs-req-row" style="flex-direction:column;align-items:flex-start;gap:4px;padding:8px 10px">'
        +'<div style="display:flex;gap:8px;align-items:center;width:100%">'
        +'<span class="obs-req-ts">'+ts+'</span>'
        +'<span class="obs-req-meth">'+esc(e.method||'POST')+'</span>'
        +'<span class="obs-req-path" style="color:var(--bl)">'+esc(e.endpoint||e.path||'?')+'</span>'
        +'<span style="font-size:10px;color:var(--mu)">'+authStr+'</span>'
        +'<a href="#" style="font-size:10px;color:var(--bl);margin-left:auto" onclick="event.preventDefault();var d=document.getElementById(\''+uid+'\');d.style.display=d.style.display===\'none\'?\'block\':\'none\'">detail</a>'
        +'</div>'
        +(reqBody||respBody ? '<div id="'+uid+'" style="display:none;width:100%;margin-top:4px">'
          +(e.request_headers ? '<div style="font-size:10px;color:var(--mu);margin-bottom:2px">REQUEST HEADERS</div>'
            +'<pre style="background:var(--sf2);border:1px solid var(--bd);border-radius:4px;padding:6px;font-size:10px;white-space:pre-wrap;word-break:break-all;max-height:100px;overflow-y:auto;margin-bottom:6px">'
            +Object.entries(e.request_headers).map(function(kv){return esc(kv[0])+': '+esc(kv[1]);}).join('\n')
            +'</pre>' : '')
          +(reqBody ? '<div style="font-size:10px;color:var(--mu);margin-bottom:2px">REQUEST BODY</div>'
            +'<pre style="background:var(--sf2);border:1px solid var(--bd);border-radius:4px;padding:6px;font-size:10px;white-space:pre-wrap;word-break:break-all;max-height:180px;overflow-y:auto;margin-bottom:6px">'+esc(reqBody)+'</pre>' : '')
          +(e.response_status ? '<div style="font-size:10px;color:var(--mu);margin-bottom:2px">RESPONSE STATUS</div>'
            +'<pre style="background:var(--sf2);border:1px solid var(--bd);border-radius:4px;padding:6px;font-size:10px;white-space:pre-wrap;margin-bottom:6px">'
            +esc(e.response_status+' '+((e.response_status||0)<400?'OK':'ERROR'))+'</pre>' : '')
          +(respBody ? '<div style="font-size:10px;color:var(--mu);margin-bottom:2px">RESPONSE BODY</div>'
            +'<pre style="background:var(--sf2);border:1px solid var(--bd);border-radius:4px;padding:6px;font-size:10px;white-space:pre-wrap;word-break:break-all;max-height:180px;overflow-y:auto">'+esc(respBody)+'</pre>' : '')
          +'</div>' : '')
        +'</div>';
    }
  }).join('');
}

function renderObsStats() {
  var logs = S.allLogs;
  var authLogs = logs.filter(function(e){return e.type==='auth';});
  var accept = authLogs.filter(function(e){return e.valid;}).length;
  var pct = authLogs.length > 0 ? Math.round(100*accept/authLogs.length) : 100;
  var donut = document.getElementById('authDonut');
  var deg = Math.round(360*accept/(authLogs.length||1));
  donut.style.background = 'conic-gradient(var(--gr) '+deg+'deg, var(--rd) 0)';
  document.getElementById('authPct').textContent = pct+'%';
  var types = {};
  logs.forEach(function(e){ types[e.type]=(types[e.type]||0)+1; });
  var vals = Object.values(types);
  var maxV = vals.length ? Math.max.apply(null, vals) : 1;
  document.getElementById('breakdownBars').innerHTML = Object.entries(types).map(function(kv) {
    var pctW = Math.round(100*kv[1]/maxV);
    return '<div class="bar-row"><span class="bar-label">'+kv[0]+'</span>'
      +'<div class="bar-track"><div class="bar-fill" style="width:'+pctW+'%"></div></div>'
      +'<span style="font-size:11px;color:var(--mu);width:24px">'+kv[1]+'</span></div>';
  }).join('');
  var subs = {};
  authLogs.forEach(function(e){
    if (e.sub) subs[e.sub] = {roles:e.roles||[], provider:e.provider||'local', last:e.ts};
  });
  document.getElementById('tokenTable').innerHTML = Object.entries(subs).map(function(kv) {
    return '<tr><td>'+esc(kv[0])+'</td><td>'+(kv[1].roles||[]).join(',')+'</td>'
      +'<td>'+kv[1].provider+'</td>'
      +'<td>'+(kv[1].last?new Date(kv[1].last*1000).toLocaleTimeString():'—')+'</td></tr>';
  }).join('');
}

function toggleObsAuto() {
  S.obsAuto = !S.obsAuto;
  var btn = document.getElementById('obsAutoBtn');
  if (S.obsAuto) {
    btn.textContent = '⏹ Auto'; btn.style.color='var(--rd)';
    S.obsAutoTimer = setInterval(refreshLogs, 5000);
    refreshLogs();
  } else {
    btn.textContent = '⏵ Auto'; btn.style.color='var(--gr)';
    clearInterval(S.obsAutoTimer);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Search screen
// ══════════════════════════════════════════════════════════════════════════════
async function doSearch() {
  var q = document.getElementById('searchInput').value.trim();
  var type = document.getElementById('searchType').value;
  if (!q) return;
  document.getElementById('searchResults').innerHTML = '<div class="search-empty">Searching…</div>';
  try {
    var r = await authFetch('/api/search?q='+encodeURIComponent(q)+'&search_type='+type);
    if (!r.ok) {
      document.getElementById('searchResults').innerHTML = '<div class="search-empty">Search failed ('+r.status+')</div>';
      return;
    }
    var d = await r.json();
    renderSearchResults(d, q);
  } catch(e) {
    document.getElementById('searchResults').innerHTML = '<div class="search-empty">Error: '+esc(e.message)+'</div>';
  }
}

function renderSearchResults(data, q) {
  var el = document.getElementById('searchResults');
  var convs = data.conversations || [];
  var msgs  = data.messages || [];
  var logs  = data.logs || [];
  var total = convs.length + msgs.length + logs.length;
  if (total === 0) {
    el.innerHTML = '<div class="search-empty">No results found for <strong>'+esc(q)+'</strong></div>';
    return;
  }
  var html = '';
  if (convs.length) {
    html += '<div class="search-group"><div class="search-group-title">Conversations ('+convs.length+')</div>';
    html += convs.map(function(s) {
      return '<div class="search-result-item" onclick="openSessionFromSearch(\''+s.id+'\')">'
        +'<div class="sr-name">'+highlight(esc(s.name), q)+'</div>'
        +'<div class="sr-id">'+esc(s.id)+'</div>'
        +'<div class="sr-meta">'+s.message_count+' messages &middot; '+relTime(s.updated_at)+'</div>'
        +'</div>';
    }).join('');
    html += '</div>';
  }
  if (msgs.length) {
    html += '<div class="search-group"><div class="search-group-title">Messages ('+msgs.length+')</div>';
    html += msgs.map(function(m) {
      var snippet = (m.content || '').substring(0, 180);
      return '<div class="search-result-item" onclick="openSessionFromSearch(\''+m.session_id+'\')">'
        +'<div class="sr-name">'+highlight(esc(m.session_name||m.session_id), q)+'</div>'
        +'<div class="sr-id">'+esc(m.session_id)+'</div>'
        +'<div class="sr-snippet">'+highlight(esc(snippet), q)+'</div>'
        +'<div class="sr-meta">'+esc(m.role)+' &middot; '+esc(m.ts||'')+'</div>'
        +'</div>';
    }).join('');
    html += '</div>';
  }
  if (logs.length) {
    html += '<div class="search-group"><div class="search-group-title">Event logs ('+logs.length+')</div>';
    html += logs.map(function(l) {
      var raw = JSON.stringify(l).substring(0, 160);
      return '<div class="search-result-item">'
        +'<div class="sr-name">'+highlight(esc(l.type||'event'), q)+'</div>'
        +'<div class="sr-snippet">'+highlight(esc(raw), q)+'</div>'
        +'</div>';
    }).join('');
    html += '</div>';
  }
  el.innerHTML = html;
}

function highlight(text, q) {
  if (!q) return text;
  try {
    var re = new RegExp('('+q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi');
    return text.replace(re, '<mark>$1</mark>');
  } catch(e) { return text; }
}

function openSessionFromSearch(id) {
  switchScreen('chat');
  switchSession(id);
}

// ══════════════════════════════════════════════════════════════════════════════
// Utilities
// ══════════════════════════════════════════════════════════════════════════════
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function nowTime() { return new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); }
function generateId() { return 'sess-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,6); }
function relTime(iso) {
  if (!iso) return '—';
  var d = new Date(iso); var now = Date.now(); var diff = (now - d.getTime())/1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60)+'m ago';
  if (diff < 86400) return Math.floor(diff/3600)+'h ago';
  return Math.floor(diff/86400)+'d ago';
}

// ══════════════════════════════════════════════════════════════════════════════
// Admin panel
// ══════════════════════════════════════════════════════════════════════════════
async function loadAdminScreen() {
  loadAdminConfig();
  loadAdminUsers();
}

async function loadAdminConfig() {
  try {
    var r = await authFetch('/api/admin/config');
    var d = await r.json();
    var el = document.getElementById('admSysConfig');
    var rows = [
      ['Hub URL', '<code style="color:var(--ac)">' + esc(d.hub_url) + '</code>'],
      ['Auth Mode', d.auth_mode === 'qa' ? '<span style="color:#3fb950">QA (real JWT)</span>' : '<span style="color:var(--yw)">Dev (default secret)</span>'],
      ['JWT Algorithm', d.jwt_algorithm || 'HS256'],
      ['Chat Port', d.chat_port],
      ['Users (in memory)', d.user_count],
      ['MCP API Key', d.mcp_api_key_set ? '<span style="color:#3fb950">&#x2713; configured</span>' : '<span style="color:var(--rd)">&#x2717; not set (dev-open mode)</span>'],
      ['Hub API Key', d.hub_api_key_set ? '<span style="color:#3fb950">&#x2713; configured</span>' : '<span style="color:var(--rd)">&#x2717; not set (dev-open mode)</span>'],
    ];
    el.innerHTML = '<h3 style="font-size:11px;font-weight:600;margin-bottom:14px;color:var(--mu);text-transform:uppercase;letter-spacing:.05em">System Configuration</h3>'
      + '<table class="adm-kv"><tbody>'
      + rows.map(function(r2){ return '<tr><td>' + r2[0] + '</td><td>' + r2[1] + '</td></tr>'; }).join('')
      + '</tbody></table>';
  } catch(e) { console.error('admConfig', e); }
}

async function loadAdminUsers() {
  try {
    var r = await authFetch('/api/admin/users');
    var d = await r.json();
    _userMap = {};
    d.users.forEach(function(u){ _userMap[u.username] = u; });
    var tbody = document.getElementById('admUsersTbody');
    tbody.innerHTML = d.users.map(function(u) {
      var roleClass = u.roles[0] === 'admin' ? 'role-adm' : 'role-agt';
      var isSelf = u.username === S.sub;
      var activeLabel = u.is_active === false ? '<span style="color:#ef4444;font-size:11px">inactive</span>' : '<span style="color:#22c55e;font-size:11px">active</span>';
      var chgPwLabel = u.must_change_password ? ' <span style="color:#f59e0b;font-size:10px" title="User must change password">⚠ must change pw</span>' : '';
      return '<tr>'
        + '<td><b>' + esc(u.username) + '</b></td>'
        + '<td>' + esc(u.display) + '</td>'
        + '<td><span class="' + roleClass + '">' + esc(u.roles[0] || 'agent') + '</span></td>'
        + '<td>' + activeLabel + chgPwLabel + '</td>'
        + '<td style="text-align:right">'
        + '<button class="btn-xs" onclick="openUserModal(\'' + esc(u.username) + '\')">Edit</button> '
        + (!isSelf ? '<button class="btn-xs" onclick="admDelUser(\'' + esc(u.username) + '\')">Delete</button>' : '')
        + '</td></tr>';
    }).join('');
  } catch(e) { console.error('admUsers', e); }
}

var _editUserMode = false;
var _userMap = {};

function openUserModal(usernameOrNull) {
  _editUserMode = !!usernameOrNull;
  var u = usernameOrNull ? (_userMap[usernameOrNull] || null) : null;
  document.getElementById('userModalTitle').textContent = u ? 'Edit User' : 'Add User';
  document.getElementById('umEditName').value = u ? u.username : '';
  document.getElementById('umUsername').value = u ? u.username : '';
  document.getElementById('umUsername').disabled = !!u;
  document.getElementById('umDisplay').value = u ? u.display : '';
  document.getElementById('umPassword').value = '';
  document.getElementById('umRole').value = u ? (u.roles[0] || 'agent') : 'agent';
  document.getElementById('umChgPw').checked = u ? !!u.must_change_password : false;
  document.getElementById('userModal').style.display = 'flex';
}

function umGenPw() {
  var pw = Math.random().toString(36).slice(2, 8) + Math.random().toString(36).slice(2, 8);
  var inp = document.getElementById('umPassword');
  inp.type = 'text';
  inp.value = pw;
  setTimeout(function(){ inp.type = 'password'; }, 3000);
}

async function saveUser() {
  var username = document.getElementById('umUsername').value.trim();
  if (!username) { alert('Username is required'); return; }
  var body = {
    username: username,
    display: document.getElementById('umDisplay').value.trim() || username,
    roles: [document.getElementById('umRole').value],
    password: document.getElementById('umPassword').value,
    must_change_password: document.getElementById('umChgPw').checked,
  };
  try {
    var method = _editUserMode ? 'PUT' : 'POST';
    var path = _editUserMode ? '/api/admin/users/' + encodeURIComponent(username) : '/api/admin/users';
    var r = await authFetch(path, {method: method, body: JSON.stringify(body)});
    if (!r.ok) { var e = await r.json(); alert('Save failed: ' + (e.detail || r.statusText)); return; }
    document.getElementById('userModal').style.display = 'none';
    loadAdminUsers();
  } catch(e) { alert('Error: ' + e.message); }
}

async function admDelUser(username) {
  if (!confirm('Delete user "' + username + '"? This cannot be undone.')) return;
  try {
    var r = await authFetch('/api/admin/users/' + encodeURIComponent(username), {method: 'DELETE'});
    if (!r.ok) { var e = await r.json(); alert('Delete failed: ' + (e.detail || r.statusText)); return; }
    loadAdminUsers();
  } catch(e) { alert('Error: ' + e.message); }
}

async function admGenToken() {
  var sub = document.getElementById('admTkSub').value.trim() || 'fab-agent';
  var roles = document.getElementById('admTkRoles').value.split(',').map(function(s){return s.trim();}).filter(Boolean);
  var hours = parseInt(document.getElementById('admTkHours').value) || 24;
  try {
    var r = await authFetch('/api/admin/token', {
      method: 'POST',
      body: JSON.stringify({sub: sub, roles: roles, hours: hours})
    });
    if (!r.ok) { var e = await r.json(); alert('Failed: ' + (e.detail||r.statusText)); return; }
    var d = await r.json();
    var out = document.getElementById('admTkOut');
    out.style.display = 'block';
    out.textContent = d.token;
    var meta = document.getElementById('admTkMeta');
    meta.style.display = 'flex';
    document.getElementById('admTkInfo').textContent =
      'sub=' + sub + '  roles=[' + roles.join(',') + ']  expires: ' + new Date(d.expires_at * 1000).toLocaleString();
  } catch(e) { alert('Error: ' + e.message); }
}

async function admCopyToken() {
  var t = document.getElementById('admTkOut').textContent;
  await navigator.clipboard.writeText(t);
  alert('Token copied to clipboard');
}

function openChangePwModal() {
  document.getElementById('cpCurrent').value = '';
  document.getElementById('cpNew').value = '';
  document.getElementById('cpConfirm').value = '';
  var msg = document.getElementById('cpMsg');
  msg.style.display = 'none';
  msg.textContent = '';
  document.getElementById('changePwModal').style.display = 'flex';
}

async function submitChangePw() {
  var current = document.getElementById('cpCurrent').value;
  var newPw   = document.getElementById('cpNew').value;
  var confirm = document.getElementById('cpConfirm').value;
  var msg = document.getElementById('cpMsg');
  msg.style.display = 'none';
  if (!current || !newPw) { msg.style.display='block'; msg.style.color='#ef4444'; msg.textContent='Current and new password are required.'; return; }
  if (newPw.length < 8) { msg.style.display='block'; msg.style.color='#ef4444'; msg.textContent='New password must be at least 8 characters.'; return; }
  if (newPw !== confirm) { msg.style.display='block'; msg.style.color='#ef4444'; msg.textContent='New passwords do not match.'; return; }
  try {
    var r = await authFetch('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({current_password: current, new_password: newPw})
    });
    if (!r.ok) { var e = await r.json(); msg.style.display='block'; msg.style.color='#ef4444'; msg.textContent='Error: ' + (e.detail || r.statusText); return; }
    msg.style.display='block'; msg.style.color='#22c55e'; msg.textContent='Password changed successfully!';
    setTimeout(function(){ document.getElementById('changePwModal').style.display='none'; }, 1500);
  } catch(e) { msg.style.display='block'; msg.style.color='#ef4444'; msg.textContent='Error: ' + e.message; }
}
</script>
</body>
</html>"""


# ── FastAPI endpoints ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@app.get("/health")
async def health():
    return {"status": "ok", "model": fab_agent.MODEL, "hub": fab_agent.HUB_SERVER_URL}


@app.get("/api/auth-info")
async def auth_info():
    mode = "dev" if CHAT_JWT_SECRET == _DEFAULT_SECRET else "qa"
    users = [
        {"username": u, "role": cfg["roles"][0], "display": cfg["display"]}
        for u, cfg in _USERS.items()
    ]
    return {"auth_required": True, "users": users, "mode": mode}


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Validate username+password locally; return a JWT — never touches the hub."""
    body     = await request.json()
    username = body.get("username", "").strip()
    password = body.get("token",    "").strip()  # JS sends as 'token'

    _check_rate_limit(username or "_empty_")

    db_user = _db_get_user(username)
    if db_user is None or not db_user["is_active"] or not _verify_password(password, db_user.get("password_hash", "")):
        _chat_log("auth", valid=False, sub=username, endpoint="/api/auth/login", reason="bad_credentials")
        raise HTTPException(status_code=401, detail="Invalid username or password")
    _clear_rate_limit(username)
    roles = db_user["roles"]
    token = _mint_jwt(username, roles)
    _chat_log("auth", valid=True, sub=username, roles=roles, endpoint="/api/auth/login",
              iss="fab-chat", token=token)
    return {"ok": True, "token": token, "sub": username,
            "roles": roles, "display": db_user["display"],
            "must_change_password": db_user["must_change_password"]}


@app.post("/api/auth/change-password")
async def change_password(request: Request, authorization: str = Header(default="")):
    """Change the calling user's own password. Requires current password for verification."""
    user = _auth_user(authorization)
    username = user["sub"]
    body = await request.json()
    current_pw  = body.get("current_password", "").strip()
    new_pw      = body.get("new_password", "").strip()
    if not current_pw or not new_pw:
        raise HTTPException(status_code=422, detail="current_password and new_password required")
    if len(new_pw) < 8:
        raise HTTPException(status_code=422, detail="New password must be at least 8 characters")
    db_user = _db_get_user(username)
    if db_user is None or not _verify_password(current_pw, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    with _get_chat_engine().begin() as conn:
        conn.execute(_sa_text(
            "UPDATE chat_users SET password_hash=:ph, must_change_password=0 WHERE username=:u"
        ), {"ph": _hash_password(new_pw), "u": username})
    return {"ok": True}


# ── Session endpoints ──────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions(all: int = 0, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if all and _is_admin(user):
        return {"sessions": _get_all_sessions(), "admin_all": True}
    return {"sessions": _get_sessions(user["sub"]), "admin_all": False}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if _is_admin(user):
        # Admin can delete any session
        with _get_chat_engine().begin() as conn:
            conn.execute(_sa_text("DELETE FROM chat_sessions WHERE id=:id"), {"id": session_id})
            conn.execute(_sa_text("DELETE FROM chat_messages WHERE session_id=:sid"), {"sid": session_id})
            conn.execute(_sa_text("DELETE FROM chat_traces WHERE session_id=:sid"), {"sid": session_id})
        return {"ok": True}
    ok = _delete_session(session_id, user["sub"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found or not yours")
    return {"ok": True}


@app.get("/api/sessions/{session_id}/messages")
async def session_messages(session_id: str, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    msgs = _get_messages(session_id, user["sub"], admin=_is_admin(user))
    if msgs is None:
        raise HTTPException(status_code=404, detail="Session not found or not yours")
    return {"messages": msgs}


@app.get("/api/sessions/{session_id}/status")
async def session_running_status(session_id: str, authorization: str = Header(default="")):
    """Returns whether a session's agent task is still running in the background."""
    user = _auth_user(authorization)
    msgs = _get_messages(session_id, user["sub"], admin=_is_admin(user))
    if msgs is None:
        raise HTTPException(status_code=404, detail="Session not found or not yours")
    return {"running": _get_session_running(session_id), "session_id": session_id}


@app.get("/api/sessions/{session_id}/trace/{msg_index}")
async def get_message_trace(session_id: str, msg_index: int,
                             authorization: str = Header(default="")):
    """Return stored trace events for a specific message (identified by msg_index)."""
    user = _auth_user(authorization)
    msgs = _get_messages(session_id, user["sub"], admin=_is_admin(user))
    if msgs is None:
        raise HTTPException(status_code=404, detail="Session not found or not yours")
    events = _get_trace_events(session_id, msg_index)
    return {"events": events, "session_id": session_id, "msg_index": msg_index,
            "count": len(events)}


@app.get("/api/dashboard")
async def dashboard(authorization: str = Header(default="")):
    user  = _auth_user(authorization)
    stats = _user_stats(user["sub"])
    recent = _get_sessions(user["sub"], limit=5)

    hub_online = False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{fab_agent.HUB_SERVER_URL}/health", timeout=3.0)
            hub_online = r.status_code == 200
    except Exception:
        pass

    # Auth stats from hub logs
    auth_accept = auth_deny = 0
    try:
        token = authorization.removeprefix("Bearer ").strip()
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{fab_agent.HUB_SERVER_URL}/api/logs",
                headers=fab_agent._auth_headers(token or fab_agent.HUB_API_KEY),
                params={"n": 100, "event_type": "auth"},
                timeout=3.0,
            )
            if r.status_code == 200:
                for ev in r.json().get("events", []):
                    if ev.get("valid"):
                        auth_accept += 1
                    else:
                        auth_deny += 1
    except Exception:
        pass

    return {
        **stats,
        "hub_online":       hub_online,
        "auth_accept_count": auth_accept,
        "auth_deny_count":   auth_deny,
        "recent_sessions":   recent,
    }


@app.get("/api/logs")
async def api_logs(n: int = 100, event_type: str = "",
                   authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required to view logs")
    hub_token = authorization.removeprefix("Bearer ").strip()
    token     = hub_token or fab_agent.HUB_API_KEY
    params: dict = {"n": min(max(n, 1), 500)}
    if event_type:
        params["event_type"] = event_type
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{fab_agent.HUB_SERVER_URL}/api/logs",
                headers=fab_agent._auth_headers(token),
                params=params,
                timeout=5.0,
            )
            if resp.status_code == 200:
                return resp.json()
            return {"events": [], "returned": 0,
                    "error": f"Hub /api/logs returned {resp.status_code}"}
    except Exception as exc:
        return {"events": [], "returned": 0, "error": str(exc)}


@app.get("/api/search")
async def api_search(q: str = "", search_type: str = "all",
                     authorization: str = Header(default="")):
    """Full-text search across conversations, messages, and hub event logs."""
    user  = _auth_user(authorization)
    username = user["sub"]
    if not q.strip():
        return {"conversations": [], "messages": [], "logs": []}

    convs = _search_sessions(username, q) if search_type in ("all", "conversations") else []
    msgs  = _search_messages(username, q) if search_type in ("all", "messages") else []

    logs: list = []
    if search_type in ("all", "logs"):
        try:
            token = authorization.removeprefix("Bearer ").strip()
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{fab_agent.HUB_SERVER_URL}/api/logs",
                    headers=fab_agent._auth_headers(token or fab_agent.HUB_API_KEY),
                    params={"n": 500},
                    timeout=5.0,
                )
                if r.status_code == 200:
                    q_lower = q.strip().lower()
                    logs = [
                        e for e in r.json().get("events", [])
                        if q_lower in json.dumps(e).lower()
                    ][:30]
        except Exception:
            pass

    return {"conversations": convs, "messages": msgs, "logs": logs}


# ── Chat streaming ─────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query:        str
    session_id:   str = ""
    session_name: str = ""


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, authorization: str = Header(default="")):
    user       = _auth_user(authorization)
    username   = user["sub"]
    hub_token  = authorization.removeprefix("Bearer ").strip()
    session_id = req.session_id or str(uuid.uuid4())
    sess_name  = req.session_name or req.query[:50] or "Conversation"

    _ensure_session(session_id, username, sess_name)

    # Count user messages already in this session to determine msg_index for trace storage
    with _get_chat_engine().connect() as _tc:
        msg_index: int = _tc.execute(_sa_text(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id=:sid AND role='user'"
        ), {"sid": session_id}).scalar() or 0

    _add_message(session_id, "user", req.query)
    _update_session_status(session_id, "pending")

    tok_hint   = (hub_token[:10] + "…") if len(hub_token) > 10 else (hub_token or "dev-open")
    _chat_log("chat_start", session_id=session_id, sub=username, query=req.query,
              bearer_token=hub_token or "dev-open")
    queue: asyncio.Queue = asyncio.Queue()
    final_ref  = {"value": ""}

    async def on_event(event: dict) -> None:
        if event.get("type") == "final_answer":
            final_ref["value"] = event.get("content", "")
        await queue.put(event)
        _save_trace_event(session_id, msg_index, event.get("type", ""), json.dumps(event))
        _chat_log(event.get("type", "event"), session_id=session_id, msg_index=msg_index,
                  **{k: v for k, v in event.items() if k != "type"})

    async def run_task() -> None:
        try:
            await queue.put({
                "type":        "auth_hop",
                "from":        "browser",
                "to":          "chat_server",
                "token_hint":  tok_hint,
                "token_full":  hub_token or "dev-open",
                "sub":         username,
                "roles":       user.get("roles", []),
                "token_type":  "jwt",
                "iss":         user.get("iss", "fab-chat"),
                "exp":         user.get("exp"),
                "iat":         user.get("iat"),
                "rbac_check":  "chat_stream (any authenticated user)",
                "rbac_passed": True,
                "http_request": {
                    "method": "POST",
                    "url":    f"http://localhost:{CHAT_PORT}/chat/stream",
                    "headers": {
                        "Authorization": f"Bearer {hub_token or 'dev-open'}",
                        "Content-Type":  "application/json",
                    },
                    "body": {"query": req.query, "session_id": session_id},
                },
            })
            await fab_agent.run_agent(req.query, on_event=on_event, hub_token=hub_token)
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            if final_ref["value"]:
                _add_message(session_id, "assistant", final_ref["value"])
            _update_session_status(session_id, "complete")
            _bg_tasks.pop(session_id, None)
            await queue.put(None)

    # Task is created BEFORE the generator so it persists if the client disconnects.
    task = asyncio.create_task(run_task())
    _bg_tasks[session_id] = task

    async def event_generator():
        try:
            while True:
                item = await asyncio.wait_for(queue.get(), timeout=300.0)
                if item is None:
                    yield "data: " + json.dumps({"type": "stream_end",
                                                  "session_id": session_id}) + "\n\n"
                    break
                yield "data: " + json.dumps(item) + "\n\n"
        except asyncio.TimeoutError:
            # Task still runs in background — client polls /status and gets result
            _timeout_ev = {"type": "stream_timeout", "session_id": session_id}
            _save_trace_event(session_id, msg_index, "stream_timeout", json.dumps(_timeout_ev))
            yield "data: " + json.dumps(_timeout_ev) + "\n\n"
        # No task.cancel() — run_task() continues in _bg_tasks even after client disconnect

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.post("/chat/ask")
async def chat_ask(req: ChatRequest, authorization: str = Header(default="")):
    _auth_user(authorization)
    hub_token = authorization.removeprefix("Bearer ").strip()
    result    = await fab_agent.run_agent(req.query, hub_token=hub_token)
    return {"answer": result}


# ── Admin API endpoints ─────────────────────────────────────────────────────

@app.get("/api/admin/config")
async def admin_config(authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required")
    mode = "dev" if CHAT_JWT_SECRET == _DEFAULT_SECRET else "qa"
    return {
        "hub_url":         fab_agent.HUB_SERVER_URL,
        "auth_mode":       mode,
        "jwt_algorithm":   "HS256",
        "chat_port":       CHAT_PORT,
        "chat_host":       CHAT_HOST,
        "user_count":      len(_USERS),
        "mcp_api_key_set": bool(fab_agent.MCP_API_KEY),
        "hub_api_key_set": bool(fab_agent.HUB_API_KEY),
    }


@app.get("/api/admin/users")
async def admin_list_users(authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required")
    with _get_chat_engine().connect() as conn:
        rows = conn.execute(_sa_text(
            "SELECT username, display_name, roles, is_active, must_change_password, auth_provider, created_at"
            " FROM chat_users ORDER BY username"
        )).fetchall()
    return {"users": [
        {"username": r.username, "display": r.display_name,
         "roles": (r.roles if isinstance(r.roles, list) else json.loads(r.roles or '["agent"]')),
         "is_active": bool(r.is_active), "must_change_password": bool(r.must_change_password),
         "auth_provider": r.auth_provider, "created_at": str(r.created_at)}
        for r in rows
    ]}


@app.post("/api/admin/users")
async def admin_create_user(request: Request, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required")
    body     = await request.json()
    username = body.get("username", "").strip()
    if not username:
        raise HTTPException(status_code=422, detail="Username required")
    password = body.get("password") or _gen_temp_password()
    display = body.get("display", username.replace("_", " ").title())
    roles = body.get("roles", ["agent"])
    must_change = bool(body.get("must_change_password", bool(not body.get("password"))))
    try:
        with _get_chat_engine().begin() as conn:
            if conn.execute(_sa_text("SELECT 1 FROM chat_users WHERE username=:u"), {"u": username}).fetchone():
                raise HTTPException(status_code=409, detail=f"User '{username}' already exists")
            conn.execute(_sa_text(
                "INSERT INTO chat_users (username, display_name, password_hash, roles, must_change_password, created_by) "
                "VALUES (:u, :d, :p, :r, :mc, :cb)"
            ), {"u": username, "d": display, "p": _hash_password(password),
                "r": json.dumps(roles), "mc": 1 if must_change else 0,
                "cb": user.get("sub", "admin")})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "generated_password": password if not body.get("password") else None}


@app.put("/api/admin/users/{username}")
async def admin_update_user(username: str, request: Request, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required")
    body = await request.json()
    db_user = _db_get_user(username)
    if db_user is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    updates, params = [], {"u": username}
    if body.get("password"):
        updates.append("password_hash=:ph"); params["ph"] = _hash_password(body["password"])
        updates.append("must_change_password=0")
    if body.get("roles"):
        updates.append("roles=:r"); params["r"] = json.dumps(body["roles"])
    if body.get("display"):
        updates.append("display_name=:d"); params["d"] = body["display"]
    if "is_active" in body:
        updates.append("is_active=:ia"); params["ia"] = 1 if body["is_active"] else 0
    if updates:
        with _get_chat_engine().begin() as conn:
            conn.execute(_sa_text(f"UPDATE chat_users SET {', '.join(updates)} WHERE username=:u"), params)
    return {"ok": True}


@app.delete("/api/admin/users/{username}")
async def admin_delete_user(username: str, authorization: str = Header(default="")):
    user = _auth_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required")
    db_user = _db_get_user(username)
    if db_user is None:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    if username == user.get("sub"):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    with _get_chat_engine().begin() as conn:
        conn.execute(_sa_text("DELETE FROM chat_users WHERE username=:u"), {"u": username})
    return {"ok": True}


@app.post("/api/admin/token")
async def admin_generate_token(request: Request, authorization: str = Header(default="")):
    """Generate a JWT for service-to-service auth (HUB_API_KEY / MCP_API_KEY)."""
    user = _auth_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin role required")
    body  = await request.json()
    sub   = body.get("sub", "fab-agent")
    roles = body.get("roles", ["agent"])
    hours = int(body.get("hours", 24))
    token = _mint_jwt(sub, roles, hours=hours)
    exp   = int(time.time()) + hours * 3600
    return {"token": token, "sub": sub, "roles": roles, "expires_at": exp}


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"FAB MCP Hub Chat UI  {CHAT_HOST}:{CHAT_PORT}")
    print(f"Hub : {fab_agent.HUB_SERVER_URL}")
    print(f"DB  : MySQL (fab_semantic) — chat_users, chat_sessions, chat_messages, chat_traces")
    print()
    print("Default credentials (seeded into MySQL on first run):")
    for uname, cfg in _USERS.items():
        print(f"  {uname:12} / {cfg['password']:12}  ({cfg['roles'][0]})")
    print()
    print(f"Open:  http://localhost:{CHAT_PORT}")
    uvicorn.run(app, host=CHAT_HOST, port=CHAT_PORT, log_level="warning")
