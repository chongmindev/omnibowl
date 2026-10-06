"""CLI: uv run python -m omnibowl.run --stage <name> --book <pdf>."""

import argparse
import sys
from pathlib import Path

from . import config, db, parse, profile, tag


def book_id_for(conn, book: Path) -> int:
    row = conn.execute("SELECT id FROM books WHERE slug = ?", (book.stem,)).fetchone()
    if row is None:
        sys.exit(f"book '{book.stem}' has not been parsed; run --stage parse first")
    return row[0]


def run_parse(book, conn, cfg):
    book_id = parse.run(book, conn, cfg)
    tag.run(book_id, conn, cfg, profile.run(book_id, conn, cfg))


def run_profile(book, conn, cfg):
    book_id = book_id_for(conn, book)
    tag.run(book_id, conn, cfg, profile.run(book_id, conn, cfg))


def run_tag(book, conn, cfg):
    tag.run(book_id_for(conn, book), conn, cfg, profile.load(book.stem, cfg))


# parse = measure (parse.py) -> profile (profile.py) -> tag (tag.py). "profile" reruns
# the last two from the stored measurement layer; "tag" reruns only tagging, using the
# saved profile plus any hand corrections.
STAGES = {"parse": run_parse, "profile": run_profile, "tag": run_tag}
ORDER = ["parse", "candidates", "clues", "rank", "assemble"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="omnibowl.run")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stage", choices=ORDER + ["profile", "tag"])
    g.add_argument("--all", action="store_true")
    ap.add_argument("--book", required=True, type=Path)
    ap.add_argument("--db", type=Path, help="override [paths].db")
    args = ap.parse_args(argv)

    cfg = config.load()
    conn = db.connect(args.db or config.ROOT / cfg["paths"]["db"])
    for name in ORDER if args.all else [args.stage]:
        fn = STAGES.get(name)
        if fn is None:
            print(f"stage '{name}' is not implemented yet", file=sys.stderr)
            if args.all:
                break
            return 1
        print(f"[{name}] running")
        fn(args.book, conn, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
