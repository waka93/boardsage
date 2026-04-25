# WeChat Backend

HTTP backend for the BoardSage WeChat Mini Program. Authenticates users via WeChat's
`jscode2session` API and answers board game rules questions via the `RulesEngine`.

## Prerequisites

- Python 3.12+
- Dependencies installed: `uv pip install -e .` from the repo root
- A registered WeChat Mini Program with App ID and App Secret

## Configuration

Set three environment variables before starting the server:

| Variable         | Required | Default | Description                              |
|------------------|----------|---------|------------------------------------------|
| `WECHAT_APP_ID`  | Yes      | —       | Your Mini Program App ID                 |
| `WECHAT_APP_SECRET` | Yes   | —       | Your Mini Program App Secret             |
| `WECHAT_PORT`    | No       | `8080`  | TCP port the server listens on           |

Also set `ANTHROPIC_API_KEY` (read by `RulesEngine`):

```bash
export WECHAT_APP_ID=wx1234567890abcdef
export WECHAT_APP_SECRET=your_app_secret_here
export ANTHROPIC_API_KEY=sk-ant-...
export WECHAT_PORT=8080          # optional
```

Secrets are stored in `.secret/` by convention in this repo:

```bash
export WECHAT_APP_ID=$(cat .secret/wechat_app_id)
export WECHAT_APP_SECRET=$(cat .secret/wechat_app_secret)
export ANTHROPIC_API_KEY=$(cat .secret/claude)
```

## Starting the server

From the repo root:

```bash
# Via the entry-point shim
python wechat-backend/backend.py

# Or via the module directly
python -m platforms.wechat.adapter
```

The server listens on `0.0.0.0:<WECHAT_PORT>`. You should put a TLS-terminating
reverse proxy (nginx, Caddy, etc.) in front of it — WeChat requires HTTPS for
Mini Program backends in production.

---

## API reference

### POST /wechat/session

Exchange a WeChat login code for a session token. Call this once after `wx.login()`
in the Mini Program.

**Request body (JSON):**

```json
{ "code": "<code from wx.login()>" }
```

**Response 200:**

```json
{ "session_token": "abc123..." }
```

Store this token on the client. It must be sent as a Bearer token on every
subsequent `/wechat/chat` request.

**Error responses:**

| Status | Body                                      | Cause                              |
|--------|-------------------------------------------|------------------------------------|
| 400    | `{"error": "missing 'code'"}`             | `code` field absent or not a string|
| 401    | `{"error": "WeChat login failed: ..."}`   | WeChat API rejected the code       |

---

### POST /wechat/chat

Ask a board game rules question. Requires a valid session token.

**Headers:**

```
Authorization: Bearer <session_token>
Content-Type: application/json
```

**Request body (JSON):**

```json
{ "question": "Can I attack during setup in Grimcoven?" }
```

**Response 200:**

```json
{ "chunks": ["Full answer text here"] }
```

Long replies are split into multiple chunks (each ≤ 2048 characters) labelled
`(1/N)`, `(2/N)`, etc. Display them in order. A short reply returns a single-element
array with no label.

```json
{
  "chunks": [
    "During setup in Grimcoven... (1/3)",
    "...continued explanation... (2/3)",
    "...final part of the answer. (3/3)"
  ]
}
```

**Error responses:**

| Status | Body                                         | Cause                              |
|--------|----------------------------------------------|------------------------------------|
| 400    | `{"error": "invalid JSON body"}`             | Malformed JSON                     |
| 400    | `{"error": "missing 'question'"}`            | `question` field absent or empty   |
| 401    | `{"error": "Invalid or missing session token"}` | Missing/expired/unknown token   |
| 500    | `{"error": "internal error"}`                | Engine error (logged server-side)  |

---

## Mini Program integration

Typical call sequence from the Mini Program:

```javascript
// 1. Login once (e.g. on app launch)
wx.login({
  success({ code }) {
    wx.request({
      url: 'https://your-server/wechat/session',
      method: 'POST',
      data: { code },
      success({ data }) {
        wx.setStorageSync('session_token', data.session_token)
      }
    })
  }
})

// 2. Ask a question
const token = wx.getStorageSync('session_token')
wx.request({
  url: 'https://your-server/wechat/chat',
  method: 'POST',
  header: { Authorization: `Bearer ${token}` },
  data: { question: 'How does the Devourer work in Grimcoven?' },
  success({ data }) {
    // data.chunks is an array of strings — display each in order
    const answer = data.chunks.join('\n')
    console.log(answer)
  }
})
```

If `/wechat/chat` returns 401, the session has expired (server restarted). Re-run
`wx.login()` to obtain a fresh token.

---

## Session and history lifecycle

- Sessions are stored **in memory only**. A server restart invalidates all tokens —
  clients will receive 401 and must re-authenticate.
- Conversation history (up to 20 turns per user) is also in-memory and lost on restart.
- There is no explicit logout endpoint. Tokens remain valid until the server restarts.

## Concurrency

The server handles one request per user at a time. If the same user sends a second
question before the first completes, the second waits until the engine returns.
Requests from different users are handled concurrently.
