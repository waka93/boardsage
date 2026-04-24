---
name: boardgame-forum-search
description: >
  Board game forum knowledge search. Searches BoardGameGeek (BGG) community discussions
  to answer rules questions that aren't covered by official rulebooks, FAQs, or errata.
  Caches thread content locally and updates only when new posts are detected.
  Use this skill when: the boardgame-rules skill couldn't find an answer in official documents;
  the user asks about a corner case, community interpretation, or unofficial ruling; the user
  explicitly asks what the BGG community says; or the question involves an edge case that 
  official documents are silent on. Requires the game name to be known from context.
---

# Board Game Forum Search (BGG)

You answer board game rules questions by searching BoardGameGeek community discussions.
Use this skill **after** the `boardgame-rules` skill has already failed to find an answer in official documents.

## What you need before starting

- **Game name** — must be known from context. If not, ask.
- **The unanswered question** — carry it over from the failed official-rules lookup.

## How the data layer works

BGG thread content is cached locally at:
```
{repo}/knowledge/{game_slug}/bgg/
  index.json           — known threads: {thread_id: {subject, numposts, url, last_fetched}}
  threads/
    {thread_id}.json   — full cached thread: {subject, numposts, posts: [{postdate, body}]}
```

The fetch module lives at:
```
{repo}/core/bgg_fetch.py
```

Run it via: `python3 -c "from core import bgg_fetch; ..."` or import it directly from Python code.

### Game slug normalization
Lowercase, remove spaces and punctuation: "GrimCoven" → "grimcoven", "Grim Coven" → "grimcoven"

## Step 1 — Search local cache first

Before going online, check whether the local cache already has relevant threads.

Read the index:
```bash
cat {repo}/knowledge/{game_slug}/bgg/index.json
```

If the index exists and has threads, grep the cached thread files for keywords from the question:
```bash
grep -ril "{keyword1}\|{keyword2}" {repo}/knowledge/{game_slug}/bgg/threads/
```

For any matching thread files, read them with:
```bash
python3 -m core.bgg_fetch read-thread {thread_id} {repo}/knowledge/{game_slug}/bgg
```

**If the cached content answers the question** → skip to Step 4. No network calls needed.

**If the cache is empty or no cached thread is relevant** → continue to Step 2.

## Step 2 — Search BGG for new threads

Use WebSearch to find BGG thread URLs matching the question:

```
site:boardgamegeek.com/thread {game_name} {2-3 keywords from the question}
```

From the search results, extract thread IDs from URLs like:
`boardgamegeek.com/thread/3657911/devourer-eat-question` → thread ID = `3657911`

Collect the 3-5 most relevant thread IDs. If the search returns no results, try broader keywords.

## Step 3 — Check cache freshness and fetch if needed

For each thread ID:

**a) Check if cached and fresh:**
```bash
python3 -m core.bgg_fetch check-stale {thread_id} {repo}/knowledge/{game_slug}/bgg
```
Returns `{"stale": false}` → use cached content, skip fetching.
Returns `{"stale": true}` → must fetch.

**b) Fetch if stale or not cached:**
```bash
python3 -m core.bgg_fetch fetch-thread {thread_id} {repo}/knowledge/{game_slug}/bgg
```
This downloads all posts and saves to the cache automatically.

**c) Update the index:**
```bash
python3 -m core.bgg_fetch update-index \
  {repo}/knowledge/{game_slug}/bgg {thread_id} "{subject}" {numposts}
```

## Step 4 — Read cached thread content

```bash
python3 -m core.bgg_fetch read-thread {thread_id} {repo}/knowledge/{game_slug}/bgg
```

This returns the full thread JSON. The `posts` array has:
- `postdate` — ISO timestamp
- `body` — post text (may contain BGG quote markup like `[q="username"]...[/q]`)

Read the posts and find content relevant to the question.

## Step 5 — Answer the question

Once you have relevant thread content:

- Lead with the community consensus or most authoritative answer (often the publisher/designer's response, or the most upvoted reasoning)
- Quote the key post(s) directly
- Cite the source: thread title + BGG thread URL
- Note that this is **community discussion, not official ruling** — flag if there's disagreement between posts
- If posts conflict, present both sides and note there's no consensus

If after reading all fetched threads you still can't answer the question, say so honestly and suggest the user post a new thread on BGG.

## Staleness policy

- A thread is considered **fresh** if its `numposts` matches the cached value
- Always check staleness before answering — BGG threads can get new replies
- Do not re-fetch a thread that is fresh; the cache is the source of truth

## Paths reference

When running commands, substitute these concrete values:
- `{repo}` = `/home/geniuswrt/repo/boardsage`
- `{game_slug}` = normalized game name (lowercase, no spaces/punctuation)

## BGG API details (for reference)

The fetch script uses BGG's internal JSON API — no auth token needed:
- Thread metadata: `https://api.geekdo.com/api/threads/{thread_id}`
- Thread posts: `https://api.geekdo.com/api/articles?threadid={thread_id}&page=1&count=100`
- Forum list: `https://api.geekdo.com/api/forums?objectid={bgg_id}&objecttype=thing`

Thread IDs come from BGG thread URLs: `boardgamegeek.com/thread/{ID}/...`
