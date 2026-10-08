"""Load settings from a local .env file (KEY=VALUE lines) so secrets stay out of the repo."""

import os
from pathlib import Path


def load_dotenv(path=Path(__file__).parent / ".env"):
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


load_dotenv()
