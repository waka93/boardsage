import asyncio
import hashlib
import math
import os
import secrets
import time
from collections import defaultdict
from typing import NamedTuple

import aiohttp
from aiohttp import web

from core.engine import RulesEngine

MAX_HISTORY = 20
WECHAT_MSG_LIMIT = 2048
SESSION_TTL_SECONDS: int = 86400
_CLEANUP_INTERVAL_SECONDS: int = 3600
_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
_CODE2SESSION_TIMEOUT = aiohttp.ClientTimeout(total=10)


class _SessionEntry(NamedTuple):
    openid: str
    created_at: float


class WeChatAdapter:
    def __init__(
        self,
        engine: RulesEngine,
        app_id: str,
        app_secret: str,
        session_ttl: int = SESSION_TTL_SECONDS,
    ) -> None:
        self._engine = engine
        self._app_id = app_id
        self._app_secret = app_secret
        self._session_ttl = session_ttl
        self._sessions: dict[str, _SessionEntry] = {}
        self._history: defaultdict[str, list[dict]] = defaultdict(list)
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._cleanup_task: asyncio.Task | None = None

        self.app = web.Application(client_max_size=64 * 1024)
        self.app.router.add_post("/wechat/session", self._handle_session)
        self.app.router.add_post("/wechat/chat", self._handle_chat)
        self.app.on_startup.append(self._start_cleanup_task)
        self.app.on_cleanup.append(self._stop_cleanup_task)

    async def _start_cleanup_task(self, app: web.Application) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _stop_cleanup_task(self, app: web.Application) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            try:
                self._evict_expired()
            except Exception as e:
                print(f"[wechat] cleanup error type={type(e).__name__} detail={str(e)!r}")

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [t for t, s in self._sessions.items()
                   if now - s.created_at > self._session_ttl]
        for token in expired:
            del self._sessions[token]

        live_openids = {s.openid for s in self._sessions.values()}
        dead = {o for o in self._history if o not in live_openids}
        dead |= {o for o in self._user_locks if o not in live_openids}
        for openid in dead:
            self._history.pop(openid, None)
            self._user_locks.pop(openid, None)

    def _get_user_lock(self, openid: str) -> asyncio.Lock:
        if openid not in self._user_locks:
            self._user_locks[openid] = asyncio.Lock()
        return self._user_locks[openid]

    async def _exchange_code(self, code: str) -> str:
        params = {
            "appid": self._app_id,
            "secret": self._app_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        try:
            async with aiohttp.ClientSession(timeout=_CODE2SESSION_TIMEOUT) as session:
                async with session.get(_CODE2SESSION_URL, params=params) as resp:
                    data = await resp.json(content_type=None)
        except Exception:
            raise ValueError("WeChat API unreachable")
        try:
            errcode = int(data.get("errcode", 0))
        except (TypeError, ValueError):
            errcode = 0
        if errcode != 0:
            raise ValueError("WeChat authentication failed")
        openid = data.get("openid")
        if not openid:
            raise ValueError("no openid in WeChat response")
        return openid

    async def _handle_session(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        code = body.get("code")
        if not isinstance(code, str) or not code:
            return web.json_response({"error": "missing 'code'"}, status=400)

        try:
            openid = await self._exchange_code(code)
        except Exception as e:
            return web.json_response({"error": f"WeChat login failed: {e}"}, status=401)

        token = secrets.token_urlsafe(32)
        self._sessions[token] = _SessionEntry(openid=openid, created_at=time.monotonic())
        return web.json_response({"session_token": token})

    async def _handle_chat(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return web.json_response({"error": "Invalid or missing session token"}, status=401)
        token = auth[len("Bearer "):]
        entry = self._sessions.get(token)
        if not entry:
            return web.json_response({"error": "Invalid or missing session token"}, status=401)
        openid = entry.openid

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            return web.json_response({"error": "missing 'question'"}, status=400)
        if len(question) > 4096:
            return web.json_response({"error": "question too long"}, status=400)

        async with self._get_user_lock(openid):
            self._history[openid].append({"role": "user", "content": question})
            snapshot = list(self._history[openid])
            loop = asyncio.get_running_loop()
            try:
                reply = await loop.run_in_executor(None, lambda: self._engine.ask(snapshot))
            except Exception as e:
                self._history[openid].pop()
                openid_tag = hashlib.sha256(openid.encode()).hexdigest()[:8]
                print(f"[wechat] engine error openid={openid_tag} type={type(e).__name__} detail={str(e)!r}")
                return web.json_response({"error": "internal error"}, status=500)
            self._history[openid].append({"role": "assistant", "content": reply})
            if len(self._history[openid]) > MAX_HISTORY * 2:
                self._history[openid] = self._history[openid][-MAX_HISTORY * 2:]

        return web.json_response({"chunks": self._chunk_reply(reply)})

    @staticmethod
    def _chunk_reply(text: str) -> list[str]:
        if len(text) <= WECHAT_MSG_LIMIT:
            return [text]
        body_size = WECHAT_MSG_LIMIT - 10
        for _ in range(2):
            total_est = math.ceil(len(text) / body_size)
            d = len(str(total_est))
            # label " (i/total)": 1+1+d+1+d+1 = 2d+4 chars
            body_size = WECHAT_MSG_LIMIT - (2 * d + 4)
        raw = [text[i:i + body_size] for i in range(0, len(text), body_size)]
        total = len(raw)
        return [f"{c} ({i + 1}/{total})" for i, c in enumerate(raw)]


def main() -> None:
    app_id = os.environ["WECHAT_APP_ID"]
    app_secret = os.environ["WECHAT_APP_SECRET"]
    try:
        port = int(os.environ.get("WECHAT_PORT", "8080"))
    except ValueError:
        raise SystemExit("WECHAT_PORT must be an integer")
    try:
        session_ttl = int(os.environ.get("WECHAT_SESSION_TTL", str(SESSION_TTL_SECONDS)))
        if session_ttl <= 0:
            raise ValueError
    except ValueError:
        raise SystemExit("WECHAT_SESSION_TTL must be a positive integer")

    engine = RulesEngine()
    adapter = WeChatAdapter(engine, app_id, app_secret, session_ttl=session_ttl)
    web.run_app(adapter.app, port=port)


if __name__ == "__main__":
    main()
