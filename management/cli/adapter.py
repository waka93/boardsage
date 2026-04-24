import argparse
import sys

from core.manager import KnowledgeManager


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "never"
    return iso[:10]


def _parse_game_names(manager: KnowledgeManager, tokens: list[str]) -> list[str]:
    """
    Convert a list of CLI tokens into game name strings, handling multi-word names
    written without quotes (e.g. ["arkham", "horror"] → ["arkham horror"]).
    Commas are stripped so users can write: boardsage info grimcoven, arkham horror

    Multi-token phrases use exact/prefix match only (avoids fuzzy false positives
    like "grimcoven arkhamhorror" matching a single game). Single tokens use full
    matching including fuzzy, so typos like "arkam" find "arkhamhorror".
    """
    from core.utils import normalize as _norm

    tokens = [t.strip().strip(",") for t in tokens]
    tokens = [t for t in tokens if t]

    slug_norms = {_norm(g.slug): g.slug for g in manager.list_games()}

    def exact_phrase_match(phrase: str) -> bool:
        n = _norm(phrase)
        return any(slug == n or n in slug for slug in slug_norms)

    results: list[str] = []
    i = 0
    while i < len(tokens):
        # Try multi-token phrases (longest first) with exact/prefix match only.
        matched: str | None = None
        for j in range(len(tokens), i + 1, -1):
            phrase = " ".join(tokens[i:j])
            if exact_phrase_match(phrase):
                matched = phrase
                i = j
                break

        # Fall back to single token with full matching (includes fuzzy).
        if matched is None:
            matched = tokens[i]
            i += 1

        results.append(matched)
    return results


def cmd_list(manager: KnowledgeManager, _args) -> int:
    games = manager.list_games()
    if not games:
        print("No games in knowledge base.")
        return 0
    print(f"{'GAME':<20} {'RULEBOOK FILES':>15} {'BGG THREADS':>12} {'REDDIT THREADS':>15}")
    for g in games:
        print(f"{g.slug:<20} {len(g.rulebook_files):>15} {g.bgg_thread_count:>12} {g.reddit_thread_count:>15}")
    return 0


def cmd_info(manager: KnowledgeManager, args) -> int:
    names = _parse_game_names(manager, args.game)
    exit_code = 0
    for name in names:
        game = manager.get_game(name)
        if game is None:
            print(f"Error: game not found: {name!r}", file=sys.stderr)
            exit_code = 1
            continue
        files_str = ", ".join(game.rulebook_files) if game.rulebook_files else "(none)"
        if len(files_str) > 60:
            files_str = files_str[:57] + "..."
        print(f"Game:           {game.slug}")
        print(f"Rulebook files: {len(game.rulebook_files)}  ({files_str})")
        print(f"BGG threads:    {game.bgg_thread_count}  (last synced: {_fmt_date(game.bgg_last_synced)})")
        print(f"Reddit threads: {game.reddit_thread_count}  (last synced: {_fmt_date(game.reddit_last_synced)})")
        if len(names) > 1:
            print()
    return exit_code


def cmd_remove(manager: KnowledgeManager, args) -> int:
    names = _parse_game_names(manager, args.game)
    exit_code = 0

    resolved: list[tuple[str, str]] = []
    for name in names:
        game = manager.get_game(name)
        if game is None:
            print(f"Error: game not found: {name!r}", file=sys.stderr)
            exit_code = 1
        else:
            resolved.append((name, game.slug))

    if not resolved:
        return exit_code

    if not args.yes:
        slugs = ", ".join(slug for _, slug in resolved)
        dirs = " and ".join(
            f"assets/{slug}/ and knowledge/{slug}/" for _, slug in resolved
        )
        answer = input(f"Remove {slugs}? This will delete {dirs}. [y/N]: ").strip().lower()
        if answer != "y":
            print("Cancelled.")
            return 0

    for name, slug in resolved:
        manager.remove_game(name)
        print(f"Removed {slug}.")

    return exit_code


def cmd_refresh(manager: KnowledgeManager, args) -> int:
    names = _parse_game_names(manager, args.game)
    exit_code = 0

    for name in names:
        game = manager.get_game(name)
        if game is None:
            print(f"Error: game not found: {name!r}", file=sys.stderr)
            exit_code = 1
            continue
        slug = game.slug
        source = args.source

        if source in (None, "bgg"):
            print(f"Refreshing BGG cache for {slug}...", end=" ", flush=True)
            result = manager.refresh_bgg(slug)
            print(f"done ({result.threads_before} → {result.threads_after} threads)")
            for err in result.errors:
                print(f"  Warning: {err}", file=sys.stderr)
                exit_code = 2

        if source in (None, "reddit"):
            print(f"Refreshing Reddit cache for {slug}...", end=" ", flush=True)
            result = manager.refresh_reddit(slug)
            print(f"done ({result.threads_before} → {result.threads_after} threads)")
            for err in result.errors:
                print(f"  Warning: {err}", file=sys.stderr)
                exit_code = 2

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="boardsage",
        description="BoardSage knowledge base management",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all games with rulebook and cache stats")

    info_p = sub.add_parser("info", help="Show details for one or more games")
    info_p.add_argument("game", nargs="+", help="Game name(s) or slug(s)")

    remove_p = sub.add_parser("remove", help="Delete game data")
    remove_p.add_argument("game", nargs="+", help="Game name(s) or slug(s)")
    remove_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    refresh_p = sub.add_parser("refresh", help="Re-sync BGG and/or Reddit caches")
    refresh_p.add_argument("game", nargs="+", help="Game name(s) or slug(s)")
    refresh_p.add_argument(
        "--source",
        choices=["bgg", "reddit"],
        default=None,
        help="Restrict to one source (default: both)",
    )

    args = parser.parse_args()
    manager = KnowledgeManager()

    handlers = {
        "list": cmd_list,
        "info": cmd_info,
        "remove": cmd_remove,
        "refresh": cmd_refresh,
    }
    sys.exit(handlers[args.command](manager, args))


if __name__ == "__main__":
    main()
