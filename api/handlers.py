"""
游戏 API 处理器 — 实现所有核心端点的业务逻辑

对应游戏登录流程:
  SERVER (100) → LOGIN (970) → START (980) → USER_GET (1000) → HOME (1200)
"""

import copy
import json
import time
import uuid
import logging
from pathlib import Path
from typing import Dict

from crypto.funcid import FuncId, ResultCode
from api.router import router
from api.models import (
    ParsedRequest,
    make_response,
    ServerData,
    LoginData,
    StartData,
    StartUserData,
    UserGetData,
    HomeData,
    AreaItemSetting,
    AreaItem,
    Deck,
    Costume,
)
from api.storage import UserStore
from api.card_costume_catalog import (
    available_rewards_for_card, costume_is_available,
    ensure_available_costumes, playable_costume_inventory,
)

logger = logging.getLogger("nanaon.handlers")

# ═══════════════════════════════════════════════════════════
# 服务器配置 (SERVER FuncId 100 返回给游戏)
# 走 DNS 劫持: 游戏用 https://227.hand.co.jp 访问, hosts 已指向本机,
# 所以 api_url/asset_url 保持游戏域名, 让游戏回连到被劫持的域名。
# 可被 _load_game_config() 覆盖。
# ═══════════════════════════════════════════════════════════

_GAME_CONFIG = {
    "api_url": "https://227.hand.co.jp",
    "asset_url": "https://prd-asset.227.hand.co.jp",
    "environment": "private",
    "asset_hash": "52501a157136111ea47b76da71be1aa5",
    "asset_version": 1652074651,
    "playable_minimal_cards": False,
}

# Optional five-card projection for caches that contain only the base bundles.
# The full owned-card catalog remains in SQLite.
PLAYABLE_CARD_MASTER_IDS = (
    10110001,
    10210001,
    10310001,
    10410001,
    10510001,
)


def _playable_card_projection(user_data: UserGetData) -> tuple[list[dict], list[int]]:
    """Return five real account card instances backed by recovered panels."""
    by_master = {
        int(card.master_card_id): card
        for card in user_data.card_list
        if int(card.master_card_id) in PLAYABLE_CARD_MASTER_IDS
    }
    cards: list[dict] = []
    ids: list[int] = []
    for master_card_id in PLAYABLE_CARD_MASTER_IDS:
        card = by_master.get(master_card_id)
        if card is None:
            continue
        projected = asdict_safe(card)
        # These five preserved one-star panels only contain the base artwork.
        # A recovered full-account snapshot marks every card evolved and asks
        # the client for a non-existent ``_1_panel`` texture. Keep the level
        # and breakthrough progress, but explicitly select the existing art.
        projected["evolve"] = 0
        projected["illust_change"] = 0
        cards.append(projected)
        ids.append(int(card.id))
    if len(cards) != len(PLAYABLE_CARD_MASTER_IDS):
        raise RuntimeError("preservation account is missing a playable card")
    return cards, ids


def _live_result_card_projection(user_data: UserGetData) -> list[dict]:
    """Return the five persisted members rendered by the result scene."""
    deck = next(
        (
            item for item in user_data.deck_list
            if int(item.slot) == int(user_data.user.main_deck_slot)
        ),
        user_data.deck_list[0] if user_data.deck_list else None,
    )
    card_ids = list(deck.main_card_ids[:5]) if deck is not None else []
    by_id = {int(card.id): card for card in user_data.card_list}
    cards = [asdict_safe(by_id[card_id]) for card_id in card_ids if card_id in by_id]
    if len(cards) < 5:
        used = {int(card["id"]) for card in cards}
        for card in user_data.card_list:
            if int(card.id) in used:
                continue
            cards.append(asdict_safe(card))
            if len(cards) == 5:
                break
    return cards


def configure_game(game_cfg: dict):
    """由 server.py 启动时调用，注入 config.yaml 的 game 段"""
    global BUILTIN_USER_ID
    BUILTIN_USER_ID=int(game_cfg.get('local_user_id',100004))
    _GAME_CONFIG['multi_user'] = bool(game_cfg.get('multi_user', False))
    if game_cfg:
        _GAME_CONFIG.update({
            k: game_cfg[k] for k in (
                "api_url", "asset_url", "environment",
                "asset_hash", "asset_version", "playable_minimal_cards",
            )
            if k in game_cfg
        })
        logger.info(f"Game config: {_GAME_CONFIG}")

# ═══════════════════════════════════════════════════════════
# 内存中的用户数据存储
# ═══════════════════════════════════════════════════════════

