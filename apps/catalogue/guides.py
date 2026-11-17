"""Industry guides and Creator Essentials entry points, from the founder's task bank.

They're ordered views of tasks that already exist in the catalogue: they never add a
task, a price or an allowance. Made with tools/catalogue/task_bank_to_manifest.py --guides.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings

GUIDES_FILE = Path(settings.BASE_DIR) / "catalogue" / "industry_guides_v2.1.json"


@lru_cache(maxsize=1)
def load() -> dict:
    try:
        return json.loads(GUIDES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"industries": [], "creator": []}


def industries() -> list[dict]:
    return load()["industries"]


def industry(tag: str) -> dict | None:
    return next((g for g in industries() if g["tag"] == tag), None)


def creator_entries() -> list[dict]:
    return load()["creator"]


def resolve(entries: list[dict], by_code: dict, key: str) -> list[dict]:
    """Attach the open catalogue versions to each step, keeping the guide's order.
    Tasks that aren't open (gated or switched off) are counted, not shown."""
    out = []
    for entry in entries:
        found = [by_code[c] for c in entry["codes"] if c in by_code]
        out.append({key: entry[key], "versions": found, "closed": len(entry["codes"]) - len(found)})
    return out
