"""python -m app.seed [--demo]"""

import asyncio
import logging
import sys

from app.core.database import SessionLocal, engine
from app.seed.base import seed_base

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


async def main(demo: bool) -> None:
    async with SessionLocal() as db:
        await seed_base(db)
    if demo:
        from app.main import app
        from app.seed.demo import seed_demo

        await seed_demo(app)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main("--demo" in sys.argv))
