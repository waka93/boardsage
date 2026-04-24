import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "BoardSage/1.0 (board game rules assistant)"

_SUBREDDIT_RE = re.compile(r"^[a-zA-Z0-9_]+$")
_THREAD_ID_RE = re.compile(r"^[a-zA-Z0-9]+$")

_last_request_time = 0.0


def _get(url: str) -> dict:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < 2.0:
        time.sleep(2.0 - elapsed)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    _last_request_time = time.monotonic()
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _validate_subreddit(subreddit: str) -> str:
    if not _SUBREDDIT_RE.match(subreddit):
        raise ValueError(f"Invalid subreddit name: {subreddit!r}")
    return subreddit


def _validate_thread_id(thread_id: str) -> str:
    if not _THREAD_ID_RE.match(thread_id):
        raise ValueError(f"Invalid thread ID: {thread_id!r}")
    return thread_id


def search_subreddit(subreddit: str, query: str, limit: int = 5) -> list[dict]:
    _validate_subreddit(subreddit)
    encoded_query = urllib.parse.quote(query)
    url = (
        f"https://www.reddit.com/r/{subreddit}/search.json"
        f"?q={encoded_query}&restrict_sr=on&limit={limit}&sort=relevance"
    )
    try:
        data = _get(url)
    except Exception as e:
        print(f"  [reddit] search r/{subreddit} failed: {e}")
        return []

    results = []
    for post in data.get("data", {}).get("children", []):
        d = post.get("data", {})
        thread_id = d.get("id", "")
        if not thread_id or not _THREAD_ID_RE.match(thread_id):
            continue
        results.append({
            "thread_id": thread_id,
            "subject": d.get("title", ""),
            "subreddit": d.get("subreddit", subreddit),
            "num_comments": d.get("num_comments", 0),
            "url": f"https://www.reddit.com{d.get('permalink', '')}",
        })
    return results


def fetch_thread(thread_id: str, subreddit: str, cache_dir: str) -> dict:
    _validate_thread_id(thread_id)
    _validate_subreddit(subreddit)
    os.makedirs(f"{cache_dir}/threads", exist_ok=True)
    cache_file = f"{cache_dir}/threads/{thread_id}.json"

    url = f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}.json"
    try:
        data = _get(url)
    except Exception as e:
        print(f"  [reddit] fetch thread {thread_id} failed: {e}")
        return {"fetched": False, "thread_id": thread_id, "reason": "fetch_failed"}

    if not isinstance(data, list) or len(data) < 2:
        return {"fetched": False, "thread_id": thread_id, "reason": "unexpected_format"}

    post_data = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
    subject = post_data.get("title", "")
    selftext = post_data.get("selftext", "")
    num_comments = post_data.get("num_comments", 0)
    permalink = post_data.get("permalink", "")

    posts = []
    if selftext:
        posts.append({
            "id": thread_id,
            "author": post_data.get("author", "[deleted]"),
            "body": selftext,
            "score": post_data.get("score", 0),
            "created_utc": post_data.get("created_utc", 0),
        })

    for comment in data[1].get("data", {}).get("children", []):
        c = comment.get("data", {})
        if comment.get("kind") != "t1":
            continue
        body = c.get("body", "")
        if not body or body == "[deleted]":
            continue
        posts.append({
            "id": c.get("id", ""),
            "author": c.get("author", "[deleted]"),
            "body": body,
            "score": c.get("score", 0),
            "created_utc": c.get("created_utc", 0),
        })

    result = {
        "thread_id": thread_id,
        "subject": subject,
        "subreddit": subreddit,
        "num_comments": num_comments,
        "url": f"https://www.reddit.com{permalink}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    }
    with open(cache_file, "w") as f:
        json.dump(result, f, indent=2)

    return {
        "fetched": True,
        "thread_id": thread_id,
        "subject": subject,
        "num_comments": num_comments,
        "posts_count": len(posts),
    }


def read_cached_thread(thread_id: str, cache_dir: str) -> dict | None:
    _validate_thread_id(thread_id)
    cache_file = f"{cache_dir}/threads/{thread_id}.json"
    if not os.path.exists(cache_file):
        return None
    try:
        with open(cache_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None


def update_index(cache_dir: str, thread_id: str, subject: str, subreddit: str, num_comments: int) -> dict:
    _validate_thread_id(thread_id)
    _validate_subreddit(subreddit)
    os.makedirs(cache_dir, exist_ok=True)
    index_file = f"{cache_dir}/index.json"
    index = {}
    if os.path.exists(index_file):
        with open(index_file) as f:
            index = json.load(f)

    index[str(thread_id)] = {
        "subject": subject,
        "subreddit": subreddit,
        "num_comments": int(num_comments),
        "url": f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}/",
        "last_fetched": datetime.now(timezone.utc).isoformat(),
    }
    with open(index_file, "w") as f:
        json.dump(index, f, indent=2)
    return {"updated": True, "index_size": len(index)}


def check_stale(thread_id: str, cache_dir: str) -> dict:
    _validate_thread_id(thread_id)
    cache_file = f"{cache_dir}/threads/{thread_id}.json"
    if not os.path.exists(cache_file):
        return {"stale": True, "reason": "not_cached"}
    try:
        with open(cache_file) as f:
            cached = json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {"stale": True, "reason": "corrupt_cache"}
    return {
        "stale": False,
        "cached_num_comments": cached.get("num_comments", 0),
    }