# {user_id: UserGetData}
USER_DB: Dict[int, UserGetData] = {}
USER_STORE = UserStore()
API_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data" / "api_empty_schemas.json"
CONTENT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "costume_movie_catalog.json"
UNAVAILABLE_MEDIA_PATH = Path(__file__).resolve().parent.parent / "data" / "unavailable_media.json"
POINT_EXCHANGE_COSTUME_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "point_exchange_costume_catalog.json"
)
POINT_EXCHANGE_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "point_exchange_catalog.json"
)
try:
    API_EMPTY_SCHEMAS = json.loads(API_SCHEMA_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    API_EMPTY_SCHEMAS = {}

try:
    CONTENT_CATALOG = json.loads(CONTENT_CATALOG_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    CONTENT_CATALOG = {"local_movies": [], "music_videos": []}
LOCAL_MOVIE_PATHS = {
    int(item["id"]): str(item["file_path"])
    for item in CONTENT_CATALOG.get("local_movies", [])
}
try:
    UNAVAILABLE_MEDIA = json.loads(UNAVAILABLE_MEDIA_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    UNAVAILABLE_MEDIA = {"movies": []}
UNAVAILABLE_LOCAL_MOVIE_IDS = {
    int(item["master_local_movie_id"])
    for item in UNAVAILABLE_MEDIA.get("movies", [])
    if item.get("master_local_movie_id") is not None
}
UNAVAILABLE_LIVE_MUSIC_VIDEO_IDS = {
    int(item["master_live_music_video_id"])
    for item in UNAVAILABLE_MEDIA.get("movies", [])
    if item.get("master_live_music_video_id") is not None
}
try:
    POINT_EXCHANGE_COSTUME_REWARDS = json.loads(
        POINT_EXCHANGE_COSTUME_PATH.read_text(encoding="utf-8")
    ).get("costume_rewards", {})
except (OSError, ValueError):
    POINT_EXCHANGE_COSTUME_REWARDS = {}
try:
    POINT_EXCHANGE_CATALOG = json.loads(
        POINT_EXCHANGE_CATALOG_PATH.read_text(encoding="utf-8")
    ).get("exchanges", {})
except (OSError, ValueError):
    POINT_EXCHANGE_CATALOG = {}

# {access_token: user_id}
TOKEN_MAP: Dict[str, int] = {}

# The v2.4.0 client does not include user_id in USER_GET and several other
# post-login request bodies.  On this single-client preservation server the
# most recent successful device login is therefore the authoritative session
# fallback when neither an explicit id nor an Authorization token is present.
_LAST_AUTHENTICATED_USER_ID: int | None = None

# {uuid: user_id}
UUID_MAP: Dict[str, int] = {}
BUILTIN_USER_ID = 100004


def _get_or_create_user(uuid_param: str) -> int:
    """Map every local client installation to the bundled single-player user."""
    if uuid_param in UUID_MAP:
        return UUID_MAP[uuid_param]
    user_data = USER_STORE.load_user(BUILTIN_USER_ID)
    if user_data is None:
        user_data = UserGetData.create_default(BUILTIN_USER_ID)
    ensure_available_costumes(user_data)
    USER_DB[BUILTIN_USER_ID] = user_data
    USER_STORE.save_user(uuid_param, user_data)
    USER_STORE.complete_story_catalog(BUILTIN_USER_ID, user_data.story_list)
    UUID_MAP[uuid_param] = BUILTIN_USER_ID
    return BUILTIN_USER_ID


def _get_user_by_token(access_token: str) -> int:
    """根据 access_token 查找用户"""
    return TOKEN_MAP.get(access_token, _LAST_AUTHENTICATED_USER_ID or BUILTIN_USER_ID)


def _request_user_id(request: ParsedRequest) -> int:
    if request.authenticated_user_id is not None:
        return request.authenticated_user_id
    if _GAME_CONFIG.get('multi_user'):
        raise PermissionError('authenticated player required')
    if request.user_id:
        return int(request.user_id)
    if request.access_token:
        token_user = TOKEN_MAP.get(request.access_token)
        if token_user is not None:
            return token_user
    return _LAST_AUTHENTICATED_USER_ID or BUILTIN_USER_ID


# ═══════════════════════════════════════════════════════════
# SERVER — FuncId 100
# 获取服务器地址 — 这是登录流程第一步
# ═══════════════════════════════════════════════════════════

@router.register(FuncId.SERVER)
def handle_server(request: ParsedRequest) -> dict:
    """
    返回本地服务器地址
    对应: MngLoginData.InitEntrypoint() → RecvServerR

    游戏使用此数据:
      server_data.api_url → 设置 NetworkEngine.m_ServerUrl
      server_data.asset_url → 设置 AssetServerURL
      server_data.environment → 用于 Photon Server 设置
    """
    server_data = ServerData(
        api_url=_GAME_CONFIG["api_url"],
        asset_url=_GAME_CONFIG["asset_url"],
        environment=_GAME_CONFIG["environment"],
    )
    return make_response(data=asdict_safe(server_data))


# ═══════════════════════════════════════════════════════════
# LOGIN — FuncId 970
# 设备登录 — 发送 UUID + AppsFlyer ID
# ═══════════════════════════════════════════════════════════

@router.register(FuncId.LOGIN)
def handle_login(request: ParsedRequest) -> dict:
    """
    设备登录
    参数: uuid, appsflyer_id
    返回: access_token, take_over_info

    对应 send_login_impl() → RecvLoginR
    """
    global _LAST_AUTHENTICATED_USER_ID
    uuid_param = request.uuid_param or str(uuid.uuid4())
    user_id = request.authenticated_user_id if request.authenticated_user_id is not None else _get_or_create_user(uuid_param)

    login_data = LoginData.create()
    if request.authenticated_user_id is None:
        TOKEN_MAP[login_data.access_token] = user_id
        _LAST_AUTHENTICATED_USER_ID = user_id

    logger.info("LOGIN completed")

    return make_response(data=asdict_safe(login_data))


# ═══════════════════════════════════════════════════════════
# START — FuncId 980
# 会话启动 — 在登录后调用
# ═══════════════════════════════════════════════════════════

@router.register(FuncId.START)
def handle_start(request: ParsedRequest) -> dict:
    """
    会话启动
    返回: asset_hash, asset_version, token, screening_version, is_service_closed

    对应 MngLoginData.SendStartApi() → RecvStartR
    """
    start_data = StartData.create()
    start_data.asset_hash = _GAME_CONFIG["asset_hash"]
    start_data.asset_version = int(_GAME_CONFIG["asset_version"])

    # 不标记为停服
    start_data.is_service_closed = False

    logger.info("START completed")

    return make_response(data=asdict_safe(start_data))


# ═══════════════════════════════════════════════════════════
# USER_GET — FuncId 1000
# 获取用户完整数据
# ═══════════════════════════════════════════════════════════

@router.register(FuncId.START_USER)
def handle_start_user(request: ParsedRequest) -> dict:
    """Device authentication/session recovery for RecvStartUserRData."""
    global _LAST_AUTHENTICATED_USER_ID
    uuid_param = request.uuid_param or str(uuid.uuid4())
    user_id = request.authenticated_user_id if request.authenticated_user_id is not None else _get_or_create_user(uuid_param)
    access_token = str(uuid.uuid4())
    if request.authenticated_user_id is None:
        TOKEN_MAP[access_token] = user_id
        _LAST_AUTHENTICATED_USER_ID = user_id

    data = StartUserData(
        user_id=user_id,
        access_token=access_token,
        is_service_closed=False,
        message=None,
    )
    logger.info("START_USER completed")
    return make_response(data=asdict_safe(data))


@router.register(FuncId.USER_GET)
def handle_user_get(request: ParsedRequest) -> dict:
    """
    返回用户完整数据 (User, Cards, Items, Decks, Stamina, etc.)
    对应 RecvUserGetRData (TypeDefIndex: 17125)
    """
    user_id = _request_user_id(request)

    # SQLite is the authority.  Reload on every USER_GET so a maintenance
    # grant or a previous committed request cannot be hidden by process memory.
    user_data = USER_STORE.load_user(user_id) or UserGetData.create_default(user_id)
    USER_DB[user_id] = user_data
    _repair_exchange_costume_ownership(user_data)
    from api.area_layout import ensure_room_layout
    layout_changed = ensure_room_layout(user_data)
    if ensure_available_costumes(user_data) or layout_changed:
        _persist_user(user_data)

    # The v2.4.0 client sends the title selection to POST /api/user, but that
    # path is identified as USER_GET (1000) by the endpoint table.  Treat the
    # presence of the update field as the discriminator, persist it, and still
    # return the full USER_GET payload expected by the client.
    request_payload = request.json_data or {}
    if any(key in request_payload for key in ("master_title_ids", "favorite_master_card_id", "favorite_card_evolve")):
        if not _apply_user_profile_update(user_data, request_payload):
            return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
        _persist_user(user_data)
        logger.info("USER_GET profile update persisted: user=%s", user_id)

    payload = user_data.to_dict()
    payload["costume_list"] = playable_costume_inventory(user_data)
    # Do not advertise media which the preservation package cannot serve.
    # A 404 makes the v2.4.0 downloader retry forever; omitting ownership lets
    # the client keep the corresponding movie/MV option unavailable instead.
    payload["master_live_music_video_ids"] = [
        item for item in payload.get("master_live_music_video_ids", [])
        if int(item) not in UNAVAILABLE_LIVE_MUSIC_VIDEO_IDS
    ]
    payload["local_movie_detail_list"] = [
        item for item in payload.get("local_movie_detail_list", [])
        if not isinstance(item, dict)
        or int(item.get("id", item.get("master_local_movie_id", 0)) or 0)
        not in UNAVAILABLE_LOCAL_MOVIE_IDS
    ]
    if _GAME_CONFIG.get("playable_minimal_cards"):
        # Project five real, distinct account instances whose CardPanel assets
        # are present in the distributable resource pack.
        playable_cards, playable_card_ids = _playable_card_projection(user_data)
        payload["card_list"] = playable_cards
        payload["deck_list"] = [{
            "slot": 1,
            "main_card_ids": playable_card_ids,
            "ability_card_ids": [],
        }]
        payload["user"]["main_deck_slot"] = 1
        if payload["user"]["favorite_master_card_id"] not in PLAYABLE_CARD_MASTER_IDS:
            payload["user"]["favorite_master_card_id"] = PLAYABLE_CARD_MASTER_IDS[0]
        payload["user"]["favorite_card_evolve"] = 0

    logger.info(
        "USER_GET: user_id=%s cards=%s exposed_cards=%s decks=%s music=%s "
        "shop=%s stories=%s story_parts=%s",
        user_id,
        len(user_data.card_list),
        len(payload.get("card_list", [])),
        len(user_data.deck_list),
        len(user_data.master_music_ids),
        len(user_data.music_shop_releases),
        len(user_data.story_list),
        sum(
            len(item.get("master_story_part_ids", []))
            for item in user_data.story_list
            if isinstance(item, dict)
        ),
    )

    return make_response(data=payload)


# ═══════════════════════════════════════════════════════════
# USER_INITIALIZE — FuncId 1010
# 初始化新用户
# ═══════════════════════════════════════════════════════════

@router.register(FuncId.USER_INITIALIZE)
def handle_user_initialize(request: ParsedRequest) -> dict:
    """初始化新用户 — 返回用户数据"""
    # 从请求中解析用户信息
    json_data = request.json_data or {}
    user_name = json_data.get("name", "ナナオンPlayer")
    birth_date = json_data.get("birth_date")

    user_id = _request_user_id(request)
    user_data = UserGetData.create_default(user_id)
    user_data.user.name = user_name
    USER_DB[user_id] = user_data
    uuid_param = USER_STORE.uuid_for_user_id(user_id)
    if uuid_param:
        USER_STORE.save_user(uuid_param, user_data)

    logger.info(f"USER_INITIALIZE: user_id={user_id}, name={user_name}")

    # RecvUserInitializeRData contains only ``user``.
    return make_response(data={"user": asdict_safe(user_data.user)})


# ═══════════════════════════════════════════════════════════
# HOME — FuncId 1200
# 主页数据
# ═══════════════════════════════════════════════════════════

@router.register(FuncId.HOME)
def handle_home(request: ParsedRequest) -> dict:
    """
    返回主页数据
    对应 RecvHomeRData (TypeDefIndex: 17149)
    """
    home = HomeData()
    return make_response(data={"home": asdict_safe(home)})


def _request_user(request: ParsedRequest) -> UserGetData:
    """Return the durable local user used by mutation-style endpoints."""
    user_id = _request_user_id(request)
    USER_DB[user_id] = (
        USER_STORE.load_user(user_id) or UserGetData.create_default(user_id)
    )
    if ensure_available_costumes(USER_DB[user_id]):
        _persist_user(USER_DB[user_id])
    return USER_DB[user_id]


def _empty_updated_values(user_data: UserGetData) -> dict:
    """Fully initialize UpdatedValueList so client collection merges are safe."""
    return {
        "gem": asdict_safe(user_data.gem),
        "card_list": [],
        "card_breakthrough_list": [],
        "card_sub_list": [],
        "item_list": [],
        "point_list": [],
        "area_item_list": [],
        "released_master_area_item_ids": [],
        "level_up_area_item_list": [],
        "master_title_ids": [],
        "master_costume_ids": [],
        "model_costumes": [],
        "master_music_ids": [],
        "master_live_music_video_ids": [],
        "vip_exp": None,
        "master_live_three_d_ids": [],
        "master_stamp_ids": [],
        "event_point_list": [],
        "master_live_lane_skin_ids": [],
        "stamina": asdict_safe(user_data.stamina),
        "live_battle_point": None,
        "character_list": [],
        "piece_list": [],
        "ability_card_list": [],
        "ability_card_level_up_list": [],
        "master_local_movie_ids": [],
    }


def _persist_user(user_data: UserGetData) -> None:
    """Persist a mutation without replacing the authenticated UUID."""
    uuid_param = USER_STORE.uuid_for_user_id(user_data.user.id)
    if uuid_param:
        USER_STORE.save_user(uuid_param, user_data)


def _repair_exchange_costume_ownership(user_data: UserGetData) -> list[int]:
    """Backfill costume rewards for exchanges sold by older server builds."""
    with USER_STORE._connect() as db:
        sold_rows = db.execute(
            """SELECT master_point_exchange_id
               FROM user_point_exchange_sales WHERE user_id = ?""",
            (user_data.user.id,),
        ).fetchall()
    repaired: list[int] = []
    for sold in sold_rows:
        reward = POINT_EXCHANGE_COSTUME_REWARDS.get(
            str(int(sold["master_point_exchange_id"]))
        )
        if not reward:
            continue
        character_id = int(reward["master_character_id"])
        costume_id = int(reward["master_costume_id"])
        costume = next(
            (
                item for item in user_data.costume_list
                if item.master_character_id == character_id
            ),
            None,
        )
        if costume is None:
            costume = Costume(character_id, [], 0)
            user_data.costume_list.append(costume)
        if costume_id not in costume.master_costume_ids:
            costume.master_costume_ids.append(costume_id)
            costume.master_costume_ids.sort()
            repaired.append(costume_id)
    if repaired:
        _persist_user(user_data)
        logger.info(
            "Repaired exchange costume ownership: user=%s costumes=%s",
            user_data.user.id,
            repaired,
        )
    return repaired


def _point(user_data: UserGetData, point_type: int):
    point = next((item for item in user_data.point_list if item.type == point_type), None)
    if point is None:
        from api.models import Point
        point = Point(type=point_type, amount=0)
        user_data.point_list.append(point)
    return point


@router.register(FuncId.USER_INFO)
def handle_user_info(request: ParsedRequest) -> dict:
    return make_response(data={"access_token": request.access_token or "", "data": None})


@router.register(FuncId.USER_DETAIL)
def handle_user_detail(request: ParsedRequest) -> dict:
    return make_response(data={"user_detail_list": []})


@router.register(FuncId.USER_MIGRATION)
def handle_user_migration(request: ParsedRequest) -> dict:
    user = _request_user(request).user
    return make_response(data={"user_name": user.name, "user_rank": 50})


def _apply_user_profile_update(user_data: UserGetData, payload: dict) -> bool:
    """Validate the entire profile edit before changing any selected member."""
    user = copy.deepcopy(user_data.user)
    try:
        for key in ("name", "comment"):
            if key in payload:
                if not isinstance(payload[key], str):
                    return False
                setattr(user, key, payload[key])
        if "main_deck_slot" in payload:
            slot = int(payload["main_deck_slot"])
            if slot not in {int(deck.slot) for deck in user_data.deck_list}:
                return False
            user.main_deck_slot = slot
        if "favorite_master_card_id" in payload or "favorite_card_evolve" in payload:
            master_id = int(payload.get("favorite_master_card_id", user.favorite_master_card_id))
            # A new favorite defaults to its base illustration if an older
            # client omits the evolution field; never inherit another card's.
            evolve = int(payload.get("favorite_card_evolve", 0))
            owned = [card for card in user_data.card_list if int(card.master_card_id) == master_id]
            if not owned or evolve not in (0, 1) or (evolve and not any(card.evolve for card in owned)):
                return False
            if _GAME_CONFIG.get("playable_minimal_cards") and (master_id not in PLAYABLE_CARD_MASTER_IDS or evolve):
                return False
            user.favorite_master_card_id = master_id
            user.favorite_card_evolve = evolve
        if "master_title_ids" in payload:
            selected = [int(item) for item in payload["master_title_ids"] or []]
            if len(selected) > 3 or any(item not in user_data.master_title_ids for item in selected):
                return False
            user.master_title_ids = selected
    except (TypeError, ValueError, OverflowError):
        return False
    user_data.user = user
    return True


@router.register(FuncId.USER_UPDATE)
@router.register(FuncId.USER_UPDATE_FAVORITE_CARD_ID)
@router.register(FuncId.USER_UPDATE_TITLE_ID)
def handle_user_update(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    if not _apply_user_profile_update(user_data, payload):
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    _persist_user(user_data)
    return make_response(data={"user": asdict_safe(user_data.user), "clear_mission_ids": []})


@router.register(FuncId.USER_UPDATE_MAIN_DECK_SLOT)
@router.register(FuncId.USER_UPDATE_PROFILE_SETTINGS)
@router.register(FuncId.USER_UPDATE_BIRTH_DATE)
def handle_user_settings_update(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    # Settings requests must never write identity or progress fields. In cloud
    # mode an arbitrary user.id here could otherwise redirect a later save.
    if any(key in payload for key in (
        "id", "exp", "vip_point", "last_login_time",
        "nanacomi_shop_dialog_unconfirmed",
    )):
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    if request.func_id == FuncId.USER_UPDATE_MAIN_DECK_SLOT:
        if "main_deck_slot" not in payload or not _apply_user_profile_update(
            user_data, {"main_deck_slot": payload["main_deck_slot"]}
        ):
            return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    elif request.func_id == FuncId.USER_UPDATE_PROFILE_SETTINGS:
        settings = payload.get("profile_settings")
        if not isinstance(settings, list) or len(settings) > 128 or any(
            type(value) is not int or not 0 <= value <= 2_147_483_647
            for value in settings
        ):
            return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
        user_data.user.profile_settings = list(settings)
    elif request.func_id == FuncId.USER_UPDATE_BIRTH_DATE:
        birth_date = payload.get("birth_date")
        if not isinstance(birth_date, str) or len(birth_date) > 32:
            return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
        user_data.user.birth_date = birth_date
    _persist_user(user_data)
    return make_response(data={"user": asdict_safe(user_data.user)})


@router.register(FuncId.DECK_UPDATE)
def handle_deck_update(request: ParsedRequest) -> dict:
    """Validate owned card instances and persist one deck slot."""
    user_data = _request_user(request)
    payload = request.json_data or {}
    slot = int(payload.get("slot", 1) or 1)
    owned = {int(card.id) for card in user_data.card_list}
    # Empty deck positions are encoded as instance id 0 by the v2.4.0
    # client.  Zero is a placeholder, not an owned card instance.
    main_ids = [int(item) for item in payload.get("main_card_ids", []) or [] if int(item)]
    support_ids = [
        int(item) for item in payload.get("support_card_ids", []) or [] if int(item)
    ]
    other_ids = [int(item) for item in payload.get("other_card_ids", []) or [] if int(item)]
    if any(item not in owned for item in main_ids + support_ids + other_ids):
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    deck = Deck(
        slot=slot,
        main_card_ids=main_ids,
        ability_card_ids=support_ids + other_ids,
    )
    user_data.deck_list = [item for item in user_data.deck_list if item.slot != slot]
    user_data.deck_list.append(deck)
    user_data.deck_list.sort(key=lambda item: item.slot)
    _persist_user(user_data)
    return make_response(data={"deck": asdict_safe(deck)})


@router.register(FuncId.GIFT_GET)
def handle_gift_get(request: ParsedRequest) -> dict:
    return make_response(data={"gift_list": []})


@router.register(FuncId.GIFT_RECEIVE)
def handle_gift_receive(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    return make_response(data={
        "failed_gift_ids": [],
        "expire_gift_ids": [],
        "updated_value_list": _empty_updated_values(user_data),
        "clear_mission_ids": [],
        "reward_list": [],
    })


@router.register(FuncId.ITEM_USE)
def handle_item_use(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    requested = (request.json_data or {}).get("use_item_list", []) or []
    by_id = {int(item.id): item for item in user_data.item_list}
    for raw in requested:
        item = by_id.get(int(raw.get("id", 0) or 0))
        amount = int(raw.get("amount", 0) or 0)
        if item is None or amount <= 0 or item.amount < amount:
            return make_response(data=None, code=ResultCode.ERROR_INSUFFICIENT_ITEM)
    for raw in requested:
        by_id[int(raw["id"])].amount -= int(raw["amount"])
    user_data.item_list = [item for item in user_data.item_list if item.amount > 0]
    _persist_user(user_data)
    return make_response(data={
        "item_list": [asdict_safe(item) for item in user_data.item_list],
        "stamina": asdict_safe(user_data.stamina),
    })


@router.register(FuncId.LIVE_START)
def handle_live_start(request: ParsedRequest) -> dict:
    """Open a durable solo-live session before chart loading starts."""
    payload = request.json_data or {}
    user_data = _request_user(request)
    user_id = user_data.user.id
    if USER_STORE.uuid_for_user_id(user_id):
        session_id = USER_STORE.start_live_session(user_id, payload)
        logger.info(
            "LIVE_START session=%s user=%s live=%s level=%s",
            session_id, user_id, payload.get("master_live_id"), payload.get("level"),
        )
    return make_response(data={})


@router.register(FuncId.LIVE_RETIRE)
def handle_live_retire(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    session_id = USER_STORE.close_live_session(
        user_data.user.id,
        "retired",
        payload,
        payload.get("master_live_id"),
        payload.get("level"),
    )
    logger.info("LIVE_RETIRE session=%s user=%s", session_id, user_data.user.id)
    return make_response(data={
        "stamina": asdict_safe(user_data.stamina),
        "event_point": None,
    })


@router.register(FuncId.LIVE_CONTINUE)
def handle_live_continue(request: ParsedRequest) -> dict:
    return make_response(data={"gem": asdict_safe(_request_user(request).gem)})


@router.register(FuncId.LIVE_VIEWING)
def handle_live_viewing(request: ParsedRequest) -> dict:
    return make_response(data={"clear_mission_ids": []})


@router.register(FuncId.LIVE)
def handle_live_list(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    user_data.live_list = USER_STORE.load_live_results(user_data.user.id)
    return make_response(data={"live_list": user_data.live_list})


@router.register(FuncId.LIVE_SETUP)
def handle_live_setup(request: ParsedRequest) -> dict:
    return make_response(data={"can_multi": False})


@router.register(FuncId.LIVE_END)
def handle_live_end(request: ParsedRequest) -> dict:
    """Return a fully initialized RecvLiveEndRData."""
    payload = request.json_data or {}
    user_id = _request_user_id(request)
    if user_id not in USER_DB:
        USER_DB[user_id] = UserGetData.create_default(user_id)
    user_data = USER_DB[user_id]

    master_live_id = int(payload.get("master_live_id", 0))
    level = int(payload.get("level", 1))
    live_score = payload.get("live_score") or {}
    user_exp = 100
    character_exp = 50
    card_exp = 0  # base-account cards intentionally start at level 1
    coin_reward = 1_000
    user_data.user.exp += user_exp
    coin = _point(user_data, 1)
    coin.amount += coin_reward
    for character in user_data.character_list:
        character.exp += character_exp
    is_all_perfect = bool(int(live_score.get("is_all_perfect", 0) or 0))
    is_full_combo = is_all_perfect or bool(
        int(live_score.get("is_full_combo", 0) or 0)
    )
    result_rewards = [{"type": 4, "value": 1, "level": 0, "amount": coin_reward}]
    if is_full_combo:
        result_rewards.append({"type": 1, "value": 0, "level": 0, "amount": 50})
        user_data.gem.total += 50
        user_data.gem.free += 50
    if is_all_perfect:
        result_rewards.append({"type": 1, "value": 0, "level": 0, "amount": 100})
        user_data.gem.total += 100
        user_data.gem.free += 100
    live_result = {
            "master_live_id": master_live_id,
            "level": level,
            "clear_count": 1,
            "high_score": int(live_score.get("score", 0)),
            "feedback_musical_score": 0,
            "feedback_difficulty_rating": 0,
            "updated_time": int(time.time()),
            "is_full_combo": is_full_combo,
            "is_all_perfect": is_all_perfect,
            "max_combo": int(live_score.get("max_combo", 0) or 0),
            "judgement": {
                key: value for key, value in live_score.items()
                if key in ("perfect", "great", "good", "bad", "miss")
            },
            "user_exp_reward": user_exp,
            "coin_reward": coin_reward,
            "gem_reward": (150 if is_all_perfect else (50 if is_full_combo else 0)),
        }
    result_cards = _live_result_card_projection(user_data)
    if _GAME_CONFIG.get("playable_minimal_cards"):
        result_cards, _ = _playable_card_projection(user_data)
    data = {
        "live": live_result,
        "all_perfected_date": (
            time.strftime("%Y-%m-%d %H:%M:%S") if is_all_perfect else ""
        ),
        "clear_live_mission_ids": [],
        "user": asdict_safe(user_data.user),
        "stamina": asdict_safe(user_data.stamina),
        "gem": asdict_safe(user_data.gem),
        # LiveResultCardExp renders this exact response collection. Returning
        # an empty list leaves five gray placeholders even though USER_GET and
        # the gameplay HUD have the card data.
        "card_list": result_cards,
        "card_sub_list": [],
        "card_breakthrough_list": [],
        "item_list": [],
        "point_list": [asdict_safe(coin)],
        "character_list": [asdict_safe(item) for item in user_data.character_list],
        "area_item_list": [],
        "released_master_area_item_ids": [],
        "level_up_area_item_list": [],
        "reward_list": result_rewards,
        "remove_reward_list": [],
        "clear_mission_ids": [],
        "event_point_list": [],
        "event_point_reward_list": [],
        "try_event": None,
        "showdown_event": None,
        "add_exp": {
            "user_exp": user_exp,
            "character_exp": character_exp,
            "card_exp": card_exp,
        },
        "master_title_ids": [],
        "master_costume_ids": [],
        "model_costumes": [],
        "master_music_ids": [],
        "master_stamp_ids": [],
        "rank_up_rewards": [],
        "piece_list": [],
        "convert_piece_list": [],
        "card_breakthrough_reward_list": [],
        "master_local_movie_ids": [],
        "ability_card_list": [],
        "ability_card_level_up_list": [],
        "ability_level_up_reward_list": [],
    }
    logger.info(
        "LIVE_END: master_live_id=%s level=%s score=%s",
        master_live_id,
        level,
        live_score.get("score", 0),
    )
    if USER_STORE.uuid_for_user_id(user_id):
        uuid_param = USER_STORE.uuid_for_user_id(user_id)
        try:
            session_id = USER_STORE.complete_live(
                uuid_param, user_data, payload, live_result
            )
        except LookupError:
            # Reward values were prepared in memory before the atomic store
            # rejected the duplicate. Reload the committed projection so a
            # later USER_GET cannot expose uncommitted growth.
            committed = USER_STORE.load_user(user_id)
            if committed is not None:
                USER_DB[user_id] = committed
            logger.warning(
                "LIVE_END rejected without active session: user=%s live=%s level=%s",
                user_id, master_live_id, level,
            )
            return make_response(data=None, code=ResultCode.ERROR_SESSION_EXPIRED)
        user_data.live_list = USER_STORE.load_live_results(user_id)
        logger.info("LIVE_END persisted session=%s user=%s", session_id, user_id)
    return make_response(data=data)


@router.register(FuncId.LIVE_BATTLE_START)
def handle_live_battle_start(request: ParsedRequest) -> dict:
    return make_response(data={})


@router.register(FuncId.LIVE_BATTLE_END)
def handle_live_battle_end(request: ParsedRequest) -> dict:
    """Complete the offline battle result with concrete rewards and counters."""
    payload = request.json_data or {}
    user_data = _request_user(request)
    live_score = payload.get("live_score") or {}
    user_exp = 100
    character_exp = 50
    coin_reward = 1_000
    user_data.user.exp += user_exp
    coin = _point(user_data, 1)
    coin.amount += coin_reward
    for character in user_data.character_list:
        character.exp += character_exp
    is_all_perfect = bool(int(live_score.get("is_all_perfect", 0) or 0))
    is_full_combo = is_all_perfect or bool(
        int(live_score.get("is_full_combo", 0) or 0)
    )
    gem_reward = 100 if is_all_perfect else (50 if is_full_combo else 0)
    rewards = [{"type": 4, "value": 1, "level": 0, "amount": coin_reward}]
    if gem_reward:
        user_data.gem.total += gem_reward
        user_data.gem.free += gem_reward
        rewards.append({"type": 1, "value": 0, "level": 0, "amount": gem_reward})
    now = int(time.time())
    stage_id = int(payload.get("master_battle_stage_id", 0) or 0)
    level = int(payload.get("level", 1) or 1)
    stage = {
        "master_battle_stage_id": stage_id,
        "level": level,
        "play_count": 1,
        "clear_count": 1,
        "full_combo_count": 1 if is_full_combo else 0,
        "all_perfect_count": 1 if is_all_perfect else 0,
        "first_clear_date": now,
    }
    data = copy.deepcopy(API_EMPTY_SCHEMAS[str(int(FuncId.LIVE_BATTLE_END))]["defaults"])
    data.update({
        "live": None,
        "all_perfected_date": (
            time.strftime("%Y-%m-%d %H:%M:%S") if is_all_perfect else ""
        ),
        "user": asdict_safe(user_data.user),
        "stamina": asdict_safe(user_data.stamina),
        "gem": asdict_safe(user_data.gem),
        "point_list": [asdict_safe(coin)],
        "character_list": [asdict_safe(item) for item in user_data.character_list],
        "reward_list": rewards,
        "first_clear_reward_list": rewards,
        "add_exp": {
            "user_exp": user_exp,
            "character_exp": character_exp,
            "card_exp": 0,
        },
        "rank_up_rewards": [],
        "live_battle_stage": stage,
        "live_battle_area": None,
        "live_battle_point": {"live_battle_point": 99, "last_updated_time": now},
    })
    _persist_user(user_data)
    return make_response(data=data)


# ═══════════════════════════════════════════════════════════
# CLIENT_ERROR — FuncId 150
# 客户端错误上报
# ═══════════════════════════════════════════════════════════

@router.register(FuncId.LIVE_MUSIC)
def handle_live_music(request: ParsedRequest) -> dict:
    """RecvLiveMusicRData requires a non-null list."""
    return make_response(data={"live_music_list": []})


@router.register(FuncId.LIVE_BATTLE_AREA)
def handle_live_battle_area(request: ParsedRequest) -> dict:
    """Return durable Quest area completion instead of the old empty stub."""
    return make_response(data={
        "live_battle_area_list": USER_STORE.load_live_battle_areas(
            _request_user_id(request)
        )
    })


@router.register(FuncId.LIVE_BATTLE_STAGE)
def handle_live_battle_stage(request: ParsedRequest) -> dict:
    """Return durable Quest stage completion used by cover-song releases."""
    return make_response(data={
        "live_battle_stage_list": USER_STORE.load_live_battle_stages(
            _request_user_id(request)
        )
    })


@router.register(FuncId.MUSICAL_SCORE)
def handle_musical_score(request: ParsedRequest) -> dict:
    """RecvMusicalScoreRData requires a non-null list."""
    return make_response(data={"musical_score_list": []})


@router.register(FuncId.MUSICAL_SCORE_GET_FAVORITE_COUNT)
def handle_musical_score_favorite_count(request: ParsedRequest) -> dict:
    return make_response(data={"favorite_count": 0})


@router.register(FuncId.MUSICAL_SCORE_MATERIAL)
def handle_musical_score_material(request: ParsedRequest) -> dict:
    """Return an initialized collection; the client immediately calls Select."""
    return make_response(data={"master_musical_score_material_ids": []})


@router.register(FuncId.TUTORIAL_SKIP)
def handle_tutorial_skip(request: ParsedRequest) -> dict:
    """Mark the current local account's tutorial as completed.

    ``RecvTutorialSkipRData`` is intentionally empty; the durable value is
    exposed by the next USER_GET response.
    """
    user_id = _request_user_id(request)
    if user_id not in USER_DB:
        USER_DB[user_id] = UserGetData.create_default(user_id)
    USER_DB[user_id].tutorial_progress = 70
    uuid_param = USER_STORE.uuid_for_user_id(user_id)
    if uuid_param:
        USER_STORE.save_user(uuid_param, USER_DB[user_id])
    logger.info(
        "TUTORIAL_SKIP: user_id=%s, skipped_progress=%s -> progress=70",
        user_id,
        (request.json_data or {}).get("skipped_progress"),
    )
    return make_response(data={})


@router.register(FuncId.MESSAGE)
def handle_message(request: ParsedRequest) -> dict:
    """RecvMessageRData: the client immediately enumerates message_list."""
    return make_response(data={"message_list": [], "unread_count": 0})


@router.register(FuncId.FRIEND_GET)
def handle_friend_get(request: ParsedRequest) -> dict:
    """RecvFriendRData: MngFriendData expects an iterable friend_list."""
    return make_response(data={"friend_list": []})


@router.register(FuncId.MEMBER_MULTI_LIVE_PERIOD)
def handle_member_multi_live_period(request: ParsedRequest) -> dict:
    """The home bootstrap immediately enumerates this period list."""
    return make_response(data={"member_multi_live_period_list": []})


@router.register(FuncId.POINT_EXCHANGE_GET)
def handle_point_exchange_get(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    with USER_STORE._connect() as db:
        rows = db.execute(
            """SELECT master_point_exchange_id, exchange_count
               FROM user_point_exchange_sales WHERE user_id = ?
               ORDER BY master_point_exchange_id""",
            (user_data.user.id,),
        ).fetchall()
    _repair_exchange_costume_ownership(user_data)
    return make_response(data={
        "point_exchange_list": [
            {
                "master_point_exchange_id": int(row["master_point_exchange_id"]),
                "exchange_count": int(row["exchange_count"]),
            }
            for row in rows
        ]
    })


@router.register(FuncId.POINT_EXCHANGE)
def handle_point_exchange(request: ParsedRequest) -> dict:
    """Charge and persist one real master-backed point exchange."""
    user_data = _request_user(request)
    payload = request.json_data or {}
    exchange_id = int(payload.get("master_point_exchange_id", 0) or 0)
    exchange_count = max(1, int(payload.get("exchange_count", 1) or 1))
    definition = POINT_EXCHANGE_CATALOG.get(str(exchange_id))
    if not definition:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    costume_reward = POINT_EXCHANGE_COSTUME_REWARDS.get(str(exchange_id))
    try:
        total_count, reward_list = USER_STORE.complete_point_exchange(
            user_data,
            exchange_id,
            exchange_count,
            definition,
            payload,
            costume_reward,
        )
    except OverflowError:
        return make_response(data=None, code=ResultCode.ERROR_INSUFFICIENT_ITEM)
    except ValueError:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)

    exchange = {
        "master_point_exchange_id": exchange_id,
        "exchange_count": total_count,
    }
    updated_values = _empty_updated_values(user_data)
    updated_values["gem"] = asdict_safe(user_data.gem)
    updated_values["point_list"] = [
        asdict_safe(item) for item in user_data.point_list
    ]
    updated_values["item_list"] = [
        asdict_safe(item) for item in user_data.item_list
    ]
    if costume_reward:
        costume_id = int(costume_reward["master_costume_id"])
        updated_values["master_costume_ids"] = [costume_id]
        logger.info(
            "POINT_EXCHANGE committed: user=%s exchange=%s character=%s costume=%s count=%s",
            user_data.user.id,
            exchange_id,
            int(costume_reward["master_character_id"]),
            costume_id,
            total_count,
        )
    else:
        logger.info(
            "POINT_EXCHANGE committed: user=%s exchange=%s count=%s",
            user_data.user.id,
            exchange_id,
            total_count,
        )
    return make_response(data={
        "point_exchange": exchange,
        "updated_value_list": updated_values,
        "gift_list": [],
        "reward_list": reward_list,
        "clear_mission_ids": [],
    })


@router.register(FuncId.LOTTERY_GET)
def handle_lottery_get(request: ParsedRequest) -> dict:
    """MngGachaData.SetLotteryDatas groups this list during home bootstrap."""
    user_data = _request_user(request)
    return make_response(data={
        "lottery_list": USER_STORE.lottery_list(user_data.user.id)
    })


@router.register(FuncId.LOTTERY_CONVERT)
def handle_lottery_convert(request: ParsedRequest) -> dict:
    """Return the initialized collection required by RecvLotteryConvertRData.

    The client unconditionally passes ``converted_list`` to ``List.InsertRange``.
    Returning the generic empty object therefore causes an ArgumentNullException
    during an automatic expired-lottery conversion on login.
    """
    return make_response(data={"converted_list": []})


def _requested_card(user_data: UserGetData, payload: dict) -> dict:
    card_id = int(payload.get("card_id", payload.get("id", 0)) or 0)
    card = next((item for item in user_data.card_list if item.id == card_id), None)
    if card is None and user_data.card_list:
        card = user_data.card_list[0]
    return asdict_safe(card) if card is not None else {
        "id": card_id,
        "master_card_id": 0,
        "exp": 0,
        "skill_exp": 0,
        "evolve": 0,
        "illust_change": 0,
        "breakthrough_level": 0,
        "episode": [],
    }


def _find_card(user_data: UserGetData, payload: dict):
    card_id = int(payload.get("id", payload.get("card_id", 0)) or 0)
    return next((item for item in user_data.card_list if int(item.id) == card_id), None)


@router.register(FuncId.CARD_REINFORCE)
def handle_card_reinforce(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    card = _find_card(user_data, payload)
    if card is None:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    card.exp = max(card.exp, 999_999)
    _persist_user(user_data)
    return make_response(data={
        "card": asdict_safe(card),
        "item_list": [],
        "clear_mission_ids": [],
    })


@router.register(FuncId.CARD_SKILL_REINFORCE)
def handle_card_skill_reinforce(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    card = _find_card(user_data, payload)
    if card is None:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    card.skill_exp = max(card.skill_exp, 999_999)
    _persist_user(user_data)
    return make_response(data={
        "card": asdict_safe(card),
        "delete_card_sub_ids": [],
        "item_list": [],
        "card_sub_list": [],
        "clear_mission_ids": [],
    })


@router.register(FuncId.CARD_EVOLVE)
def handle_card_evolve(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    card = _find_card(user_data, request.json_data or {})
    if card is None:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    card.evolve = 1
    reward_ids = available_rewards_for_card(card.master_card_id)
    if reward_ids:
        character_id = int(card.master_card_id) // 100_000 * 100_000
        costume = next(
            (
                item for item in user_data.costume_list
                if int(item.master_character_id) == character_id
            ),
            None,
        )
        if costume is None:
            costume = Costume(
                master_character_id=character_id,
                master_costume_ids=[],
                master_costume_id=reward_ids[0],
            )
            user_data.costume_list.append(costume)
        costume.master_costume_ids = sorted(
            set(costume.master_costume_ids) | set(reward_ids)
        )
        if not costume.master_costume_id:
            costume.master_costume_id = reward_ids[0]
    _persist_user(user_data)
    return make_response(data={
        "card": asdict_safe(card),
        "item_list": [],
        "point_list": [],
        "clear_mission_ids": [],
    })


@router.register(FuncId.CARD_EPISODE_UNLOCK)
def handle_card_episode_unlock(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    card = _find_card(user_data, payload)
    if card is None:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    episode_type = int(payload.get("episode_type", 0) or 0)
    if episode_type and episode_type not in card.episode:
        card.episode.append(episode_type)
        card.episode.sort()
    _persist_user(user_data)
    if episode_type:
        USER_STORE.mark_card_episode_progress(
            user_data.user.id, card.id, episode_type
        )
    return make_response(data={
        "card": asdict_safe(card),
        "item_list": [],
        "clear_mission_ids": [],
    })


@router.register(FuncId.CARD_EPISODE_READ)
def handle_card_episode_read(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    card = _find_card(user_data, payload)
    if card is None:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    episode_type = int(payload.get("episode_type", 0) or 0)
    if episode_type and episode_type not in card.episode:
        card.episode.append(episode_type)
        card.episode.sort()
    _persist_user(user_data)
    if episode_type:
        USER_STORE.mark_card_episode_progress(
            user_data.user.id, card.id, episode_type, read=True
        )
    return make_response(data={
        "card": asdict_safe(card),
        "clear_mission_ids": [],
    })


@router.register(FuncId.CARD_ILLUST_CHANGE)
def handle_card_illust_change(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    card = _find_card(user_data, payload)
    if card is None:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    card.illust_change = int(payload.get("illust_change", 0) or 0)
    _persist_user(user_data)
    return make_response(data={
        "card": asdict_safe(card),
    })


@router.register(FuncId.CARD_SUB_EXCHANGE)
def handle_card_sub_exchange(request: ParsedRequest) -> dict:
    return make_response(data={
        "point": {"type": 0, "amount": 0},
        "delete_card_sub_ids": [],
    })


@router.register(FuncId.LOTTERY_DRAW)
def handle_lottery_draw(request: ParsedRequest) -> dict:
    from api.lottery import InsufficientTicket, DrawLimitReached
    user_data = _request_user(request)
    payload = request.json_data or {}
    lottery_id = int(payload.get("master_lottery_id", 0) or 0)
    price_number = int(payload.get("master_lottery_price_number", 0) or 0)
    uuid_param = USER_STORE.uuid_for_user_id(user_data.user.id)
    if not uuid_param:
        return make_response(data=None, code=ResultCode.ERROR_USER_NOT_FOUND)
    before = {field: {x.id: asdict_safe(x) for x in getattr(user_data, field)}
              for field in ('card_list', 'card_sub_list', 'item_list')}
    try:
        lottery_items, _cost = USER_STORE.complete_lottery_draw(
            uuid_param, user_data, lottery_id, price_number, payload
        )
    except OverflowError:
        return make_response(data=None, code=ResultCode.ERROR_INSUFFICIENT_GEM)
    except InsufficientTicket:
        return make_response(data=None, code=ResultCode.ERROR_INSUFFICIENT_ITEM)
    except DrawLimitReached:
        return make_response(data=None, code=ResultCode.ERROR_EXCEED_LIMIT)
    except ValueError:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    updated_values = _empty_updated_values(user_data)
    for field, previous in before.items():
        updated_values[field] = [asdict_safe(x) for x in getattr(user_data, field)
                                 if previous.get(x.id) != asdict_safe(x)]
    effects = getattr(user_data, '_lottery_effects', {})
    updated_values['card_breakthrough_list'] = effects.get('card_breakthrough_list', [])
    updated_values['ability_card_level_up_list'] = effects.get('ability_card_level_up_list', [])
    updated_values['ability_card_list'] = user_data.ability_card_list
    updated_values['point_list'] = [asdict_safe(x) for x in user_data.point_list]
    updated_values['master_title_ids'] = user_data.master_title_ids
    updated_values['model_costumes'] = effects.get('model_costumes', [])
    return make_response(data={
        "lottery_item_list": lottery_items,
        "is_send_gift": 0,
        "updated_value_list": updated_values,
        "clear_mission_ids": [],
        "reward_list": effects.get('reward_list', []),
    })


@router.register(FuncId.AREA_ITEM_SETTING)
def handle_area_item_setting(request: ParsedRequest) -> dict:
    """Replace requested furniture slots and persist the complete room layout."""
    user_data = _request_user(request)
    payload = request.json_data or {}
    requested = payload.get("area_item_setting_request_list") or []
    by_position = {
        int(item.master_area_position_id): item
        for item in user_data.area_item_setting_list
    }
    owned_ids = {int(item.id) for item in user_data.area_item_list}
    changed = []
    for raw in requested:
        position_id = int(raw.get("master_area_position_id", 0) or 0)
        area_item_id = int(raw.get("area_item_id", 0) or 0)
        if not position_id or (area_item_id and area_item_id not in owned_ids):
            continue
        setting = AreaItemSetting(
            master_area_position_id=position_id,
            area_item_id=area_item_id,
        )
        by_position[position_id] = setting
        changed.append(setting)
    user_data.area_item_setting_list = [
        by_position[key] for key in sorted(by_position)
    ]
    _persist_user(user_data)
    return make_response(data={
        "area_item_setting_list": [asdict_safe(item) for item in changed],
        "clear_mission_ids": [],
    })


@router.register(FuncId.AREA_ITEM)
def handle_area_item_get(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    return make_response(data={
        "area_item_list": [asdict_safe(item) for item in user_data.area_item_list]
    })


@router.register(FuncId.AREA_ITEM_BUY)
def handle_area_item_buy(request: ParsedRequest) -> dict:
    """Create an owned furniture instance; existing items are levelled up."""
    user_data = _request_user(request)
    master_id = int((request.json_data or {}).get("master_area_item_id", 0) or 0)
    from api.area_layout import max_level
    limit=max_level(master_id)
    if limit<1:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    item = next(
        (row for row in user_data.area_item_list if row.master_area_item_id == master_id),
        None,
    )
    if item is None:
        next_id = max((row.id for row in user_data.area_item_list), default=0) + 1
        item = AreaItem(id=next_id, master_area_item_id=master_id, level=1)
        user_data.area_item_list.append(item)
        if master_id not in user_data.released_master_area_item_ids:
            user_data.released_master_area_item_ids.append(master_id)
            user_data.released_master_area_item_ids.sort()
    else:
        item.level = min(limit, item.level + 1)
    _persist_user(user_data)
    return make_response(data={
        "area_item": asdict_safe(item),
        "item_list": [],
        "point_list": [asdict_safe(row) for row in user_data.point_list],
        "clear_mission_ids": [],
    })


@router.register(FuncId.TALK_READ)
def handle_talk_read(request: ParsedRequest) -> dict:
    """Persist the first-clear growth shown by a home conversation script."""
    user_data = _request_user(request)
    talk_id = int((request.json_data or {}).get("master_talk_id", 0) or 0)
    rewarded = bool(talk_id) and USER_STORE.complete_talk_reward(user_data, talk_id)
    coin = _point(user_data, 1)
    updated_values = _empty_updated_values(user_data)
    reward_list = []
    if rewarded:
        updated_values["point_list"] = [asdict_safe(coin)]
        updated_values["character_list"] = [
            asdict_safe(item) for item in user_data.character_list
        ]
        reward_list = [
            {"type": 4, "value": 1, "level": 0, "amount": 1_000},
            {"type": 1, "value": 0, "level": 0, "amount": 50},
        ]
        logger.info(
            "TALK_READ rewarded: user=%s talk=%s exp=+200 character=+50 coin=+1000 gem=+50",
            user_data.user.id,
            talk_id,
        )
    return make_response(data={
        "stamina": asdict_safe(user_data.stamina),
        "reward_list": reward_list,
        "clear_mission_ids": [],
        "updated_value_list": updated_values,
    })


@router.register(FuncId.TALK_END)
def handle_talk_end(request: ParsedRequest) -> dict:
    return make_response(data={})


@router.register(FuncId.TALK_GET)
def handle_talk_get(request: ParsedRequest) -> dict:
    """Return recovered home conversations already registered as read.

    The home scripts remain playable from their local bundles.  Marking them
    read prevents every login from treating the same conversation as a first
    clear and opening a rank-reward notice without server reward metadata.
    """
    user_data = _request_user(request)
    return make_response(data={"master_talk_ids": list(user_data.master_talk_ids)})


def _story_group_id(part_id: int) -> int:
    return part_id // 100 * 100


def _ensure_story_part(user_data: UserGetData, part_id: int) -> None:
    group_id = _story_group_id(part_id)
    group = next(
        (
            item for item in user_data.story_list
            if isinstance(item, dict) and int(item.get("master_story_id", 0)) == group_id
        ),
        None,
    )
    if group is None:
        group = {"master_story_id": group_id, "master_story_part_ids": []}
        user_data.story_list.append(group)
    parts = group.setdefault("master_story_part_ids", [])
    if part_id not in parts:
        parts.append(part_id)
        parts.sort()


@router.register(FuncId.STORY_READ)
def handle_story_read(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    part_id = int((request.json_data or {}).get("master_story_part_id", 0) or 0)
    if not part_id:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    _ensure_story_part(user_data, part_id)
    _persist_user(user_data)
    USER_STORE.mark_story_progress(user_data.user.id, part_id, read=True)
    return make_response(data={
        "reward_list": [],
        "updated_value_list": _empty_updated_values(user_data),
        "clear_mission_ids": [],
    })


@router.register(FuncId.STORY_END)
def handle_story_end(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    part_id = int((request.json_data or {}).get("master_story_part_id", 0) or 0)
    if not part_id:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    _ensure_story_part(user_data, part_id)
    _persist_user(user_data)
    USER_STORE.mark_story_progress(user_data.user.id, part_id, read=True, completed=True)
    return make_response(data={})


@router.register(FuncId.CARD_STORY_END)
def handle_card_story_end(request: ParsedRequest) -> dict:
    # Card episode IDs are master identifiers, while Card.episode uses the
    # client's episode type.  Preserve the completion request durably after
    # the matching card endpoint has unlocked/read the episode.
    user_data = _request_user(request)
    _persist_user(user_data)
    return make_response(data={})


@router.register(FuncId.MUSIC_BUY)
def handle_music_buy(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    music_id = int((request.json_data or {}).get("master_music_id", 0) or 0)
    if not music_id:
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    if music_id not in user_data.master_music_ids:
        user_data.master_music_ids.append(music_id)
        user_data.master_music_ids.sort()
    user_data.music_shop_releases = list(user_data.master_music_ids)
    _persist_user(user_data)
    return make_response(data={"item_list": [], "clear_mission_ids": []})


def _update_costume(user_data: UserGetData, raw: dict, *, apply: bool = True) -> bool:
    if not isinstance(raw, dict):
        return False
    try:
        character_id = int(raw.get("master_character_id", 0) or 0)
        costume_id = int(raw.get("master_costume_id", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    row = next(
        (item for item in user_data.costume_list if item.master_character_id == character_id),
        None,
    )
    if (row is None or costume_id not in row.master_costume_ids
            or not costume_is_available(character_id, costume_id)):
        return False
    if apply:
        row.master_costume_id = costume_id
    return True


def _update_model_costume(
    user_data: UserGetData, raw: dict, *, apply: bool = True
) -> bool:
    character_id = int(raw.get("master_character_id", 0) or 0)
    costume_id = int(raw.get("master_model_costume_id", 0) or 0)
    costume_type = int(
        raw.get("model_costume_type", raw.get("master_model_costume_type", 0)) or 0
    )
    row = next(
        (
            item for item in user_data.model_costume_list
            if isinstance(item, dict)
            and int(item.get("master_character_id", 0)) == character_id
            and int(item.get("master_model_costume_type", 0)) == costume_type
        ),
        None,
    )
    if row is None:
        return False
    # BODY (type 1) is mandatory.  Accessory types use id 0 to mean
    # "unequipped" and the released client includes those zero rows in a
    # multi-update request when the player presses save.
    if costume_id == 0:
        if costume_type == 1:
            return False
    elif costume_id not in row.get("master_model_costume_ids", []):
        return False
    if apply:
        row["master_model_costume_id"] = costume_id
    return True


@router.register(FuncId.COSTUME_UPDATE)
@router.register(FuncId.COSTUME_MULTI_UPDATE)
def handle_costume_update(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    settings = payload.get("costume_setting_request_list")
    settings = settings if isinstance(settings, list) else [payload]
    if not settings or any(not _update_costume(user_data, item, apply=False) for item in settings):
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    for item in settings:
        _update_costume(user_data, item)
    _persist_user(user_data)
    return make_response(data={
        "costume_list": playable_costume_inventory(user_data),
        "clear_mission_ids": [],
    })


@router.register(FuncId.MODEL_COSTUME_UPDATE)
@router.register(FuncId.MODEL_COSTUME_MULTI_UPDATE)
def handle_model_costume_update(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    payload = request.json_data or {}
    settings = payload.get("model_costume_setting_request_list")
    settings = settings if isinstance(settings, list) else [payload]
    # Validate the entire request before mutating cached account state so one
    # bad row cannot leave a partially applied in-memory selection.
    if not settings or any(
        not _update_model_costume(user_data, item, apply=False)
        for item in settings
    ):
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    for item in settings:
        _update_model_costume(user_data, item)
    _persist_user(user_data)
    return make_response(data={
        "model_costume_list": list(user_data.model_costume_list),
        "clear_mission_ids": [],
    })


@router.register(FuncId.UPDATE_BACKSTAGE_SETTING)
def handle_update_backstage_setting(request: ParsedRequest) -> dict:
    """Persist card/evolved-card/movie background selections by position."""
    user_data = _request_user(request)
    payload = request.json_data or {}
    raw_settings = (
        payload.get("setting_list")
        or payload.get("backstage_background_setting_list")
        or []
    )
    if isinstance(raw_settings, dict):
        raw_settings = [raw_settings]
    normalized = []
    for raw in raw_settings:
        normalized.append({
            "position_number": int(raw.get("position_number", 0) or 0),
            "setting_type": int(raw.get("setting_type", 0) or 0),
            "setting_id": int(raw.get("setting_id", 0) or 0),
        })
    if normalized:
        by_position = {
            int(item.get("position_number", 0)): item
            for item in user_data.backstage_background_setting_list
            if isinstance(item, dict)
        }
        for item in normalized:
            by_position[item["position_number"]] = item
        user_data.backstage_background_setting_list = [
            by_position[key] for key in sorted(by_position)
        ]
        _persist_user(user_data)
    return make_response(data={"setting_list": normalized})


@router.register(FuncId.MISSION_GET)
def handle_mission_get(request: ParsedRequest) -> dict:
    return make_response(data={"mission_list": []})


@router.register(FuncId.LOGIN_BONUS)
def handle_login_bonus(request: ParsedRequest) -> dict:
    return make_response(data={"login_bonus_list": [], "remove_reward_list": []})


@router.register(FuncId.TITLE_GET)
def handle_title_get(request: ParsedRequest) -> dict:
    user_data = _request_user(request)
    return make_response(data={"master_title_ids": list(user_data.master_title_ids)})


@router.register(FuncId.LOCAL_MOVIE)
def handle_local_movie(request: ParsedRequest) -> dict:
    """Return every movie granted to the local preservation account."""
    user_data = _request_user(request)
    available = [
        item for item in user_data.local_movie_detail_list
        if not isinstance(item, dict)
        or int(item.get("id", item.get("master_local_movie_id", 0)) or 0)
        not in UNAVAILABLE_LOCAL_MOVIE_IDS
    ]
    return make_response(data={"local_movie_detail_list": available})


@router.register(FuncId.LOCAL_MOVIE_GET_SECRET_PATH)
def handle_local_movie_secret_path(request: ParsedRequest) -> dict:
    payload = request.json_data or {}
    movie_id = int(payload.get("master_local_movie_id", payload.get("id", 0)) or 0)
    user_data = _request_user(request)
    owned_ids = {
        int(item.get("id", item.get("master_local_movie_id", 0)) or 0)
        for item in user_data.local_movie_detail_list if isinstance(item, dict)
    }
    if (
        movie_id in UNAVAILABLE_LOCAL_MOVIE_IDS
        or movie_id not in owned_ids
        or movie_id not in LOCAL_MOVIE_PATHS
    ):
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    return make_response(data={"secret_path": LOCAL_MOVIE_PATHS[movie_id]})


@router.register(FuncId.SHOP)
def handle_shop(request: ParsedRequest) -> dict:
    from api.shop import inventory
    return make_response(data={"shop_list": inventory(USER_STORE, _request_user_id(request))})


@router.register(FuncId.SHOP_ITEM_BUY)
def handle_shop_item_buy(request: ParsedRequest) -> dict:
    from api.shop import purchase
    from api.lottery import DrawLimitReached
    user_data = _request_user(request)
    try:
        item_id = int((request.json_data or {}).get("master_shop_item_id", 0))
        item = purchase(USER_STORE, user_data, item_id)
    except OverflowError:
        return make_response(data=None, code=ResultCode.ERROR_INSUFFICIENT_GEM)
    except DrawLimitReached:
        return make_response(data=None, code=ResultCode.ERROR_EXCEED_LIMIT)
    except (ValueError, TypeError):
        return make_response(data=None, code=ResultCode.ERROR_INVALID_PARAM)
    updated = _empty_updated_values(user_data)
    updated['live_battle_point'] = user_data.live_battle_point
    return make_response(data={"gem": asdict_safe(user_data.gem), "shop_list": item,
                               "updated_value_list": updated})


@router.register(FuncId.TROPHY)
def handle_trophy(request: ParsedRequest) -> dict:
    from api.trophy import showroom_response
    return make_response(data=showroom_response(_request_user(request)))


@router.register(FuncId.CLIENT_ERROR)
def handle_client_error(request: ParsedRequest) -> dict:
    """接收并记录客户端错误上报"""
    logger.warning(f"CLIENT_ERROR: {request.json_data}")
    return make_response(data=None, code=ResultCode.SUCCESS)


# ═══════════════════════════════════════════════════════════
# 默认处理器 — 处理未实现的端点
# ═══════════════════════════════════════════════════════════

def handle_default(request: ParsedRequest) -> dict:
    """Return the dump-derived empty shape for every non-stateful endpoint."""
    func_name = (
        FuncId(request.func_id).name
        if request.func_id in FuncId.__members__.values()
        else f"UNKNOWN_{request.func_id}"
    )
    schema = API_EMPTY_SCHEMAS.get(str(int(request.func_id)), {})
    data = copy.deepcopy(schema.get("defaults", {}))
    logger.info(
        "DEFAULT HANDLER: %s (FuncId=%s, fields=%s)",
        func_name,
        request.func_id,
        sorted(data),
    )
    return make_response(data=data)


router.set_default_handler(handle_default)


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def asdict_safe(obj) -> dict:
    """将 dataclass 安全转为 dict"""
    from dataclasses import fields, is_dataclass
    if not is_dataclass(obj):
        return obj
    result = {}
    for f in fields(obj):
        value = getattr(obj, f.name)
        if is_dataclass(value):
            result[f.name] = asdict_safe(value)
        elif isinstance(value, list):
            result[f.name] = [
                asdict_safe(item) if is_dataclass(item) else item
                for item in value
            ]
        else:
            result[f.name] = value
    return result
