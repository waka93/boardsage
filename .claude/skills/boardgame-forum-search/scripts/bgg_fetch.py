#!/usr/bin/env python3
"""
Fetch and cache BGG thread content for boardgame-forum-search skill.

Usage:
  python3 bgg_fetch.py lookup-game <game_name>
      -> prints JSON: {bgg_id, name}

  python3 bgg_fetch.py fetch-thread <thread_id> <cache_dir>
      -> fetches thread, saves to <cache_dir>/threads/<thread_id>.json
      -> prints JSON: {fetched: true/false, reason: ..., posts: [...]}

  python3 bgg_fetch.py check-stale <thread_id> <cache_dir>
      -> prints JSON: {stale: true/false, cached_numposts, live_numposts}

  python3 bgg_fetch.py update-index <cache_dir> <thread_id> <subject> <numposts>
      -> updates <cache_dir>/index.json with thread metadata
"""

import json
import sys
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())

def lookup_game(name):
    # BGG's search APIs require auth; use DuckDuckGo HTML to find the BGG game page URL,
    # which embeds the BGG ID: boardgamegeek.com/boardgame/{id}/slug
    encoded = urllib.parse.quote(f"{name} site:boardgamegeek.com/boardgame")
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    try:
        import re as _re
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode(errors="replace")
        ids = _re.findall(r"boardgamegeek\.com/boardgame/(\d+)", html)
        if ids:
            return {"bgg_id": int(ids[0]), "name": name}
    except Exception:
        pass

    return {"bgg_id": None, "name": name, "error": "not_found"}

def get_forums(bgg_id):
    url = f"https://api.geekdo.com/api/forums?objectid={bgg_id}&objecttype=thing"
    data = get(url)
    forums = {}
    for f in data.get("forums", []):
        forums[f["title"]] = {
            "forumid": f["forumid"],
            "forumuid": f["forumuid"],
            "numposts": f["numposts"],
            "numthreads": f["numthreads"],
        }
    return forums

def get_thread_meta(thread_id):
    url = f"https://api.geekdo.com/api/threads/{thread_id}"
    return get(url)

def get_game_files(bgg_id):
    url = f"https://api.geekdo.com/api/files?objectid={bgg_id}&objecttype=thing&nosession=1"
    try:
        data = get(url)
        return data.get("files", [])
    except Exception:
        return []


def get_forum_threads(forum_id, pages=2):
    threads = []
    for page in range(1, pages + 1):
        url = f"https://api.geekdo.com/api/threads?forumid={forum_id}&page={page}&count=50"
        try:
            data = get(url)
            batch = data.get("threads", [])
            if not batch:
                break
            for t in batch:
                threads.append({
                    "thread_id": t.get("id"),
                    "subject": t.get("subject", ""),
                    "numposts": t.get("numposts", 0),
                })
        except Exception:
            break
    return threads


def get_thread_posts(thread_id):
    posts = []
    page = 1
    while True:
        url = f"https://api.geekdo.com/api/articles?threadid={thread_id}&page={page}&count=100"
        data = get(url)
        articles = data.get("articles", [])
        if not articles:
            break
        for a in articles:
            posts.append({
                "id": a.get("id"),
                "author_id": a.get("author"),
                "postdate": a.get("postdate"),
                "editdate": a.get("editdate"),
                "body": a.get("body", ""),
            })
        # Check if there are more pages
        if len(articles) < 100:
            break
        page += 1
    return posts

def fetch_thread(thread_id, cache_dir):
    os.makedirs(f"{cache_dir}/threads", exist_ok=True)
    cache_file = f"{cache_dir}/threads/{thread_id}.json"

    meta = get_thread_meta(thread_id)
    numposts = meta.get("numposts", 0)
    subject = meta.get("subject", "")

    posts = get_thread_posts(thread_id)
    result = {
        "thread_id": thread_id,
        "subject": subject,
        "numposts": numposts,
        "url": f"https://boardgamegeek.com/thread/{thread_id}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "posts": posts,
    }
    with open(cache_file, "w") as f:
        json.dump(result, f, indent=2)

    return {"fetched": True, "thread_id": thread_id, "subject": subject,
            "numposts": numposts, "posts_count": len(posts)}

def check_stale(thread_id, cache_dir):
    cache_file = f"{cache_dir}/threads/{thread_id}.json"
    if not os.path.exists(cache_file):
        return {"stale": True, "reason": "not_cached"}

    with open(cache_file) as f:
        cached = json.load(f)

    cached_numposts = cached.get("numposts", 0)
    meta = get_thread_meta(thread_id)
    live_numposts = meta.get("numposts", 0)

    stale = live_numposts != cached_numposts
    return {
        "stale": stale,
        "cached_numposts": cached_numposts,
        "live_numposts": live_numposts,
        "subject": meta.get("subject", ""),
    }

def update_index(cache_dir, thread_id, subject, numposts):
    os.makedirs(cache_dir, exist_ok=True)
    index_file = f"{cache_dir}/index.json"
    index = {}
    if os.path.exists(index_file):
        with open(index_file) as f:
            index = json.load(f)

    index[str(thread_id)] = {
        "subject": subject,
        "numposts": int(numposts),
        "url": f"https://boardgamegeek.com/thread/{thread_id}",
        "last_fetched": datetime.now(timezone.utc).isoformat(),
    }
    with open(index_file, "w") as f:
        json.dump(index, f, indent=2)
    return {"updated": True, "index_size": len(index)}

def read_cached_thread(thread_id, cache_dir):
    cache_file = f"{cache_dir}/threads/{thread_id}.json"
    if not os.path.exists(cache_file):
        return None
    with open(cache_file) as f:
        return json.load(f)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "lookup-game":
        print(json.dumps(lookup_game(sys.argv[2])))

    elif cmd == "fetch-thread":
        thread_id = int(sys.argv[2])
        cache_dir = sys.argv[3]
        print(json.dumps(fetch_thread(thread_id, cache_dir)))

    elif cmd == "check-stale":
        thread_id = int(sys.argv[2])
        cache_dir = sys.argv[3]
        print(json.dumps(check_stale(thread_id, cache_dir)))

    elif cmd == "update-index":
        cache_dir, thread_id, subject, numposts = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        print(json.dumps(update_index(cache_dir, int(thread_id), subject, int(numposts))))

    elif cmd == "read-thread":
        thread_id = int(sys.argv[2])
        cache_dir = sys.argv[3]
        data = read_cached_thread(thread_id, cache_dir)
        print(json.dumps(data) if data else "null")

    elif cmd == "get-forums":
        bgg_id = sys.argv[2]
        print(json.dumps(get_forums(bgg_id)))

    else:
        print(json.dumps({"error": f"Unknown command: {cmd}"}))
        sys.exit(1)
