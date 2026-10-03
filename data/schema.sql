-- Schema reference generated from api/storage.py. No player records.

-- Created by api/identity.py for cloud mode. Stores credential digests only.
CREATE TABLE cloud_accounts (
    credential_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id),
    created_at INTEGER NOT NULL
);

CREATE TABLE api_idempotency (
                    user_id INTEGER NOT NULL,
                    func_id INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, func_id, idempotency_key),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE asset_objects (
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

CREATE TABLE content_catalog (
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

CREATE TABLE live_sessions (
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

CREATE TABLE lottery_draws (
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

CREATE TABLE master_lotteries (
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

CREATE TABLE master_lottery_items (
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

CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at INTEGER NOT NULL
                );

CREATE TABLE server_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

CREATE TABLE state_ledger_entries (
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

CREATE TABLE state_transactions (
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

CREATE TABLE user_area_items (
                    user_id INTEGER NOT NULL,
                    instance_id INTEGER NOT NULL,
                    master_area_item_id INTEGER NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
                    amount INTEGER NOT NULL DEFAULT 1 CHECK (amount >= 0),
                    PRIMARY KEY (user_id, instance_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_area_layout (
                    user_id INTEGER NOT NULL,
                    master_area_position_id INTEGER NOT NULL,
                    area_item_instance_id INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_area_position_id),
                    FOREIGN KEY (user_id, area_item_instance_id)
                        REFERENCES user_area_items(user_id, instance_id) ON DELETE CASCADE
                );

CREATE TABLE user_backstage_settings (
                    user_id INTEGER NOT NULL,
                    position_number INTEGER NOT NULL,
                    setting_type INTEGER NOT NULL,
                    setting_id INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, position_number),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_card_episode_progress (
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

CREATE TABLE user_card_subs (
                    user_id INTEGER NOT NULL,
                    instance_id INTEGER NOT NULL,
                    master_card_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    PRIMARY KEY (user_id, instance_id),
                    UNIQUE (user_id, master_card_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_cards (
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

CREATE TABLE user_characters (
                    user_id INTEGER NOT NULL,
                    master_character_id INTEGER NOT NULL,
                    exp INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, master_character_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_content_reads (
                    user_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    read_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, content_type, content_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_content_unlocks (
                    user_id INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    unlocked_at INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'local_grant',
                    PRIMARY KEY (user_id, content_type, content_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_costume_selections (
                    user_id INTEGER NOT NULL,
                    costume_kind TEXT NOT NULL,
                    master_character_id INTEGER NOT NULL,
                    part_type INTEGER NOT NULL DEFAULT 0,
                    master_costume_id INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, costume_kind, master_character_id, part_type),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_costumes (
                    user_id INTEGER NOT NULL,
                    costume_kind TEXT NOT NULL,
                    master_character_id INTEGER NOT NULL,
                    master_costume_id INTEGER NOT NULL,
                    part_type INTEGER NOT NULL DEFAULT 0,
                    unlocked_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, costume_kind, master_character_id, master_costume_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_decks (
                    user_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    main_card_ids_json TEXT NOT NULL,
                    ability_card_ids_json TEXT NOT NULL,
                    PRIMARY KEY (user_id, slot),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_items (
                    user_id INTEGER NOT NULL,
                    instance_id INTEGER NOT NULL,
                    master_item_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    PRIMARY KEY (user_id, instance_id),
                    UNIQUE (user_id, master_item_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_live_battle_areas (
                    user_id INTEGER NOT NULL,
                    master_battle_area_id INTEGER NOT NULL,
                    clear_date INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, master_battle_area_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_live_battle_stages (
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

CREATE TABLE user_live_missions (
                    user_id INTEGER NOT NULL,
                    master_live_id INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    mission_id INTEGER NOT NULL,
                    completed_at INTEGER,
                    reward_received_at INTEGER,
                    PRIMARY KEY (user_id, master_live_id, level, mission_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_live_results (
                    user_id INTEGER NOT NULL,
                    master_live_id INTEGER NOT NULL,
                    level INTEGER NOT NULL,
                    clear_count INTEGER NOT NULL DEFAULT 0,
                    high_score INTEGER NOT NULL DEFAULT 0,
                    feedback_musical_score INTEGER NOT NULL DEFAULT 0,
                    feedback_difficulty_rating INTEGER NOT NULL DEFAULT 0,
                    updated_time INTEGER NOT NULL DEFAULT 0, play_count INTEGER NOT NULL DEFAULT 0, full_combo_count INTEGER NOT NULL DEFAULT 0, all_perfect_count INTEGER NOT NULL DEFAULT 0, max_combo INTEGER NOT NULL DEFAULT 0, first_clear_time INTEGER, last_played_time INTEGER NOT NULL DEFAULT 0, judgement_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (user_id, master_live_id, level),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_missions (
                    user_id INTEGER NOT NULL,
                    master_mission_id INTEGER NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    completed_at INTEGER,
                    reward_received_at INTEGER,
                    PRIMARY KEY (user_id, master_mission_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_music (
                    user_id INTEGER NOT NULL,
                    master_music_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_music_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_music_shop_releases (
                    user_id INTEGER NOT NULL,
                    master_music_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_music_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_pieces (
                    user_id INTEGER NOT NULL,
                    master_piece_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    data_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (user_id, master_piece_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_point_exchange_sales (
                    user_id INTEGER NOT NULL,
                    master_point_exchange_id INTEGER NOT NULL,
                    exchange_count INTEGER NOT NULL DEFAULT 1,
                    sold_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, master_point_exchange_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_snapshots (
                    user_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_stamina (
                    user_id INTEGER PRIMARY KEY,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
                    last_updated_time INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_story_progress (
                    user_id INTEGER NOT NULL,
                    master_story_id INTEGER NOT NULL,
                    master_story_part_id INTEGER NOT NULL,
                    unlocked_at INTEGER,
                    read_at INTEGER,
                    completed_at INTEGER,
                    PRIMARY KEY (user_id, master_story_id, master_story_part_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

CREATE TABLE user_wallets (
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

CREATE TABLE users (
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

CREATE INDEX idx_asset_objects_content
                    ON asset_objects(content_type, content_id, asset_kind);

CREATE INDEX idx_live_sessions_user_status
                    ON live_sessions(user_id, status, id);
