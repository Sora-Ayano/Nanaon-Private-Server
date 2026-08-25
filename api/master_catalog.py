"""Typed access to master content recovered from the v2.4.0 client."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "master_catalog.json"


@lru_cache(maxsize=1)
def load_master_catalog() -> dict:
    if not CATALOG_PATH.exists():
        raise RuntimeError(
            "missing bundled data/master_catalog.json"
        )
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1:
        raise RuntimeError("unsupported master catalog schema")
    return catalog


def story_inventory() -> list[dict]:
    return [
        {
            "master_story_id": int(story["id"]),
            "master_story_part_ids": [int(item) for item in story["part_ids"]],
        }
        for story in load_master_catalog()["stories"]
        if story["part_ids"]
    ]


def music_ids() -> list[int]:
    return sorted(int(item["id"]) for item in load_master_catalog()["music"])


def music_shop_ids() -> list[int]:
    return sorted(int(item) for item in load_master_catalog()["music_shop_ids"])


def live_three_d_ids() -> list[int]:
    """Return protocol 3D Live ownership IDs from LiveMst."""
    return sorted({
        int(item.get("three_d_id", 0))
        for item in load_master_catalog()["lives"]
        if int(item.get("three_d_id", 0))
    })


def live_music_video_ids() -> list[int]:
    return sorted({
        int(video_id)
        for item in load_master_catalog()["lives"]
        for video_id in item.get("music_video_ids", [])
        if int(video_id)
    })


def battle_area_ids() -> list[int]:
    return sorted(int(item["id"]) for item in load_master_catalog()["battle_areas"])


def battle_stage_ids() -> list[int]:
    return sorted(int(item["id"]) for item in load_master_catalog()["battle_stages"])
