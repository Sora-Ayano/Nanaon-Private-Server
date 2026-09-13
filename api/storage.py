"""Persistent local account storage for the preservation server.

The client protocol still receives ``UserGetData``.  SQLite only replaces the
old process-local UUID/user mapping and preserves the mutable parts of that
object between server restarts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import fields
from pathlib import Path
from typing import Optional

from api.models import (
    AreaItem, AreaItemSetting, Card, CardSub, Costume, Deck, Gem, Group,
    Item, Point, Stamina, User, UserGetData,
)


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("NANAON_DB_PATH", BASE_DIR / "var/data/users.sqlite3"))
LOTTERY_CATALOG_PATH = BASE_DIR / "data/preservation_lottery.json"
COSTUME_CATALOG_PATH = BASE_DIR / "data/costume_movie_catalog.json"
CARD_COSTUME_CATALOG_PATH = BASE_DIR / "data/card_costume_catalog.json"


class UserStore:
    def __init__(self, path: Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    uuid TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    comment TEXT NOT NULL DEFAULT '',
                    birth_date TEXT NOT NULL DEFAULT '',
                    exp INTEGER NOT NULL DEFAULT 0,
                    vip_point INTEGER NOT NULL DEFAULT 0,
                    profile_settings_json TEXT NOT NULL DEFAULT '[]',
                    last_login_time INTEGER NOT NULL DEFAULT 0,
                    nanacomi_shop_dialog_unconfirmed INTEGER NOT NULL DEFAULT 0,
                    tutorial_progress INTEGER NOT NULL DEFAULT 70,
                    gem_total INTEGER NOT NULL DEFAULT 99999,
                    gem_charge INTEGER NOT NULL DEFAULT 0,
                    gem_free INTEGER NOT NULL DEFAULT 99999,
                    main_deck_slot INTEGER NOT NULL DEFAULT 1,
                    favorite_master_card_id INTEGER NOT NULL DEFAULT 10110001,
                    favorite_card_evolve INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_cards (
                    user_id INTEGER NOT NULL,
                    instance_id INTEGER NOT NULL,
                    master_card_id INTEGER NOT NULL,
                    exp INTEGER NOT NULL DEFAULT 0,
                    skill_exp INTEGER NOT NULL DEFAULT 0,
                    evolve INTEGER NOT NULL DEFAULT 0,
                    illust_change INTEGER NOT NULL DEFAULT 0,
                    breakthrough_level INTEGER NOT NULL DEFAULT 0,
                    episode_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (user_id, instance_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_decks (
                    user_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    main_card_ids_json TEXT NOT NULL,
                    ability_card_ids_json TEXT NOT NULL,
                    PRIMARY KEY (user_id, slot),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_music (
                    user_id INTEGER NOT NULL,
                    master_music_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_music_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_music_shop_releases (
                    user_id INTEGER NOT NULL,
                    master_music_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_music_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_characters (
                    user_id INTEGER NOT NULL,
                    master_character_id INTEGER NOT NULL,
                    exp INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, master_character_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                -- Full protocol snapshot for inventory categories that do not
                -- yet need their own relational query path.  Core identity,
                -- cards, decks, music, characters and live results remain in
                -- normalized tables and overwrite this snapshot on load.
                CREATE TABLE IF NOT EXISTS user_snapshots (
                    user_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_live_results (
                    user_id INTEGER NOT NULL,
                    master_live_id INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    clear_count INTEGER NOT NULL DEFAULT 0,
                    high_score INTEGER NOT NULL DEFAULT 0,
                    feedback_musical_score INTEGER NOT NULL DEFAULT 0,
                    feedback_difficulty_rating INTEGER NOT NULL DEFAULT 0,
                    updated_time INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, master_live_id, level),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS live_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    master_live_id INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    started_at INTEGER NOT NULL,
                    ended_at INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_live_sessions_user_status
                    ON live_sessions(user_id, status, id);

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS server_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                -- Trusted static definitions.  Binary assets remain on the
                -- local CDN; this table only stores protocol/master metadata.
                CREATE TABLE IF NOT EXISTS content_catalog (
                    content_type TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    parent_id INTEGER,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    display_name TEXT NOT NULL DEFAULT '',
                    data_json TEXT NOT NULL DEFAULT '{}',
                    source TEXT NOT NULL,
                    verification_status TEXT NOT NULL DEFAULT 'unverified'
                        CHECK (verification_status IN ('unverified', 'observed', 'verified', 'invalid')),
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (content_type, content_id)
                );

                CREATE TABLE IF NOT EXISTS asset_objects (
                    logical_path TEXT PRIMARY KEY,
                    content_type TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    asset_kind TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    byte_size INTEGER,
                    crc32 INTEGER,
                    sha256 TEXT,
                    source TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_asset_objects_content
                    ON asset_objects(content_type, content_id, asset_kind);

                CREATE TABLE IF NOT EXISTS master_lotteries (
                    master_lottery_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    cost_resource_type TEXT NOT NULL,
                    cost_resource_id INTEGER NOT NULL DEFAULT 0,
                    cost_amount INTEGER NOT NULL,
                    draw_count INTEGER NOT NULL DEFAULT 1,
                    start_time INTEGER,
                    end_time INTEGER,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    source TEXT NOT NULL,
                    verification_status TEXT NOT NULL DEFAULT 'unverified'
                );

                CREATE TABLE IF NOT EXISTS master_lottery_items (
                    master_lottery_id INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    reward_type TEXT NOT NULL,
                    reward_id INTEGER NOT NULL,
                    reward_amount INTEGER NOT NULL DEFAULT 1,
                    weight INTEGER NOT NULL,
                    rarity INTEGER,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (master_lottery_id, sequence),
                    FOREIGN KEY (master_lottery_id) REFERENCES master_lotteries(master_lottery_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_wallets (
                    user_id INTEGER NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id INTEGER NOT NULL DEFAULT 0,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    paid_amount INTEGER NOT NULL DEFAULT 0 CHECK (paid_amount >= 0),
                    free_amount INTEGER NOT NULL DEFAULT 0 CHECK (free_amount >= 0),
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, resource_type, resource_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_stamina (
                    user_id INTEGER PRIMARY KEY,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    last_updated_time INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_items (
                    user_id INTEGER NOT NULL,
                    instance_id INTEGER NOT NULL,
                    master_item_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    PRIMARY KEY (user_id, instance_id),
                    UNIQUE (user_id, master_item_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_point_exchange_sales (
                    user_id INTEGER NOT NULL,
                    master_point_exchange_id INTEGER NOT NULL,
                    exchange_count INTEGER NOT NULL DEFAULT 1,
                    sold_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_point_exchange_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_card_subs (
                    user_id INTEGER NOT NULL,
                    instance_id INTEGER NOT NULL,
                    master_card_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    PRIMARY KEY (user_id, instance_id),
                    UNIQUE (user_id, master_card_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_pieces (
                    user_id INTEGER NOT NULL,
                    master_piece_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    data_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (user_id, master_piece_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                -- Simple ownership (music/title/stamp/MV/3D/movie/lane skin)
                -- is separate from read/completion history.
                CREATE TABLE IF NOT EXISTS user_content_unlocks (
                    user_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    unlocked_at INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'local_grant',
                    PRIMARY KEY (user_id, content_type, content_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_content_reads (
                    user_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    read_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, content_type, content_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_story_progress (
                    user_id INTEGER NOT NULL,
                    master_story_id INTEGER NOT NULL,
                    master_story_part_id INTEGER NOT NULL,
                    unlocked_at INTEGER,
                    read_at INTEGER,
                    completed_at INTEGER,
                    PRIMARY KEY (user_id, master_story_id, master_story_part_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_card_episode_progress (
                    user_id INTEGER NOT NULL,
                    card_instance_id INTEGER NOT NULL,
                    episode_type INTEGER NOT NULL,
                    unlocked_at INTEGER,
                    read_at INTEGER,
                    completed_at INTEGER,
                    PRIMARY KEY (user_id, card_instance_id, episode_type),
                    FOREIGN KEY (user_id, card_instance_id)
                        REFERENCES user_cards(user_id, instance_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_costumes (
                    user_id INTEGER NOT NULL,
                    costume_kind TEXT NOT NULL,
                    master_character_id INTEGER NOT NULL,
                    master_costume_id INTEGER NOT NULL,
                    part_type INTEGER NOT NULL DEFAULT 0,
                    unlocked_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, costume_kind, master_character_id, master_costume_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_costume_selections (
                    user_id INTEGER NOT NULL,
                    costume_kind TEXT NOT NULL,
                    master_character_id INTEGER NOT NULL,
                    part_type INTEGER NOT NULL DEFAULT 0,
                    master_costume_id INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, costume_kind, master_character_id, part_type),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_area_items (
                    user_id INTEGER NOT NULL,
                    instance_id INTEGER NOT NULL,
                    master_area_item_id INTEGER NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
                    amount INTEGER NOT NULL DEFAULT 1 CHECK (amount >= 0),
                    PRIMARY KEY (user_id, instance_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_area_layout (
                    user_id INTEGER NOT NULL,
                    master_area_position_id INTEGER NOT NULL,
                    area_item_instance_id INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_area_position_id),
                    FOREIGN KEY (user_id, area_item_instance_id)
                        REFERENCES user_area_items(user_id, instance_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_backstage_settings (
                    user_id INTEGER NOT NULL,
                    position_number INTEGER NOT NULL,
                    setting_type INTEGER NOT NULL,
                    setting_id INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, position_number),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_live_missions (
                    user_id INTEGER NOT NULL,
                    master_live_id INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    mission_id INTEGER NOT NULL,
                    completed_at INTEGER,
                    reward_received_at INTEGER,
                    PRIMARY KEY (user_id, master_live_id, level, mission_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_live_battle_areas (
                    user_id INTEGER NOT NULL,
                    master_battle_area_id INTEGER NOT NULL,
                    clear_date INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, master_battle_area_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_live_battle_stages (
                    user_id INTEGER NOT NULL,
                    master_battle_stage_id INTEGER NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    clear_count INTEGER NOT NULL DEFAULT 0,
                    full_combo_count INTEGER NOT NULL DEFAULT 0,
                    all_perfect_count INTEGER NOT NULL DEFAULT 0,
                    first_clear_date INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, master_battle_stage_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_missions (
                    user_id INTEGER NOT NULL,
                    master_mission_id INTEGER NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    completed_at INTEGER,
                    reward_received_at INTEGER,
                    PRIMARY KEY (user_id, master_mission_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS state_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    func_id INTEGER NOT NULL,
                    idempotency_key TEXT,
                    reason TEXT NOT NULL,
                    request_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'committed',
                    created_at INTEGER NOT NULL,
                    UNIQUE (user_id, idempotency_key),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS state_ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id INTEGER NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id INTEGER NOT NULL DEFAULT 0,
                    instance_id INTEGER,
                    delta INTEGER NOT NULL,
                    balance_after INTEGER,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (transaction_id) REFERENCES state_transactions(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS api_idempotency (
                    user_id INTEGER NOT NULL,
                    func_id INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, func_id, idempotency_key),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS lottery_draws (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    master_lottery_id INTEGER NOT NULL,
                    draw_index INTEGER NOT NULL,
                    reward_type TEXT NOT NULL,
                    reward_id INTEGER NOT NULL,
                    reward_amount INTEGER NOT NULL DEFAULT 1,
                    random_value INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (transaction_id) REFERENCES state_transactions(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (master_lottery_id) REFERENCES master_lotteries(master_lottery_id)
                );
                """
            )
            self._ensure_column(db, "users", "exp", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db, "users", "vip_point", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(
                db, "users", "profile_settings_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._ensure_column(
                db, "users", "last_login_time", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(
                db,
                "users",
                "nanacomi_shop_dialog_unconfirmed",
                "INTEGER NOT NULL DEFAULT 0",
            )
            for name, definition in (
                ("play_count", "INTEGER NOT NULL DEFAULT 0"),
                ("full_combo_count", "INTEGER NOT NULL DEFAULT 0"),
                ("all_perfect_count", "INTEGER NOT NULL DEFAULT 0"),
                ("max_combo", "INTEGER NOT NULL DEFAULT 0"),
                ("first_clear_time", "INTEGER"),
                ("last_played_time", "INTEGER NOT NULL DEFAULT 0"),
                ("judgement_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                self._ensure_column(db, "user_live_results", name, definition)
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations VALUES (?, ?, ?)",
                (2, "normalized_local_state_foundation", int(time.time())),
            )
            # Pool 10100001 / price 4 and item (10100001, 1) were observed in
            # a real client request and recovered LotteryItemMst memory row.
            db.execute(
                """INSERT OR IGNORE INTO master_lotteries (
                       master_lottery_id, name, cost_resource_type,
                       cost_resource_id, cost_amount, draw_count, data_json,
                       source, verification_status
                   ) VALUES (10100001, 'Local preservation lottery', 'gem',
                             0, 0, 10, ?, 'client_memory', 'observed')""",
                (json.dumps({"master_lottery_price_number": 4}),),
            )
            db.execute(
                """INSERT OR IGNORE INTO master_lottery_items (
                       master_lottery_id, sequence, reward_type, reward_id,
                       reward_amount, weight, data_json
                   ) VALUES (10100001, 1, 'common_reward', 12100020,
                             1, 10000, ?)""",
                (json.dumps({"master_lottery_item_number": 1, "priority": 4}),),
            )
            db.execute(
                """UPDATE master_lotteries SET cost_amount = 0, draw_count = 10
                   WHERE master_lottery_id = 10100001
                     AND source = 'client_memory'"""
            )
            self._install_preservation_lottery(db)
            self._upgrade_base_accounts(db)
            self._upgrade_wall_seal_balance(db)
            self._upgrade_wall_ticket_balance(db)
            self._upgrade_full_3d_costume_inventory(db)
            self._upgrade_correct_live_unlock_inventory(db)
            self._upgrade_verified_2d_costume_inventory(db)

    @staticmethod
    def _upgrade_base_accounts(db: sqlite3.Connection) -> None:
        """Apply the distributable rank-50 preservation baseline exactly once.

        Existing user choices such as decks, equipped titles, costumes and
        room layouts are retained.  Static ownership and starting balances are
        synchronized with ``UserGetData.create_default`` so an upgraded server
        and a newly created database expose the same account inventory.
        """
        migration_version = 3
        if db.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone():
            return

        now = int(time.time())
        user_ids = [int(row[0]) for row in db.execute("SELECT user_id FROM users")]
        for user_id in user_ids:
            baseline = UserGetData.create_default(user_id)
            db.execute(
                """UPDATE users SET
                       exp = ?, gem_total = ?, gem_charge = ?, gem_free = ?,
                       updated_at = ?
                   WHERE user_id = ?""",
                (
                    baseline.user.exp,
                    baseline.gem.total,
                    baseline.gem.charge,
                    baseline.gem.free,
                    now,
                    user_id,
                ),
            )

            # Card instance IDs are stable in the preservation catalog and are
            # referenced by saved decks.  Replace progress, not the deck rows.
            db.execute("DELETE FROM user_cards WHERE user_id = ?", (user_id,))
            db.executemany(
                """INSERT INTO user_cards (
                       user_id, instance_id, master_card_id, exp, skill_exp,
                       evolve, illust_change, breakthrough_level, episode_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        user_id,
                        card.id,
                        card.master_card_id,
                        card.exp,
                        card.skill_exp,
                        card.evolve,
                        card.illust_change,
                        card.breakthrough_level,
                        json.dumps(card.episode),
                    )
                    for card in baseline.card_list
                ],
            )

            db.execute("DELETE FROM user_items WHERE user_id = ?", (user_id,))
            db.executemany(
                "INSERT INTO user_items VALUES (?, ?, ?, ?)",
                [
                    (user_id, item.id, item.master_item_id, item.amount)
                    for item in baseline.item_list
                ],
            )
            db.execute(
                """INSERT OR REPLACE INTO user_wallets (
                       user_id, resource_type, resource_id, amount,
                       paid_amount, free_amount, updated_at
                   ) VALUES (?, 'gem', 0, ?, ?, ?, ?)""",
                (
                    user_id,
                    baseline.gem.total,
                    baseline.gem.charge,
                    baseline.gem.free,
                    now,
                ),
            )
            db.executemany(
                """INSERT OR REPLACE INTO user_wallets (
                       user_id, resource_type, resource_id, amount,
                       paid_amount, free_amount, updated_at
                   ) VALUES (?, 'point', ?, ?, 0, 0, ?)""",
                [
                    (user_id, point.type, point.amount, now)
                    for point in baseline.point_list
                ],
            )

            db.executemany(
                "INSERT OR IGNORE INTO user_music VALUES (?, ?)",
                [(user_id, item) for item in baseline.master_music_ids],
            )
            db.executemany(
                "INSERT OR IGNORE INTO user_music_shop_releases VALUES (?, ?)",
                [(user_id, item) for item in baseline.music_shop_releases],
            )
            db.executemany(
                "INSERT OR IGNORE INTO user_characters VALUES (?, ?, ?)",
                [
                    (user_id, group.master_character_id, group.exp)
                    for group in baseline.character_list
                ],
            )

            unlock_fields = {
                "title": baseline.master_title_ids,
                "music": baseline.master_music_ids,
                "music_video": baseline.master_live_music_video_ids,
                "three_d": baseline.master_live_three_d_ids,
                "stamp": baseline.master_stamp_ids,
                "lane_skin": baseline.master_live_lane_skin_ids,
            }
            unlock_rows = [
                (user_id, content_type, int(content_id), now, "base_account_v3")
                for content_type, content_ids in unlock_fields.items()
                for content_id in content_ids
            ]
            db.executemany(
                "INSERT OR IGNORE INTO user_content_unlocks VALUES (?, ?, ?, ?, ?)",
                unlock_rows,
            )

            for story in baseline.story_list:
                story_id = int(story.get("master_story_id", 0) or 0)
                db.executemany(
                    """INSERT INTO user_story_progress (
                           user_id, master_story_id, master_story_part_id,
                           unlocked_at, read_at, completed_at
                       ) VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, master_story_id, master_story_part_id)
                       DO UPDATE SET unlocked_at=COALESCE(
                           user_story_progress.unlocked_at, excluded.unlocked_at
                       )""",
                    [
                        (user_id, story_id, int(part_id), now, None, None)
                        for part_id in story.get("master_story_part_ids", []) or []
                    ],
                )

            existing_area_items = {
                int(row["master_area_item_id"]): int(row["instance_id"])
                for row in db.execute(
                    "SELECT instance_id, master_area_item_id FROM user_area_items "
                    "WHERE user_id = ?",
                    (user_id,),
                )
            }
            next_instance_id = max(existing_area_items.values(), default=0) + 1
            for item in baseline.area_item_list:
                instance_id = existing_area_items.get(item.master_area_item_id)
                if instance_id is None:
                    instance_id = next_instance_id
                    next_instance_id += 1
                    db.execute(
                        "INSERT INTO user_area_items VALUES (?, ?, ?, ?, 1)",
                        (
                            user_id,
                            instance_id,
                            item.master_area_item_id,
                            item.level,
                        ),
                    )
                else:
                    db.execute(
                        """UPDATE user_area_items SET level = MAX(level, ?), amount = 1
                           WHERE user_id = ? AND instance_id = ?""",
                        (item.level, user_id, instance_id),
                    )

        db.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (migration_version, "rank_50_full_inventory_base_account", now),
        )

    @staticmethod
    def _upgrade_wall_seal_balance(db: sqlite3.Connection) -> None:
        """Retain 100,000 POINT_TYPE.SEAL for preservation completeness."""
        migration_version = 4
        if db.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone():
            return

        now = int(time.time())
        db.execute(
            """INSERT INTO user_wallets (
                   user_id, resource_type, resource_id, amount,
                   paid_amount, free_amount, updated_at
               )
               SELECT user_id, 'point', 3, 100000, 0, 0, ? FROM users
               WHERE 1
               ON CONFLICT(user_id, resource_type, resource_id)
               DO UPDATE SET
                   amount = MAX(user_wallets.amount, excluded.amount),
                   updated_at = excluded.updated_at""",
            (now,),
        )
        db.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (migration_version, "wall_seal_base_balance", now),
        )

    @staticmethod
    def _upgrade_wall_ticket_balance(db: sqlite3.Connection) -> None:
        """Grant 100,000 currency used by the 壁ちゃん exchange.

        Decrypted ExchangeMst row 11100100 uses CONSUME_TYPE.POINT with
        value 2, which is POINT_TYPE.TICKET in the released client.
        """
        migration_version = 5
        if db.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone():
            return

        now = int(time.time())
        db.execute(
            """INSERT INTO user_wallets (
                   user_id, resource_type, resource_id, amount,
                   paid_amount, free_amount, updated_at
               )
               SELECT user_id, 'point', 2, 100000, 0, 0, ? FROM users
               WHERE 1
               ON CONFLICT(user_id, resource_type, resource_id)
               DO UPDATE SET
                   amount = MAX(user_wallets.amount, excluded.amount),
                   updated_at = excluded.updated_at""",
            (now,),
        )
        db.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (migration_version, "wall_ticket_base_balance", now),
        )

    @staticmethod
    def _grant_full_3d_costumes(
        db: sqlite3.Connection, user_id: int, now: Optional[int] = None
    ) -> int:
        """Grant only real 3D model-costume rows from the recovered master.

        The 2D ``costume_list`` is intentionally untouched because the released
        client still has a separate 2D downloader defect.  Ownership only grows;
        user selections are retained when they already exist.
        """
        if not COSTUME_CATALOG_PATH.is_file():
            return 0
        catalog = json.loads(COSTUME_CATALOG_PATH.read_text(encoding="utf-8"))
        unlocked_at = int(time.time()) if now is None else int(now)
        ownership_rows = []
        selection_rows = []
        for group in catalog.get("costume_3d", []) or []:
            character_id = int(group.get("master_character_id", 0) or 0)
            part_type = int(group.get("master_model_costume_type", 0) or 0)
            costume_ids = sorted({
                int(costume_id)
                for costume_id in group.get("master_model_costume_ids", []) or []
                if int(costume_id)
            })
            if not character_id or not part_type or not costume_ids:
                continue
            ownership_rows.extend(
                (
                    int(user_id), "3d", character_id, costume_id,
                    part_type, unlocked_at,
                )
                for costume_id in costume_ids
            )
            selection_rows.append(
                (
                    int(user_id), "3d", character_id, part_type,
                    costume_ids[0], unlocked_at,
                )
            )
        db.executemany(
            "INSERT OR IGNORE INTO user_costumes VALUES (?, ?, ?, ?, ?, ?)",
            ownership_rows,
        )
        db.executemany(
            "INSERT OR IGNORE INTO user_costume_selections VALUES (?, ?, ?, ?, ?, ?)",
            selection_rows,
        )
        return len(ownership_rows)

    @classmethod
    def _upgrade_full_3d_costume_inventory(cls, db: sqlite3.Connection) -> None:
        """Give every existing local account all recovered 3D costumes once."""
        migration_version = 6
        if db.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone():
            return

        now = int(time.time())
        for row in db.execute("SELECT user_id FROM users").fetchall():
            cls._grant_full_3d_costumes(db, int(row[0]), now)
        db.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (migration_version, "full_3d_costume_inventory", now),
        )

    @staticmethod
    def _upgrade_correct_live_unlock_inventory(db: sqlite3.Connection) -> None:
        """Add LiveMst protocol IDs, replacing the old Timeline-ID assumption."""
        migration_version = 7
        if db.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone():
            return

        now = int(time.time())
        rows = []
        for row in db.execute("SELECT user_id FROM users").fetchall():
            user_id = int(row[0])
            baseline = UserGetData.create_default(user_id)
            rows.extend(
                (user_id, "three_d", int(content_id), now, "live_mst_v7")
                for content_id in baseline.master_live_three_d_ids
            )
            rows.extend(
                (user_id, "music_video", int(content_id), now, "live_mst_v7")
                for content_id in baseline.master_live_music_video_ids
            )
        db.executemany(
            "INSERT OR IGNORE INTO user_content_unlocks VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        db.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (migration_version, "live_mst_protocol_unlock_inventory", now),
        )

    @staticmethod
    def _grant_verified_2d_costumes(
        db: sqlite3.Connection, user_id: int, now: Optional[int] = None
    ) -> int:
        """Grant only costumes whose complete Live2D chain is in the package."""
        if not CARD_COSTUME_CATALOG_PATH.is_file():
            return 0
        catalog = json.loads(CARD_COSTUME_CATALOG_PATH.read_text(encoding="utf-8"))
        unlocked_at = int(time.time()) if now is None else int(now)
        ownership_rows = []
        selection_rows = []
        for group in catalog.get("available_costumes", []) or []:
            character_id = int(group.get("master_character_id", 0) or 0)
            costume_ids = sorted({
                int(costume_id)
                for costume_id in group.get("master_costume_ids", []) or []
                if int(costume_id)
            })
            if not character_id or not costume_ids:
                continue
            ownership_rows.extend(
                (int(user_id), "2d", character_id, costume_id, 0, unlocked_at)
                for costume_id in costume_ids
            )
            selection_rows.append(
                (int(user_id), "2d", character_id, 0, costume_ids[0], unlocked_at)
            )
        db.executemany(
            "INSERT OR IGNORE INTO user_costumes VALUES (?, ?, ?, ?, ?, ?)",
            ownership_rows,
        )
        db.executemany(
            "INSERT OR IGNORE INTO user_costume_selections VALUES (?, ?, ?, ?, ?, ?)",
            selection_rows,
        )
        return len(ownership_rows)

    @classmethod
    def _upgrade_verified_2d_costume_inventory(
        cls, db: sqlite3.Connection
    ) -> None:
        migration_version = 8
        if db.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (migration_version,),
        ).fetchone():
            return

        now = int(time.time())
        for row in db.execute("SELECT user_id FROM users").fetchall():
            cls._grant_verified_2d_costumes(db, int(row[0]), now)
        db.execute(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            (migration_version, "verified_2d_evolution_costumes", now),
        )

    @staticmethod
    def _install_preservation_lottery(db: sqlite3.Connection) -> None:
        """Install the distributable real base pool into every new database."""
        if not LOTTERY_CATALOG_PATH.is_file():
            return
        catalog = json.loads(LOTTERY_CATALOG_PATH.read_text(encoding="utf-8"))
        lottery = catalog["lottery"]
        lottery_id = int(lottery["master_lottery_id"])
        db.execute("DELETE FROM master_lottery_items WHERE master_lottery_id = ?", (lottery_id,))
        db.execute(
            """INSERT OR REPLACE INTO master_lotteries (
                   master_lottery_id, name, cost_resource_type, cost_resource_id,
                   cost_amount, draw_count, data_json, source, verification_status
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'decrypted_master', 'verified')""",
            (
                lottery_id,
                lottery["name"],
                lottery["cost_resource_type"],
                int(lottery["cost_resource_id"]),
                int(lottery["cost_amount"]),
                int(lottery["draw_count"]),
                json.dumps({
                    "master_lottery_price_number": int(lottery["master_lottery_price_number"])
                }),
            ),
        )
        for item in catalog["items"]:
            db.execute(
                """INSERT INTO master_lottery_items (
                       master_lottery_id, sequence, reward_type, reward_id,
                       reward_amount, weight, rarity, data_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    lottery_id,
                    int(item["sequence"]),
                    item["reward_type"],
                    int(item["reward_id"]),
                    int(item["reward_amount"]),
                    int(item["weight"]),
                    int(item["rarity"]),
                    json.dumps({
                        "master_lottery_item_id": int(item["master_lottery_item_id"]),
                        "master_lottery_item_number": int(item["master_lottery_item_number"]),
                        "master_common_reward_id": int(item["master_common_reward_id"]),
                    }),
                ),
            )

    @staticmethod
    def _ensure_column(
        db: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        """Add one backward-compatible column to an existing local database."""
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def next_user_id(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COALESCE(MAX(user_id), 100000) + 1 AS id FROM users").fetchone()
        return int(row["id"])

    def user_id_for_uuid(self, uuid_param: str) -> Optional[int]:
        with self._connect() as db:
            row = db.execute("SELECT user_id FROM users WHERE uuid = ?", (uuid_param,)).fetchone()
        return int(row["user_id"]) if row else None

    def uuid_for_user_id(self, user_id: int) -> Optional[str]:
        with self._connect() as db:
            row = db.execute("SELECT uuid FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return str(row["uuid"]) if row else None

    def create_user(self, uuid_param: str, user_id: Optional[int] = None) -> UserGetData:
        existing = self.user_id_for_uuid(uuid_param)
        if existing is not None:
            loaded = self.load_user(existing)
            if loaded is not None:
                return loaded
        resolved_id = self.next_user_id() if user_id is None else int(user_id)
        user_data = UserGetData.create_default(resolved_id)
        self.save_user(uuid_param, user_data)
        with self._connect() as db:
            self._grant_full_3d_costumes(db, resolved_id)
            self._grant_verified_2d_costumes(db, resolved_id)
        return self.load_user(resolved_id) or user_data

    def save_user(self, uuid_param: str, data: UserGetData) -> None:
        now = int(time.time())
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO users (
                    user_id, uuid, name, comment, birth_date, exp, vip_point,
                    profile_settings_json, last_login_time,
                    nanacomi_shop_dialog_unconfirmed, tutorial_progress,
                    gem_total, gem_charge, gem_free, main_deck_slot,
                    favorite_master_card_id, favorite_card_evolve, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    uuid=excluded.uuid, name=excluded.name, comment=excluded.comment,
                    birth_date=excluded.birth_date,
                    exp=excluded.exp, vip_point=excluded.vip_point,
                    profile_settings_json=excluded.profile_settings_json,
                    last_login_time=excluded.last_login_time,
                    nanacomi_shop_dialog_unconfirmed=excluded.nanacomi_shop_dialog_unconfirmed,
                    tutorial_progress=excluded.tutorial_progress,
                    gem_total=excluded.gem_total, gem_charge=excluded.gem_charge,
                    gem_free=excluded.gem_free, main_deck_slot=excluded.main_deck_slot,
                    favorite_master_card_id=excluded.favorite_master_card_id,
                    favorite_card_evolve=excluded.favorite_card_evolve,
                    updated_at=excluded.updated_at
                """,
                (
                    data.user.id, uuid_param, data.user.name, data.user.comment,
                    data.user.birth_date or "", data.user.exp, data.user.vip_point,
                    json.dumps(data.user.profile_settings), data.user.last_login_time,
                    data.user.nanacomi_shop_dialog_unconfirmed, data.tutorial_progress,
                    data.gem.total, data.gem.charge, data.gem.free,
                    data.user.main_deck_slot, data.user.favorite_master_card_id,
                    data.user.favorite_card_evolve, now, now,
                ),
            )
            for table in (
                "user_decks", "user_music", "user_music_shop_releases",
                "user_characters",
                "user_wallets", "user_stamina", "user_items", "user_card_subs",
                "user_content_unlocks", "user_content_reads",
                "user_area_layout",
                "user_area_items", "user_backstage_settings",
            ):
                db.execute(f"DELETE FROM {table} WHERE user_id = ?", (data.user.id,))
            db.executemany(
                """INSERT INTO user_cards (
                       user_id, instance_id, master_card_id, exp, skill_exp,
                       evolve, illust_change, breakthrough_level, episode_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, instance_id) DO UPDATE SET
                       master_card_id=excluded.master_card_id,
                       exp=excluded.exp,
                       skill_exp=excluded.skill_exp,
                       evolve=excluded.evolve,
                       illust_change=excluded.illust_change,
                       breakthrough_level=excluded.breakthrough_level,
                       episode_json=excluded.episode_json""",
                [
                    (
                        data.user.id, card.id, card.master_card_id, card.exp,
                        card.skill_exp, card.evolve, card.illust_change,
                        card.breakthrough_level, json.dumps(card.episode),
                    )
                    for card in data.card_list
                ],
            )
            db.executemany(
                """INSERT INTO user_decks VALUES (?, ?, ?, ?)""",
                [
                    (
                        data.user.id, deck.slot, json.dumps(deck.main_card_ids),
                        json.dumps(deck.ability_card_ids),
                    )
                    for deck in data.deck_list
                ],
            )
            db.executemany(
                "INSERT INTO user_music VALUES (?, ?)",
                [(data.user.id, music_id) for music_id in data.master_music_ids],
            )
            db.executemany(
                "INSERT INTO user_music_shop_releases VALUES (?, ?)",
                [
                    (data.user.id, music_id)
                    for music_id in data.music_shop_releases
                ],
            )
            db.executemany(
                "INSERT INTO user_characters VALUES (?, ?, ?)",
                [(data.user.id, group.master_character_id, group.exp) for group in data.character_list],
            )
            db.executemany(
                """INSERT INTO user_wallets (
                       user_id, resource_type, resource_id, amount,
                       paid_amount, free_amount, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        data.user.id, "gem", 0, data.gem.total,
                        data.gem.charge, data.gem.free, now,
                    )
                ] + [
                    (data.user.id, "point", point.type, point.amount, 0, 0, now)
                    for point in data.point_list
                ],
            )
            db.execute(
                "INSERT INTO user_stamina VALUES (?, ?, ?)",
                (data.user.id, data.stamina.stamina, data.stamina.last_updated_time),
            )
            db.executemany(
                "INSERT INTO user_items VALUES (?, ?, ?, ?)",
                [
                    (data.user.id, item.id, item.master_item_id, item.amount)
                    for item in data.item_list
                ],
            )
            db.executemany(
                "INSERT INTO user_card_subs VALUES (?, ?, ?, ?)",
                [
                    (data.user.id, item.id, item.master_card_id, item.amount)
                    for item in data.card_sub_list
                ],
            )

            unlock_fields = {
                "title": data.master_title_ids,
                "music": data.master_music_ids,
                "music_video": data.master_live_music_video_ids,
                "three_d": data.master_live_three_d_ids,
                "stamp": data.master_stamp_ids,
                "lane_skin": data.master_live_lane_skin_ids,
            }
            unlock_rows = []
            for content_type, content_ids in unlock_fields.items():
                unlock_rows.extend(
                    (data.user.id, content_type, int(content_id), now, "snapshot_migration")
                    for content_id in content_ids
                )
            for detail in data.local_movie_detail_list:
                if not isinstance(detail, dict):
                    continue
                content_id = detail.get("master_local_movie_id", detail.get("id"))
                if content_id is not None:
                    unlock_rows.append(
                        (data.user.id, "local_movie", int(content_id), now, "snapshot_migration")
                    )
            db.executemany(
                "INSERT OR IGNORE INTO user_content_unlocks VALUES (?, ?, ?, ?, ?)",
                unlock_rows,
            )
            db.executemany(
                "INSERT INTO user_content_reads VALUES (?, 'talk', ?, ?)",
                [(data.user.id, int(talk_id), now) for talk_id in data.master_talk_ids],
            )

            story_rows = []
            for story in data.story_list:
                if not isinstance(story, dict):
                    continue
                story_id = int(story.get("master_story_id", 0) or 0)
                for part_id in story.get("master_story_part_ids", []) or []:
                    story_rows.append((data.user.id, story_id, int(part_id), now, None, None))
            db.executemany(
                """INSERT INTO user_story_progress (
                       user_id, master_story_id, master_story_part_id,
                       unlocked_at, read_at, completed_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, master_story_id, master_story_part_id)
                   DO UPDATE SET
                       unlocked_at=COALESCE(
                           user_story_progress.unlocked_at,
                           excluded.unlocked_at
                       )""",
                story_rows,
            )

            costume_rows = []
            selection_rows = []
            for costume in data.costume_list:
                costume_rows.extend(
                    (
                        data.user.id, "2d", costume.master_character_id,
                        int(costume_id), 0, now,
                    )
                    for costume_id in costume.master_costume_ids
                )
                if costume.master_costume_id:
                    selection_rows.append(
                        (
                            data.user.id, "2d", costume.master_character_id, 0,
                            costume.master_costume_id, now,
                        )
                    )
            for model in data.model_costume_list:
                if not isinstance(model, dict):
                    continue
                character_id = int(model.get("master_character_id", 0) or 0)
                part_type = int(model.get("master_model_costume_type", 0) or 0)
                costume_rows.extend(
                    (data.user.id, "3d", character_id, int(costume_id), part_type, now)
                    for costume_id in model.get("master_model_costume_ids", []) or []
                )
                selected_id = int(model.get("master_model_costume_id", 0) or 0)
                # Optional model parts persist id 0 as an explicit
                # "unequipped" selection.  BODY (type 1) remains mandatory.
                if selected_id or part_type != 1:
                    selection_rows.append(
                        (data.user.id, "3d", character_id, part_type, selected_id, now)
                    )
            db.executemany(
                "INSERT OR IGNORE INTO user_costumes VALUES (?, ?, ?, ?, ?, ?)",
                costume_rows,
            )
            db.executemany(
                "INSERT OR REPLACE INTO user_costume_selections VALUES (?, ?, ?, ?, ?, ?)",
                selection_rows,
            )

            db.executemany(
                "INSERT INTO user_area_items VALUES (?, ?, ?, ?, 1)",
                [
                    (data.user.id, item.id, item.master_area_item_id, item.level)
                    for item in data.area_item_list
                ],
            )
            owned_area_item_ids = {item.id for item in data.area_item_list}
            db.executemany(
                "INSERT INTO user_area_layout VALUES (?, ?, ?, ?)",
                [
                    (
                        data.user.id, setting.master_area_position_id,
                        setting.area_item_id, now,
                    )
                    for setting in data.area_item_setting_list
                    if setting.area_item_id in owned_area_item_ids
                ],
            )
            db.executemany(
                "INSERT INTO user_backstage_settings VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        data.user.id, int(setting.get("position_number", 0) or 0),
                        int(setting.get("setting_type", 0) or 0),
                        int(setting.get("setting_id", 0) or 0), now,
                    )
                    for setting in data.backstage_background_setting_list
                    if isinstance(setting, dict)
                ],
            )
            db.execute(
                """INSERT INTO user_snapshots (user_id, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       state_json=excluded.state_json,
                       updated_at=excluded.updated_at""",
                (
                    data.user.id,
                    json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )

    def load_user(self, user_id: int) -> Optional[UserGetData]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                return None
            card_rows = db.execute(
                "SELECT * FROM user_cards WHERE user_id = ? ORDER BY instance_id", (user_id,)
            ).fetchall()
            deck_rows = db.execute(
                "SELECT * FROM user_decks WHERE user_id = ? ORDER BY slot", (user_id,)
            ).fetchall()
            music_rows = db.execute(
                "SELECT master_music_id FROM user_music WHERE user_id = ?", (user_id,)
            ).fetchall()
            music_shop_rows = db.execute(
                """SELECT master_music_id FROM user_music_shop_releases
                   WHERE user_id = ?""",
                (user_id,),
            ).fetchall()
            character_rows = db.execute(
                "SELECT * FROM user_characters WHERE user_id = ?", (user_id,)
            ).fetchall()
            snapshot_row = db.execute(
                "SELECT state_json FROM user_snapshots WHERE user_id = ?", (user_id,)
            ).fetchone()
            live_rows = db.execute(
                "SELECT * FROM user_live_results WHERE user_id = ?", (user_id,)
            ).fetchall()
            wallet_rows = db.execute(
                "SELECT * FROM user_wallets WHERE user_id = ?", (user_id,)
            ).fetchall()
            stamina_row = db.execute(
                "SELECT * FROM user_stamina WHERE user_id = ?", (user_id,)
            ).fetchone()
            item_rows = db.execute(
                "SELECT * FROM user_items WHERE user_id = ? ORDER BY instance_id", (user_id,)
            ).fetchall()
            card_sub_rows = db.execute(
                "SELECT * FROM user_card_subs WHERE user_id = ? ORDER BY instance_id", (user_id,)
            ).fetchall()
            unlock_rows = db.execute(
                "SELECT * FROM user_content_unlocks WHERE user_id = ?", (user_id,)
            ).fetchall()
            read_rows = db.execute(
                "SELECT * FROM user_content_reads WHERE user_id = ?", (user_id,)
            ).fetchall()
            story_rows = db.execute(
                """SELECT * FROM user_story_progress WHERE user_id = ?
                   ORDER BY master_story_id, master_story_part_id""",
                (user_id,),
            ).fetchall()
            costume_rows = db.execute(
                "SELECT * FROM user_costumes WHERE user_id = ?", (user_id,)
            ).fetchall()
            costume_selection_rows = db.execute(
                "SELECT * FROM user_costume_selections WHERE user_id = ?", (user_id,)
            ).fetchall()
            area_item_rows = db.execute(
                "SELECT * FROM user_area_items WHERE user_id = ? ORDER BY instance_id", (user_id,)
            ).fetchall()
            area_layout_rows = db.execute(
                "SELECT * FROM user_area_layout WHERE user_id = ? ORDER BY master_area_position_id",
                (user_id,),
            ).fetchall()
            backstage_rows = db.execute(
                "SELECT * FROM user_backstage_settings WHERE user_id = ? ORDER BY position_number",
                (user_id,),
            ).fetchall()

        catalog_data = UserGetData.create_default(user_id)
        data = catalog_data
        if snapshot_row is not None:
            data = self._restore_snapshot(
                user_id, json.loads(snapshot_row["state_json"])
            )
        data.user.name = row["name"]
        data.user.comment = row["comment"]
        data.user.birth_date = row["birth_date"]
        data.user.exp = row["exp"]
        data.user.vip_point = row["vip_point"]
        data.user.profile_settings = json.loads(row["profile_settings_json"])
        data.user.last_login_time = row["last_login_time"]
        data.user.nanacomi_shop_dialog_unconfirmed = row[
            "nanacomi_shop_dialog_unconfirmed"
        ]
        data.user.main_deck_slot = row["main_deck_slot"]
        data.user.favorite_master_card_id = row["favorite_master_card_id"]
        data.user.favorite_card_evolve = row["favorite_card_evolve"]
        data.tutorial_progress = row["tutorial_progress"]
        data.gem.total = row["gem_total"]
        data.gem.charge = row["gem_charge"]
        data.gem.free = row["gem_free"]
        if card_rows:
            data.card_list = [
                Card(
                    id=item["instance_id"], master_card_id=item["master_card_id"],
                    exp=item["exp"], skill_exp=item["skill_exp"], evolve=item["evolve"],
                    illust_change=item["illust_change"],
                    breakthrough_level=item["breakthrough_level"],
                    episode=json.loads(item["episode_json"]),
                )
                for item in card_rows
            ]
        if deck_rows:
            data.deck_list = [
                Deck(
                    slot=item["slot"],
                    main_card_ids=json.loads(item["main_card_ids_json"]),
                    ability_card_ids=json.loads(item["ability_card_ids_json"]),
                )
                for item in deck_rows
            ]
        # These normalized tables are authoritative.  Never merge the old
        # snapshot/resource-derived 611*/612* preview BGM identifiers back in.
        data.master_music_ids = sorted(
            int(item["master_music_id"]) for item in music_rows
        )
        data.music_shop_releases = sorted(
            int(item["master_music_id"]) for item in music_shop_rows
        )
        if character_rows:
            data.character_list = [
                Group(master_character_id=item["master_character_id"], exp=item["exp"])
                for item in character_rows
            ]
        if wallet_rows:
            gem_row = next(
                (item for item in wallet_rows if item["resource_type"] == "gem"), None
            )
            if gem_row is not None:
                data.gem = Gem(
                    total=gem_row["amount"],
                    charge=gem_row["paid_amount"],
                    free=gem_row["free_amount"],
                )
            data.point_list = [
                Point(type=item["resource_id"], amount=item["amount"])
                for item in wallet_rows
                if item["resource_type"] == "point"
            ]
        if stamina_row is not None:
            data.stamina = Stamina(
                stamina=stamina_row["amount"],
                last_updated_time=stamina_row["last_updated_time"],
            )
        if item_rows:
            data.item_list = [
                Item(
                    id=item["instance_id"],
                    master_item_id=item["master_item_id"],
                    amount=item["amount"],
                )
                for item in item_rows
            ]
        if card_sub_rows:
            data.card_sub_list = [
                CardSub(
                    id=item["instance_id"],
                    master_card_id=item["master_card_id"],
                    amount=item["amount"],
                )
                for item in card_sub_rows
            ]

        if unlock_rows:
            unlocked: dict[str, list[int]] = {}
            for item in unlock_rows:
                unlocked.setdefault(item["content_type"], []).append(item["content_id"])
            # Only IDs present in v2.4.0 TitleMst are safe to return.  Older
            # imported snapshots can contain synthetic IDs (notably 100001),
            # which make TitleSaveData dereference a missing master row.
            valid_title_ids = set(catalog_data.master_title_ids)
            equipped_titles = [
                int(item) for item in data.user.master_title_ids
                if int(item) in valid_title_ids
            ]
            data.master_title_ids = sorted(
                valid_title_ids
                | (set(unlocked.get("title", [])) & valid_title_ids)
            )
            data.user.master_title_ids = (
                equipped_titles[:3] or list(data.master_title_ids[:3])
            )
            # Music ownership is projected solely from user_music.  The
            # generic unlock table is kept only as an audit/migration record.
            # Old releases stored resource Timeline IDs in the 3D protocol
            # field. Project both inventories through the current LiveMst so
            # stale generated values are never sent back to the client.
            valid_music_videos = set(catalog_data.master_live_music_video_ids)
            data.master_live_music_video_ids = sorted(
                valid_music_videos
                | (set(unlocked.get("music_video", [])) & valid_music_videos)
            )
            valid_three_d = set(catalog_data.master_live_three_d_ids)
            data.master_live_three_d_ids = sorted(
                valid_three_d | (set(unlocked.get("three_d", [])) & valid_three_d)
            )
            data.master_stamp_ids = sorted(
                set(data.master_stamp_ids) | set(unlocked.get("stamp", []))
            )
            data.master_live_lane_skin_ids = sorted(
                set(data.master_live_lane_skin_ids) | set(unlocked.get("lane_skin", []))
            )
        if read_rows:
            data.master_talk_ids = sorted(
                item["content_id"]
                for item in read_rows
                if item["content_type"] == "talk"
            )
        if story_rows:
            story_groups: dict[int, set[int]] = {}
            for item in story_rows:
                if item["unlocked_at"] is not None:
                    story_groups.setdefault(item["master_story_id"], set()).add(
                        item["master_story_part_id"]
                    )
            data.story_list = [
                {
                    "master_story_id": story_id,
                    "master_story_part_ids": sorted(part_ids),
                }
                for story_id, part_ids in sorted(story_groups.items())
            ]
        else:
            data.story_list = []
        if costume_rows:
            owned_2d: dict[int, list[int]] = {}
            owned_3d: dict[tuple[int, int], list[int]] = {}
            for item in costume_rows:
                if item["costume_kind"] == "2d":
                    owned_2d.setdefault(item["master_character_id"], []).append(
                        item["master_costume_id"]
                    )
                elif item["costume_kind"] == "3d":
                    owned_3d.setdefault(
                        (item["master_character_id"], item["part_type"]), []
                    ).append(item["master_costume_id"])
            selected = {
                (
                    item["costume_kind"], item["master_character_id"], item["part_type"]
                ): item["master_costume_id"]
                for item in costume_selection_rows
            }
            data.costume_list = [
                Costume(
                    master_character_id=character_id,
                    master_costume_ids=sorted(costume_ids),
                    master_costume_id=selected.get(
                        ("2d", character_id, 0), 0
                    ),
                )
                for character_id, costume_ids in sorted(owned_2d.items())
            ]
            data.model_costume_list = [
                {
                    "master_character_id": character_id,
                    "master_model_costume_type": part_type,
                    "master_model_costume_ids": sorted(costume_ids),
                    "master_model_costume_id": selected.get(
                        ("3d", character_id, part_type), sorted(costume_ids)[0]
                    ),
                }
                for (character_id, part_type), costume_ids in sorted(owned_3d.items())
            ]
        if area_item_rows:
            data.area_item_list = [
                AreaItem(
                    id=item["instance_id"],
                    master_area_item_id=item["master_area_item_id"],
                    level=item["level"],
                )
                for item in area_item_rows
            ]
            data.released_master_area_item_ids = sorted(
                {item.master_area_item_id for item in data.area_item_list}
            )
        if area_layout_rows:
            data.area_item_setting_list = [
                AreaItemSetting(
                    master_area_position_id=item["master_area_position_id"],
                    area_item_id=item["area_item_instance_id"],
                )
                for item in area_layout_rows
            ]
        if backstage_rows:
            data.backstage_background_setting_list = [
                {
                    "position_number": item["position_number"],
                    "setting_type": item["setting_type"],
                    "setting_id": item["setting_id"],
                }
                for item in backstage_rows
            ]
        data.live_list = [
            {
                "master_live_id": item["master_live_id"], "level": item["level"],
                "clear_count": item["clear_count"], "high_score": item["high_score"],
                "feedback_musical_score": item["feedback_musical_score"],
                "feedback_difficulty_rating": item["feedback_difficulty_rating"],
                "updated_time": item["updated_time"],
                "play_count": item["play_count"],
                "full_combo_count": item["full_combo_count"],
                "all_perfect_count": item["all_perfect_count"],
                "max_combo": item["max_combo"],
                "first_clear_time": item["first_clear_time"],
                "last_played_time": item["last_played_time"],
                "judgement": json.loads(item["judgement_json"]),
            }
            for item in live_rows
        ]
        if data.match_live is None:
            # The owned stamp gallery is opened from Match Live's room-select
            # scene. Older snapshots stored null here and hid the entry.
            data.match_live = catalog_data.match_live
        return data

    @staticmethod
    def _restore_snapshot(user_id: int, payload: dict) -> UserGetData:
        """Rebuild dataclass-backed protocol state from a trusted local row."""
        data = UserGetData.create_default(user_id)

        def restore_object(cls, value):
            if not isinstance(value, dict):
                return cls()
            allowed = {item.name for item in fields(cls)}
            return cls(**{key: val for key, val in value.items() if key in allowed})

        object_fields = {
            "user": User,
            "gem": Gem,
            "stamina": Stamina,
        }
        collection_fields = {
            "card_list": Card,
            "card_sub_list": CardSub,
            "deck_list": Deck,
            "costume_list": Costume,
            "item_list": Item,
            "point_list": Point,
            "area_item_list": AreaItem,
            "area_item_setting_list": AreaItemSetting,
            "character_list": Group,
        }
        for name, cls in object_fields.items():
            if name in payload:
                setattr(data, name, restore_object(cls, payload[name]))
        for name, cls in collection_fields.items():
            value = payload.get(name)
            if isinstance(value, list):
                setattr(data, name, [restore_object(cls, item) for item in value])

        reserved = set(object_fields) | set(collection_fields)
        for definition in fields(UserGetData):
            name = definition.name
            if name not in reserved and name in payload:
                setattr(data, name, payload[name])
        data.user.id = user_id
        return data

    def save_live_result(self, user_id: int, result: dict) -> None:
        with self._connect() as db:
            self._upsert_live_result(db, user_id, result)

    def complete_story_catalog(self, user_id: int, story_list: list[dict]) -> None:
        """Persist the preservation account's recovered stories as completed.

        Release checks are evaluated from the protocol story-part inventory,
        while later story screens also expect durable read/end progress.  A
        single bulk UPSERT keeps both views consistent across restarts.
        """
        now = int(time.time())
        rows = []
        for story in story_list:
            if not isinstance(story, dict):
                continue
            story_id = int(story.get("master_story_id", 0) or 0)
            rows.extend(
                (user_id, story_id, int(part_id), now, now, now)
                for part_id in story.get("master_story_part_ids", []) or []
            )
        if not rows:
            return
        with self._connect() as db:
            db.executemany(
                """INSERT INTO user_story_progress (
                       user_id, master_story_id, master_story_part_id,
                       unlocked_at, read_at, completed_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, master_story_id, master_story_part_id)
                   DO UPDATE SET
                       unlocked_at=COALESCE(user_story_progress.unlocked_at, excluded.unlocked_at),
                       read_at=COALESCE(user_story_progress.read_at, excluded.read_at),
                       completed_at=COALESCE(user_story_progress.completed_at, excluded.completed_at)""",
                rows,
            )

    def complete_talk_reward(
        self,
        data: UserGetData,
        talk_id: int,
        *,
        user_exp: int = 200,
        character_exp: int = 50,
        coin: int = 1_000,
        gem: int = 50,
    ) -> bool:
        """Apply a first-read home-talk reward exactly once and atomically."""
        user_id = int(data.user.id)
        now = int(time.time())
        character_suffix = (int(talk_id) // 10_000) % 100
        master_character_id = 10_000_000 + character_suffix * 100_000
        target_character = next(
            (
                item for item in data.character_list
                if item.master_character_id == master_character_id
            ),
            None,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                """SELECT 1 FROM state_transactions
                   WHERE user_id = ? AND idempotency_key = ?""",
                (user_id, f"talk:{int(talk_id)}"),
            ).fetchone()
            if exists is not None:
                return False

            data.user.exp += int(user_exp)
            data.gem.total += int(gem)
            data.gem.free += int(gem)
            point = next((item for item in data.point_list if item.type == 1), None)
            if point is None:
                point = Point(type=1, amount=0)
                data.point_list.append(point)
            point.amount += int(coin)
            if target_character is not None:
                target_character.exp += int(character_exp)
            if talk_id not in data.master_talk_ids:
                data.master_talk_ids.append(int(talk_id))
                data.master_talk_ids.sort()

            db.execute(
                "INSERT OR IGNORE INTO user_content_reads VALUES (?, 'talk', ?, ?)",
                (user_id, int(talk_id), now),
            )
            db.execute(
                """UPDATE users SET exp = ?, gem_total = ?, gem_charge = ?,
                       gem_free = ?, updated_at = ? WHERE user_id = ?""",
                (
                    data.user.exp, data.gem.total, data.gem.charge,
                    data.gem.free, now, user_id,
                ),
            )
            db.execute(
                """INSERT INTO user_wallets (
                       user_id, resource_type, resource_id, amount,
                       paid_amount, free_amount, updated_at
                   ) VALUES (?, 'gem', 0, ?, ?, ?, ?)
                   ON CONFLICT(user_id, resource_type, resource_id) DO UPDATE SET
                       amount=excluded.amount, paid_amount=excluded.paid_amount,
                       free_amount=excluded.free_amount, updated_at=excluded.updated_at""",
                (user_id, data.gem.total, data.gem.charge, data.gem.free, now),
            )
            db.execute(
                """INSERT INTO user_wallets (
                       user_id, resource_type, resource_id, amount,
                       paid_amount, free_amount, updated_at
                   ) VALUES (?, 'point', 1, ?, 0, 0, ?)
                   ON CONFLICT(user_id, resource_type, resource_id) DO UPDATE SET
                       amount=excluded.amount, updated_at=excluded.updated_at""",
                (user_id, point.amount, now),
            )
            if target_character is not None:
                db.execute(
                    """INSERT INTO user_characters VALUES (?, ?, ?)
                       ON CONFLICT(user_id, master_character_id) DO UPDATE SET
                           exp=excluded.exp""",
                    (user_id, target_character.master_character_id, target_character.exp),
                )
            db.execute(
                """INSERT INTO user_snapshots (user_id, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (
                    user_id,
                    json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            cursor = db.execute(
                """INSERT INTO state_transactions (
                       user_id, func_id, idempotency_key, reason,
                       request_json, created_at
                   ) VALUES (?, 11000, ?, 'talk_read', ?, ?)""",
                (
                    user_id,
                    f"talk:{int(talk_id)}",
                    json.dumps({"master_talk_id": int(talk_id)}, ensure_ascii=False),
                    now,
                ),
            )
            transaction_id = int(cursor.lastrowid)
            entries = [
                ("user_exp", 0, int(user_exp), data.user.exp),
                ("point", 1, int(coin), point.amount),
                ("gem", 0, int(gem), data.gem.total),
            ]
            if target_character is not None:
                entries.append(
                    (
                        "character_exp", target_character.master_character_id,
                        int(character_exp), target_character.exp,
                    )
                )
            db.executemany(
                """INSERT INTO state_ledger_entries (
                       transaction_id, resource_type, resource_id,
                       delta, balance_after
                   ) VALUES (?, ?, ?, ?, ?)""",
                [(transaction_id, *entry) for entry in entries if entry[2]],
            )
            return True

    @staticmethod
    def _upsert_live_result(
        db: sqlite3.Connection, user_id: int, result: dict
    ) -> None:
        now = int(result.get("updated_time", time.time()))
        is_full_combo = int(bool(result.get("is_full_combo", False)))
        is_all_perfect = int(bool(result.get("is_all_perfect", False)))
        db.execute(
                """
                INSERT INTO user_live_results (
                    user_id, master_live_id, level, clear_count, high_score,
                    feedback_musical_score, feedback_difficulty_rating, updated_time,
                    play_count, full_combo_count, all_perfect_count, max_combo,
                    first_clear_time, last_played_time, judgement_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, master_live_id, level) DO UPDATE SET
                    play_count=user_live_results.play_count + 1,
                    clear_count=user_live_results.clear_count + 1,
                    high_score=MAX(user_live_results.high_score, excluded.high_score),
                    full_combo_count=user_live_results.full_combo_count + excluded.full_combo_count,
                    all_perfect_count=user_live_results.all_perfect_count + excluded.all_perfect_count,
                    max_combo=MAX(user_live_results.max_combo, excluded.max_combo),
                    first_clear_time=COALESCE(user_live_results.first_clear_time, excluded.first_clear_time),
                    last_played_time=excluded.last_played_time,
                    judgement_json=excluded.judgement_json,
                    feedback_musical_score=excluded.feedback_musical_score,
                    feedback_difficulty_rating=excluded.feedback_difficulty_rating,
                    updated_time=excluded.updated_time
                """,
                (
                    user_id, result["master_live_id"], result["level"],
                    result.get("clear_count", 1), result.get("high_score", 0),
                    result.get("feedback_musical_score", 0),
                    result.get("feedback_difficulty_rating", 0),
                    now, 1, is_full_combo, is_all_perfect,
                    int(result.get("max_combo", 0) or 0), now, now,
                    json.dumps(result.get("judgement", {}), ensure_ascii=False),
                ),
            )

    def complete_live(
        self,
        uuid_param: str,
        data: UserGetData,
        request_data: dict,
        result: dict,
    ) -> Optional[int]:
        """Commit session close, score, growth and rewards atomically."""
        user_id = int(data.user.id)
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM live_sessions
                   WHERE user_id = ? AND status = 'started'
                     AND master_live_id = ? AND level = ?
                   ORDER BY id DESC LIMIT 1""",
                (user_id, int(result["master_live_id"]), int(result["level"])),
            ).fetchone()
            session_id = int(row["id"]) if row is not None else None
            if session_id is None:
                raise LookupError("no active live session")
            db.execute(
                """UPDATE live_sessions
                   SET status = 'cleared', result_json = ?, ended_at = ?
                   WHERE id = ?""",
                (json.dumps(request_data, ensure_ascii=False), now, session_id),
            )

            self._upsert_live_result(db, user_id, result)
            db.execute(
                """UPDATE users SET
                       uuid = ?, exp = ?, vip_point = ?, gem_total = ?,
                       gem_charge = ?, gem_free = ?, updated_at = ?
                   WHERE user_id = ?""",
                (
                    uuid_param, data.user.exp, data.user.vip_point,
                    data.gem.total, data.gem.charge, data.gem.free, now, user_id,
                ),
            )
            db.execute(
                """INSERT INTO user_wallets (
                       user_id, resource_type, resource_id, amount,
                       paid_amount, free_amount, updated_at
                   ) VALUES (?, 'gem', 0, ?, ?, ?, ?)
                   ON CONFLICT(user_id, resource_type, resource_id) DO UPDATE SET
                       amount=excluded.amount, paid_amount=excluded.paid_amount,
                       free_amount=excluded.free_amount, updated_at=excluded.updated_at""",
                (user_id, data.gem.total, data.gem.charge, data.gem.free, now),
            )
            for point in data.point_list:
                db.execute(
                    """INSERT INTO user_wallets (
                           user_id, resource_type, resource_id, amount,
                           paid_amount, free_amount, updated_at
                       ) VALUES (?, 'point', ?, ?, 0, 0, ?)
                       ON CONFLICT(user_id, resource_type, resource_id) DO UPDATE SET
                           amount=excluded.amount, updated_at=excluded.updated_at""",
                    (user_id, point.type, point.amount, now),
                )
            for character in data.character_list:
                db.execute(
                    """INSERT INTO user_characters VALUES (?, ?, ?)
                       ON CONFLICT(user_id, master_character_id) DO UPDATE SET
                           exp=excluded.exp""",
                    (user_id, character.master_character_id, character.exp),
                )
            db.execute(
                """INSERT INTO user_snapshots (user_id, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (
                    user_id,
                    json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            db.execute(
                """INSERT INTO state_transactions (
                       user_id, func_id, reason, request_json, created_at
                   ) VALUES (?, 3010, 'live_end', ?, ?)""",
                (user_id, json.dumps(request_data, ensure_ascii=False), now),
            )
            transaction_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
            for resource_type, resource_id, delta, balance in (
                ("user_exp", 0, int(result.get("user_exp_reward", 0)), data.user.exp),
                ("point", 1, int(result.get("coin_reward", 0)), next(
                    (point.amount for point in data.point_list if point.type == 1), 0
                )),
                ("gem", 0, int(result.get("gem_reward", 0)), data.gem.total),
            ):
                if delta:
                    db.execute(
                        """INSERT INTO state_ledger_entries (
                               transaction_id, resource_type, resource_id,
                               delta, balance_after
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (transaction_id, resource_type, resource_id, delta, balance),
                    )
            return session_id

    def complete_point_exchange(
        self,
        data: UserGetData,
        master_point_exchange_id: int,
        purchase_count: int,
        definition: dict,
        request_data: dict,
        costume_reward: Optional[dict] = None,
    ) -> tuple[int, list[dict]]:
        """Charge, grant and record one master-backed exchange atomically."""
        user_id = int(data.user.id)
        purchase_count = max(1, int(purchase_count))
        now = int(time.time())
        reward_type_values = {
            "GEM": 1,
            "CARD": 2,
            "ITEM": 3,
            "POINT": 4,
            "AREA_ITEM": 5,
            "COSTUME": 6,
            "MODEL_COSTUME": 7,
            "AREA_ITEM_RELEASE": 8,
            "AREA_ITEM_LEVEL_UP": 9,
            "TITLE": 10,
            "MUSIC": 11,
            "MUSIC_SHOP_RELEASE": 12,
            "MUSIC_VIDEO": 13,
            "STAMP": 14,
            "STAMINA": 15,
            "VIP_EXP": 16,
            "THREE_D_LIVE": 21,
            "LIVE_SKIN": 22,
            "LOCAL_MOVIE": 23,
        }

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            sold = db.execute(
                """SELECT exchange_count FROM user_point_exchange_sales
                   WHERE user_id = ? AND master_point_exchange_id = ?""",
                (user_id, int(master_point_exchange_id)),
            ).fetchone()
            previous_count = int(sold["exchange_count"]) if sold else 0
            total_count = previous_count + purchase_count
            exchange_limit = int(definition.get("exchange_limit", 0) or 0)
            if exchange_limit and total_count > exchange_limit:
                raise ValueError("exchange limit exceeded")

            price = int(definition.get("price", 0) or 0) * purchase_count
            consume_type = str(definition.get("consume_type", "NONE")).upper()
            consume_value = int(definition.get("consume_value", 0) or 0)
            ledger: dict[tuple[str, int], int] = {}

            if consume_type == "POINT":
                point = next(
                    (item for item in data.point_list if item.type == consume_value),
                    None,
                )
                if point is None or point.amount < price:
                    raise OverflowError("insufficient point")
                point.amount -= price
                ledger[("point", consume_value)] = -price
            elif consume_type == "ITEM":
                item = next(
                    (
                        item for item in data.item_list
                        if item.master_item_id == consume_value
                    ),
                    None,
                )
                if item is None or item.amount < price:
                    raise OverflowError("insufficient item")
                item.amount -= price
                ledger[("item", consume_value)] = -price
            elif consume_type in {"GEM", "CHARGE_GEM"}:
                available = data.gem.free if consume_type == "GEM" else data.gem.charge
                if available < price or data.gem.total < price:
                    raise OverflowError("insufficient gem")
                data.gem.total -= price
                if consume_type == "GEM":
                    data.gem.free -= price
                else:
                    data.gem.charge -= price
                ledger[("gem", 0)] = -price
            elif consume_type != "NONE" and price:
                raise ValueError(f"unsupported exchange consume type: {consume_type}")

            rewards = []
            for raw in definition.get("rewards", []) or []:
                reward_type = str(raw.get("type", "NONE")).upper()
                reward_value = int(raw.get("value", 0) or 0)
                reward_amount = int(raw.get("amount", 0) or 0) * purchase_count
                rewards.append({
                    "type": reward_type_values.get(reward_type, 0),
                    "value": reward_value,
                    "level": int(raw.get("level", 0) or 0),
                    "amount": reward_amount,
                })
                if reward_type == "GEM":
                    data.gem.total += reward_amount
                    data.gem.free += reward_amount
                    ledger[("gem", 0)] = ledger.get(("gem", 0), 0) + reward_amount
                elif reward_type == "POINT":
                    point = next(
                        (item for item in data.point_list if item.type == reward_value),
                        None,
                    )
                    if point is None:
                        point = Point(type=reward_value, amount=0)
                        data.point_list.append(point)
                    point.amount += reward_amount
                    key = ("point", reward_value)
                    ledger[key] = ledger.get(key, 0) + reward_amount
                elif reward_type == "ITEM":
                    item = next(
                        (
                            item for item in data.item_list
                            if item.master_item_id == reward_value
                        ),
                        None,
                    )
                    if item is None:
                        item = Item(
                            id=max((row.id for row in data.item_list), default=0) + 1,
                            master_item_id=reward_value,
                            amount=0,
                        )
                        data.item_list.append(item)
                    item.amount += reward_amount
                    key = ("item", reward_value)
                    ledger[key] = ledger.get(key, 0) + reward_amount

            data.item_list = [item for item in data.item_list if item.amount > 0]
            granted_costume_id = 0
            if costume_reward:
                character_id = int(costume_reward["master_character_id"])
                granted_costume_id = int(costume_reward["master_costume_id"])
                costume = next(
                    (
                        item for item in data.costume_list
                        if item.master_character_id == character_id
                    ),
                    None,
                )
                if costume is None:
                    costume = Costume(character_id, [], 0)
                    data.costume_list.append(costume)
                if granted_costume_id not in costume.master_costume_ids:
                    costume.master_costume_ids.append(granted_costume_id)
                    costume.master_costume_ids.sort()
                db.execute(
                    """INSERT OR IGNORE INTO user_costumes
                       VALUES (?, '2d', ?, ?, 0, ?)""",
                    (user_id, character_id, granted_costume_id, now),
                )

            db.execute(
                """UPDATE users SET gem_total = ?, gem_charge = ?, gem_free = ?,
                       updated_at = ? WHERE user_id = ?""",
                (data.gem.total, data.gem.charge, data.gem.free, now, user_id),
            )
            db.execute("DELETE FROM user_wallets WHERE user_id = ?", (user_id,))
            db.execute(
                "INSERT INTO user_wallets VALUES (?, 'gem', 0, ?, ?, ?, ?)",
                (
                    user_id, data.gem.total, data.gem.charge,
                    data.gem.free, now,
                ),
            )
            db.executemany(
                "INSERT INTO user_wallets VALUES (?, 'point', ?, ?, 0, 0, ?)",
                [
                    (user_id, point.type, point.amount, now)
                    for point in data.point_list
                ],
            )
            db.execute("DELETE FROM user_items WHERE user_id = ?", (user_id,))
            db.executemany(
                "INSERT INTO user_items VALUES (?, ?, ?, ?)",
                [
                    (user_id, item.id, item.master_item_id, item.amount)
                    for item in data.item_list
                ],
            )
            db.execute(
                """INSERT INTO user_point_exchange_sales (
                       user_id, master_point_exchange_id, exchange_count, sold_at
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, master_point_exchange_id) DO UPDATE SET
                       exchange_count=excluded.exchange_count,
                       sold_at=excluded.sold_at""",
                (user_id, int(master_point_exchange_id), total_count, now),
            )
            db.execute(
                """INSERT INTO user_snapshots (user_id, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (
                    user_id,
                    json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            cursor = db.execute(
                """INSERT INTO state_transactions (
                       user_id, func_id, reason, request_json, created_at
                   ) VALUES (?, 14300, 'point_exchange', ?, ?)""",
                (user_id, json.dumps(request_data, ensure_ascii=False), now),
            )
            transaction_id = int(cursor.lastrowid)

            def balance(resource_type: str, resource_id: int) -> int:
                if resource_type == "gem":
                    return int(data.gem.total)
                if resource_type == "point":
                    return next(
                        (
                            int(item.amount) for item in data.point_list
                            if item.type == resource_id
                        ),
                        0,
                    )
                return next(
                    (
                        int(item.amount) for item in data.item_list
                        if item.master_item_id == resource_id
                    ),
                    0,
                )

            db.executemany(
                """INSERT INTO state_ledger_entries (
                       transaction_id, resource_type, resource_id,
                       delta, balance_after
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        transaction_id, resource_type, resource_id,
                        delta, balance(resource_type, resource_id),
                    )
                    for (resource_type, resource_id), delta in ledger.items()
                    if delta
                ],
            )
            return total_count, rewards

    def complete_lottery_draw(
        self,
        uuid_param: str,
        data: UserGetData,
        master_lottery_id: int,
        price_number: int,
        request_data: dict,
    ) -> tuple[list[dict], int]:
        """Charge one verified local pool and record every draw atomically."""
        user_id = int(data.user.id)
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lottery = db.execute(
                """SELECT * FROM master_lotteries
                   WHERE master_lottery_id = ?
                     AND verification_status IN ('observed', 'verified')""",
                (master_lottery_id,),
            ).fetchone()
            if lottery is None:
                raise ValueError("unknown lottery")
            metadata = json.loads(lottery["data_json"])
            if int(metadata.get("master_lottery_price_number", 0)) != price_number:
                raise ValueError("unknown lottery price")
            cost = int(lottery["cost_amount"])
            if data.gem.total < cost or data.gem.free < cost:
                raise OverflowError("insufficient gem")
            items = db.execute(
                """SELECT * FROM master_lottery_items
                   WHERE master_lottery_id = ? ORDER BY sequence""",
                (master_lottery_id,),
            ).fetchall()
            if not items:
                raise ValueError("empty lottery")

            data.gem.total -= cost
            data.gem.free -= cost
            db.execute(
                """UPDATE users SET gem_total = ?, gem_free = ?, updated_at = ?
                   WHERE user_id = ?""",
                (data.gem.total, data.gem.free, now, user_id),
            )
            db.execute(
                """UPDATE user_wallets SET amount = ?, free_amount = ?, updated_at = ?
                   WHERE user_id = ? AND resource_type = 'gem' AND resource_id = 0""",
                (data.gem.total, data.gem.free, now, user_id),
            )
            cursor = db.execute(
                """INSERT INTO state_transactions (
                       user_id, func_id, reason, request_json, created_at
                   ) VALUES (?, 7010, 'lottery_draw', ?, ?)""",
                (user_id, json.dumps(request_data, ensure_ascii=False), now),
            )
            transaction_id = int(cursor.lastrowid)
            if cost:
                db.execute(
                    """INSERT INTO state_ledger_entries (
                           transaction_id, resource_type, resource_id,
                           delta, balance_after
                       ) VALUES (?, 'gem', 0, ?, ?)""",
                    (transaction_id, -cost, data.gem.total),
                )
            results = []
            for draw_index in range(int(lottery["draw_count"])):
                item = items[draw_index % len(items)]
                item_data = json.loads(item["data_json"])
                results.append({
                    "master_lottery_item_id": int(
                        item_data.get("master_lottery_item_id", master_lottery_id)
                    ),
                    "master_lottery_item_number": int(
                        item_data.get("master_lottery_item_number", item["sequence"])
                    ),
                    "is_new": 0,
                })
                db.execute(
                    """INSERT INTO lottery_draws (
                           transaction_id, user_id, master_lottery_id, draw_index,
                           reward_type, reward_id, reward_amount, random_value, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (
                        transaction_id, user_id, master_lottery_id, draw_index,
                        item["reward_type"], item["reward_id"], item["reward_amount"], now,
                    ),
                )
            db.execute(
                """INSERT INTO user_snapshots (user_id, state_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       state_json=excluded.state_json, updated_at=excluded.updated_at""",
                (
                    user_id,
                    json.dumps(data.to_dict(), ensure_ascii=False, separators=(",", ":")),
                    now,
                ),
            )
            return results, cost

    def mark_story_progress(
        self,
        user_id: int,
        master_story_part_id: int,
        *,
        read: bool = False,
        completed: bool = False,
    ) -> None:
        now = int(time.time())
        story_id = int(master_story_part_id) // 100 * 100
        with self._connect() as db:
            db.execute(
                """INSERT INTO user_story_progress (
                       user_id, master_story_id, master_story_part_id,
                       unlocked_at, read_at, completed_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, master_story_id, master_story_part_id)
                   DO UPDATE SET
                       unlocked_at=COALESCE(user_story_progress.unlocked_at, excluded.unlocked_at),
                       read_at=COALESCE(user_story_progress.read_at, excluded.read_at),
                       completed_at=COALESCE(user_story_progress.completed_at, excluded.completed_at)""",
                (
                    user_id, story_id, master_story_part_id, now,
                    now if read else None, now if completed else None,
                ),
            )

    def mark_card_episode_progress(
        self,
        user_id: int,
        card_instance_id: int,
        episode_type: int,
        *,
        read: bool = False,
        completed: bool = False,
    ) -> None:
        now = int(time.time())
        with self._connect() as db:
            db.execute(
                """INSERT INTO user_card_episode_progress (
                       user_id, card_instance_id, episode_type,
                       unlocked_at, read_at, completed_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, card_instance_id, episode_type)
                   DO UPDATE SET
                       unlocked_at=COALESCE(user_card_episode_progress.unlocked_at, excluded.unlocked_at),
                       read_at=COALESCE(user_card_episode_progress.read_at, excluded.read_at),
                       completed_at=COALESCE(user_card_episode_progress.completed_at, excluded.completed_at)""",
                (
                    user_id, card_instance_id, episode_type, now,
                    now if read else None, now if completed else None,
                ),
            )

    def lottery_list(self, user_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT l.master_lottery_id, l.data_json,
                          COUNT(d.id) AS draw_count
                   FROM master_lotteries l
                   LEFT JOIN lottery_draws d
                     ON d.master_lottery_id = l.master_lottery_id AND d.user_id = ?
                   WHERE l.verification_status IN ('observed', 'verified')
                   GROUP BY l.master_lottery_id, l.data_json
                   ORDER BY l.master_lottery_id""",
                (user_id,),
            ).fetchall()
        return [
            {
                "master_lottery_id": row["master_lottery_id"],
                "master_lottery_price_number": int(
                    json.loads(row["data_json"]).get("master_lottery_price_number", 0)
                ),
                "count": row["draw_count"],
                "daily_count": 0,
            }
            for row in rows
        ]

    def start_live_session(self, user_id: int, request_data: dict) -> int:
        """Open one durable solo-live session and supersede stale sessions."""
        now = int(time.time())
        with self._connect() as db:
            db.execute(
                """UPDATE live_sessions
                   SET status = 'superseded', ended_at = ?
                   WHERE user_id = ? AND status = 'started'""",
                (now, user_id),
            )
            cursor = db.execute(
                """INSERT INTO live_sessions (
                       user_id, master_live_id, level, status, request_json, started_at
                   ) VALUES (?, ?, ?, 'started', ?, ?)""",
                (
                    user_id,
                    int(request_data.get("master_live_id", 0)),
                    int(request_data.get("level", 1)),
                    json.dumps(request_data, ensure_ascii=False),
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def close_live_session(
        self,
        user_id: int,
        status: str,
        result_data: Optional[dict] = None,
        master_live_id: Optional[int] = None,
        level: Optional[int] = None,
    ) -> Optional[int]:
        """Close the newest matching active session, if one exists."""
        clauses = ["user_id = ?", "status = 'started'"]
        values = [user_id]
        if master_live_id is not None:
            clauses.append("master_live_id = ?")
            values.append(int(master_live_id))
        if level is not None:
            clauses.append("level = ?")
            values.append(int(level))
        with self._connect() as db:
            row = db.execute(
                f"SELECT id FROM live_sessions WHERE {' AND '.join(clauses)} "
                "ORDER BY id DESC LIMIT 1",
                values,
            ).fetchone()
            if row is None:
                return None
            session_id = int(row["id"])
            db.execute(
                """UPDATE live_sessions
                   SET status = ?, result_json = ?, ended_at = ? WHERE id = ?""",
                (
                    status,
                    json.dumps(result_data or {}, ensure_ascii=False),
                    int(time.time()),
                    session_id,
                ),
            )
            return session_id

    def load_live_results(self, user_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM user_live_results
                   WHERE user_id = ? ORDER BY master_live_id, level""",
                (user_id,),
            ).fetchall()
        return [
            {
                "master_live_id": item["master_live_id"],
                "level": item["level"],
                "clear_count": item["clear_count"],
                "high_score": item["high_score"],
                "feedback_musical_score": item["feedback_musical_score"],
                "feedback_difficulty_rating": item["feedback_difficulty_rating"],
                "updated_time": item["updated_time"],
                "play_count": item["play_count"],
                "full_combo_count": item["full_combo_count"],
                "all_perfect_count": item["all_perfect_count"],
                "max_combo": item["max_combo"],
                "first_clear_time": item["first_clear_time"],
                "last_played_time": item["last_played_time"],
                "judgement": json.loads(item["judgement_json"]),
            }
            for item in rows
        ]

    def grant_live_battle_progress(
        self, user_id: int, area_ids: list[int], stage_ids: list[int]
    ) -> None:
        """Persist completed Quest areas/stages used by unlock conditions."""
        now = int(time.time())
        with self._connect() as db:
            db.executemany(
                """INSERT INTO user_live_battle_areas VALUES (?, ?, ?)
                   ON CONFLICT(user_id, master_battle_area_id) DO UPDATE SET
                       clear_date=MAX(clear_date, excluded.clear_date)""",
                [(user_id, int(area_id), now) for area_id in area_ids],
            )
            db.executemany(
                """INSERT INTO user_live_battle_stages VALUES (?, ?, 4, 1, 1, 1, 1, ?)
                   ON CONFLICT(user_id, master_battle_stage_id) DO UPDATE SET
                       level=MAX(level, excluded.level),
                       play_count=MAX(play_count, excluded.play_count),
                       clear_count=MAX(clear_count, excluded.clear_count),
                       full_combo_count=MAX(full_combo_count, excluded.full_combo_count),
                       all_perfect_count=MAX(all_perfect_count, excluded.all_perfect_count),
                       first_clear_date=CASE WHEN first_clear_date=0
                           THEN excluded.first_clear_date ELSE first_clear_date END""",
                [(user_id, int(stage_id), now) for stage_id in stage_ids],
            )

    def load_live_battle_areas(self, user_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT master_battle_area_id, clear_date
                   FROM user_live_battle_areas WHERE user_id=?
                   ORDER BY master_battle_area_id""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_live_battle_stages(self, user_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT master_battle_stage_id, level, play_count, clear_count,
                          full_combo_count, all_perfect_count, first_clear_date
                   FROM user_live_battle_stages WHERE user_id=?
                   ORDER BY master_battle_stage_id""",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_user(self, user_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
