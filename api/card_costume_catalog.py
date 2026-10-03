"""2D costume inventory backed by the packaged Live2D and icon resources."""

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


def ensure_available_costumes(user_data) -> bool:
    """Add newly packaged costumes without removing ownership or selections.

    This is repeatable so imported saves and future resource-pack revisions
    receive the same inventory as a fresh account, independently of old SQL
    migration numbers. Missing-resource ownership remains in the saved data.
    """
    from api.models import Costume

    changed = False
    by_character = {row.master_character_id: row for row in user_data.costume_list}
    for group in available_costume_inventory():
        character_id = group["master_character_id"]
        available = group["master_costume_ids"]
        if not available:
            continue
        row = by_character.get(character_id)
        if row is None:
            row = Costume(character_id, [], available[0])
            user_data.costume_list.append(row)
            by_character[character_id] = row
            changed = True
        owned = sorted(set(row.master_costume_ids) | set(available))
        if owned != row.master_costume_ids:
            row.master_costume_ids = owned
            changed = True
        if not row.master_costume_id:
            row.master_costume_id = available[0]
            changed = True
    return changed


def playable_costume_inventory(user_data) -> list[dict]:
    """Client projection only; unavailable saved ownership is never deleted."""
    available = {
        row["master_character_id"]: set(row["master_costume_ids"])
        for row in available_costume_inventory()
    }
    result = []
    for row in user_data.costume_list:
        owned = sorted(set(row.master_costume_ids) & available.get(row.master_character_id, set()))
        if not owned:
            continue
        result.append({
            "master_character_id": row.master_character_id,
            "master_costume_ids": owned,
            "master_costume_id": row.master_costume_id if row.master_costume_id in owned else owned[0],
        })
    return result


def costume_is_available(character_id: int, costume_id: int) -> bool:
    return any(
        row["master_character_id"] == character_id and costume_id in row["master_costume_ids"]
        for row in available_costume_inventory()
    )
