"""Operational commands.

Usage::

    python -m app.cli seed    # load the verified exchanges and instruments
"""

import argparse
import asyncio

from app.db.session import AsyncSessionLocal, engine
from app.market_data.seed import seed_market_reference_data


async def _seed() -> None:
    try:
        async with AsyncSessionLocal() as session:
            counts = await seed_market_reference_data(session)
            await session.commit()
    finally:
        await engine.dispose()
    print(
        "Seeded reference data: "
        f"{counts['exchanges']} exchanges, {counts['instruments']} instruments, "
        f"{counts['provider_symbols']} provider symbols (existing rows left untouched)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="Idempotently load exchange and instrument reference data")
    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(_seed())


if __name__ == "__main__":
    main()
