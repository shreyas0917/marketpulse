"""Entry point: `python -m app {api|worker|producer}`."""

from __future__ import annotations

import sys

from app.common.config import Settings
from app.common.logging import configure_logging

SERVICES = ("api", "worker", "producer")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in SERVICES:
        print(f"usage: python -m app {{{'|'.join(SERVICES)}}}", file=sys.stderr)
        return 2
    service = args[0]
    settings = Settings()
    configure_logging(service, settings.log_level)
    if service == "api":
        import uvicorn

        from app.api.main import create_app

        uvicorn.run(
            create_app(settings),
            host="0.0.0.0",  # noqa: S104 - must listen on the container IP
            port=settings.api_port,
            log_config=None,
            access_log=False,
        )
    elif service == "worker":
        from app.worker.main import main as worker_main

        worker_main(settings)
    else:
        from app.producer.main import main as producer_main

        producer_main(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
