"""
Shared .env loader used by all standalone scripts in this project.

Reads src/distributed scraper/.env into os.environ. If the file does not
exist it is created with placeholder values so the user knows what to fill in.
"""

import os
from pathlib import Path

_ENV_FILE = Path(__file__).parents[1] / "distributed scraper" / ".env"

_ENV_TEMPLATE = """\
DATABASE_URL=postgresql://postgres:yourpassword@localhost/deckgen
API_KEY=your-api-key
SCRAPER_API_URL=http://127.0.0.1:8000
"""


def load_env() -> None:
    if not _ENV_FILE.exists():
        # Hosted environments (e.g. Fly.io) inject these directly via the
        # platform config/secrets — there's no .env to load and no placeholder
        # to write. Detect that by the presence of API_KEY and bail out quietly.
        if "API_KEY" in os.environ:
            return
        _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ENV_FILE.write_text(_ENV_TEMPLATE)
        print(f"Created {_ENV_FILE} with placeholder values — please fill in real credentials.")
        return
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
