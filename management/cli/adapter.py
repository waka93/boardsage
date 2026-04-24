import argparse
import sys

from core.manager import KnowledgeManager


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "never"
    return iso[:10]


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
    game = manager.get_game(args.game)
    if game is None:
        print(f"Error: game not found: {args.game!r}", file=sys.stderr)
        return 1
    files_str = ", ".join(game.rulebook_files) if game.rulebook_files else "(none)"
    if len(files_str) > 60:
        files_str = files_str[:57] + "..."
    print(f"Game:           {game.slug}")
    print(f"Rulebook files: {len(game.rulebook_files)}  ({files_str})")
    print(f"BGG threads:    {game.bgg_thread_count}  (last synced: {_fmt_date(game.bgg_last_synced)})")
    print(f"Reddit threads: {game.reddit_thread_count}  (last synced: {_fmt_date(game.reddit_last_synced)})")
    return 0


def cmd_remove(manager: KnowledgeManager, args) -> int:
    game = manager.get_game(args.game)
    if game is None:
        print(f"Error: game not found: {args.game!r}", file=sys.stderr)
        return 1
    slug = game.slug
    if not args.yes:
        answer = input(
            f"Remove {slug}? This will delete assets/{slug}/ and knowledge/{slug}/. [y/N]: "
        ).strip().lower()
        if answer != "y":
            print("Cancelled.")
            return 0
    manager.remove_game(args.game)
    print(f"Removed {slug}.")
    return 0


def cmd_refresh(manager: KnowledgeManager, args) -> int:
    game = manager.get_game(args.game)
    if game is None:
        print(f"Error: game not found: {args.game!r}", file=sys.stderr)
        return 1
    slug = game.slug
    source = args.source
    exit_code = 0

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
        prog="python -m management.cli",
        description="BoardSage knowledge base management",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all games with rulebook and cache stats")

    info_p = sub.add_parser("info", help="Show details for one game")
    info_p.add_argument("game", help="Game name or slug")

    remove_p = sub.add_parser("remove", help="Delete game data")
    remove_p.add_argument("game", help="Game name or slug")
    remove_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    refresh_p = sub.add_parser("refresh", help="Re-sync BGG and/or Reddit caches")
    refresh_p.add_argument("game", help="Game name or slug")
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
