from __future__ import annotations

import asyncio

from .bot import run_bot


def main() -> None:
    try:
        asyncio.run(run_bot())
    except RuntimeError as exc:
        raise SystemExit(f"hixbot: {exc}") from exc


if __name__ == "__main__":
    main()
