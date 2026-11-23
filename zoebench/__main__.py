"""Local drum design and photograph preparation service."""

import os
from pathlib import Path

from waitress import serve

from image_lab import create_app

from .web import install


def application(data=None):
    root = Path(data or os.environ.get("ZOEBENCH_DATA", "runtime-data"))
    app = create_app(root / "preparation")
    install(app, root / "drums")
    return app


if __name__ == "__main__":
    serve(application(), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), threads=4)
