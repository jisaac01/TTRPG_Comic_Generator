"""Launch the localhost web app on 127.0.0.1:8765."""

from __future__ import annotations

import uvicorn

from web.app import WEB_HOST, WEB_PORT, create_app


def main() -> None:
    uvicorn.run(create_app(), host=WEB_HOST, port=WEB_PORT)


if __name__ == "__main__":
    main()
