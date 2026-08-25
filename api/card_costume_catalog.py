"""Access card-evolution 2D costumes whose Live2D assets are packaged."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "card_costume_catalog.json"
)


@lru_cache(maxsize=1)
def load_card_costume_catalog() -> dict:
    if not CATALOG_PATH.is_file():
        return {"cards": {}, "available_costumes": []}
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1:
        raise RuntimeError("unsupported card costume catalog schema")
    return catalog


def available_costume_inventory() -> list[dict[str, object]]:
    return [
        {
            "master_character_id": int(group["master_character_id"]),
            "master_costume_ids": sorted(
                {int(item) for item in group.get("master_costume_ids", [])}
            ),
        }
        for group in load_card_costume_catalog().get("available_costumes", [])
    ]


def available_rewards_for_card(master_card_id: int) -> list[int]:
    record = load_card_costume_catalog().get("cards", {}).get(
        str(int(master_card_id)), {}
    )
    return sorted({int(item) for item in record.get("available_costume_ids", [])})
