from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .formatting import (
    format_bytes,
    format_duration,
    format_elapsed_duration,
    format_resolution,
)


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
TEMPLATES.env.filters["duration"] = format_duration
TEMPLATES.env.filters["elapsed_duration"] = format_elapsed_duration
TEMPLATES.env.filters["filesize"] = format_bytes
TEMPLATES.env.filters["resolution"] = format_resolution
