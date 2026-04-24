import asyncio
import os
import secrets
from collections import defaultdict

import aiohttp
from aiohttp import web

from core.engine import RulesEngine

MAX_HISTORY = 20
WECHAT_MSG_LIMIT = 2048
_CHUNK_BODY_SIZE = 2038
_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
_CODE2SESSION_TIMEOUT = aiohttp.ClientTimeout(total=10)


class WeChatAdapter:
    def __init__(self, engine: RulesEngine, app_id: str, app_secret: str) -> None:
        self._engine = engine
        self._app_id = app_id
        self._app_secret = app_secret
        self._sessions: dict[str, str] = {}
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._user_locks: dict[str, asyncio.Lock] = {}

        self.app = web.Application(client_max_size=64 * 1024)
        self.app.router.add_post("/wechat/session", self._handle_session)
        self.app.router.add_post("/wechat/chat", self._handle_chat)

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
        self._sessions[token] = openid
        return web.json_response({"session_token": token})

    async def _handle_chat(self, request: web.Request) -> web.Response:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return web.json_response({"error": "Invalid or missing session token"}, status=401)
        token = auth[len("Bearer "):]
        openid = self._sessions.get(token)
        if not openid:
            return web.json_response({"error": "Invalid or missing session token"}, status=401)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            return web.json_response({"error": "missing 'question'"}, status=400)

        self._user_locks.setdefault(openid, asyncio.Lock())

        async with self._user_locks[openid]:
            self._history[openid].append({"role": "user", "content": question})
            snapshot = list(self._history[openid])
            loop = asyncio.get_running_loop()
            try:
                reply = await loop.run_in_executor(None, lambda: self._engine.ask(snapshot))
            except Exception as e:
                self._history[openid].pop()
                print(f"[wechat] engine error: {e}")
                return web.json_response({"error": "internal error"}, status=500)
            self._history[openid].append({"role": "assistant", "content": reply})
            if len(self._history[openid]) > MAX_HISTORY * 2:
                self._history[openid] = self._history[openid][-MAX_HISTORY * 2:]

        return web.json_response({"chunks": self._chunk_reply(reply)})

    @staticmethod
    def _chunk_reply(text: str) -> list[str]:
        if len(text) <= WECHAT_MSG_LIMIT:
            return [text]
        raw = [text[i:i + _CHUNK_BODY_SIZE] for i in range(0, len(text), _CHUNK_BODY_SIZE)]
        total = len(raw)
        return [f"{c} ({i + 1}/{total})" for i, c in enumerate(raw)]


def main() -> None:
    app_id = os.environ["WECHAT_APP_ID"]
    app_secret = os.environ["WECHAT_APP_SECRET"]
    try:
        port = int(os.environ.get("WECHAT_PORT", "8080"))
    except ValueError:
        raise SystemExit("WECHAT_PORT must be an integer")

    engine = RulesEngine()
    adapter = WeChatAdapter(engine, app_id, app_secret)
    web.run_app(adapter.app, port=port)


if __name__ == "__main__":
    main()
