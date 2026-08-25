"""
API 响应数据模型 — 完全匹配游戏 dump.cs 中的类型定义
来源:
  - RecvServerRData (TypeDefIndex: 17117)
  - RecvLoginRData  (TypeDefIndex: 17121)
  - RecvStartRData  (TypeDefIndex: 17123)
  - RecvUserGetRData (TypeDefIndex: 17125)
  - RecvHomeRData   (TypeDefIndex: 17149)
"""

import time
import uuid
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Any

from api.card_costume_catalog import available_costume_inventory
from api.master_catalog import live_music_video_ids, live_three_d_ids
from api.master_catalog import music_ids as master_music_ids
from api.master_catalog import music_shop_ids, story_inventory


_UNLOCK_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "unlock_catalog.json"
_ITEM_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "item_catalog.json"


def _load_unlock_catalog() -> Dict[str, List[int]]:
    if not _UNLOCK_CATALOG_PATH.exists():
        return {}
    return json.loads(_UNLOCK_CATALOG_PATH.read_text(encoding="utf-8"))


def _load_item_catalog() -> List[Dict[str, int]]:
    if not _ITEM_CATALOG_PATH.exists():
        return []
    payload = json.loads(_ITEM_CATALOG_PATH.read_text(encoding="utf-8"))
    return list(payload.get("items", []))


# ═══════════════════════════════════════════════════════════
# 基础响应包装 — 匹配 RecvData (TypeDefIndex: 20064)
# ═══════════════════════════════════════════════════════════

def make_response(data: Any, code: int = 0) -> dict:
    """构造标准响应体 — 匹配 RecvData + 子类结构"""
    return {
        "code": code,
        "data": data,
        "server_time": int(time.time()),
    }


# ═══════════════════════════════════════════════════════════
# SERVER 响应 — RecvServerRData (TypeDefIndex: 17117)
# ═══════════════════════════════════════════════════════════

@dataclass
class ServerData:
    """服务器信息 — 对应 NetworkConst.ServerInfo (TypeDefIndex: 20633)"""
    api_url: str = "http://127.0.0.1:8080"
    asset_url: str = "http://127.0.0.1:8080/assets"
    environment: str = "private"


# ═══════════════════════════════════════════════════════════
# LOGIN 响应 — RecvLoginRData (TypeDefIndex: 17121)
# ═══════════════════════════════════════════════════════════

@dataclass
class TakeOverUserInfo:
    """账号继承信息"""
    take_over_code: Optional[str] = None
    take_over_password: Optional[str] = None
    expired_at: int = 0


@dataclass
class LoginData:
    access_token: str = ""
    take_over_info: Optional[TakeOverUserInfo] = None

    @classmethod
    def create(cls) -> "LoginData":
        return cls(
            access_token=str(uuid.uuid4()),
            take_over_info=None,
        )


# ═══════════════════════════════════════════════════════════
# START 响应 — RecvStartRData (TypeDefIndex: 17123)
# ═══════════════════════════════════════════════════════════

@dataclass
class AppCloseMessage:
    title: str = ""
    body: str = ""


@dataclass
class StartData:
    asset_hash: str = ""
    asset_version: int = 0
    token: str = ""
    screening_version: int = 0
    is_service_closed: bool = False
    message: Optional[AppCloseMessage] = None

    @classmethod
    def create(cls) -> "StartData":
        return cls(
            asset_hash="",
            asset_version=0,
            token=str(uuid.uuid4()),
            screening_version=0,
            is_service_closed=False,
            message=None,
        )


# ═══════════════════════════════════════════════════════════
# USER_GET 响应 — RecvUserGetRData (TypeDefIndex: 17125)
# ═══════════════════════════════════════════════════════════

@dataclass
class StartUserData:
    """RecvStartUserRData (TypeDefIndex: 17131)."""
    user_id: int
    access_token: str
    is_service_closed: bool = False
    message: Optional[AppCloseMessage] = None


@dataclass
class User:
    id: int = 100001
    name: str = "ナナオンPlayer"
    comment: str = ""
    birth_date: str = ""
    exp: int = 0
    vip_point: int = 0
    main_deck_slot: int = 1
    favorite_master_card_id: int = 10110001
    favorite_card_evolve: int = 0
    master_title_ids: List[int] = field(default_factory=list)
    profile_settings: List[int] = field(default_factory=list)
    last_login_time: int = 0
    nanacomi_shop_dialog_unconfirmed: int = 0


@dataclass
class Gem:
    total: int = 50000
    charge: int = 0
    free: int = 50000


@dataclass
class Stamina:
    stamina: int = 10
    last_updated_time: int = 0


@dataclass
class Card:
    id: int = 0
    master_card_id: int = 0
    exp: int = 0
    skill_exp: int = 0
    evolve: int = 0
    illust_change: int = 0
    breakthrough_level: int = 0
    episode: List[int] = field(default_factory=list)


@dataclass
class CardSub:
    id: int = 0
    master_card_id: int = 0
    amount: int = 0


@dataclass
class Deck:
    """TTS.ProtocolData.Deck.

    The client indexes this collection by ``User.main_deck_slot`` while
    opening DeckEditScene, so a new local account still needs one concrete
    slot even when it owns no cards yet.
    """
    slot: int = 1
    main_card_ids: List[int] = field(default_factory=list)
    ability_card_ids: List[int] = field(default_factory=list)


@dataclass
class Costume:
    master_character_id: int = 0
    master_costume_ids: List[int] = field(default_factory=list)
    master_costume_id: int = 0


@dataclass
class Item:
    id: int = 0
    master_item_id: int = 0
    amount: int = 0


@dataclass
class Point:
    type: int = 0
    amount: int = 0


@dataclass
class AreaItem:
    id: int = 0
    master_area_item_id: int = 0
    level: int = 1


@dataclass
class AreaItemSetting:
    master_area_position_id: int = 0
    area_item_id: int = 0


@dataclass
class Group:
    master_character_id: int = 0
    exp: int = 0


@dataclass
class UserGetData:
    """用户完整数据"""
    user: User = field(default_factory=User)
    gem: Gem = field(default_factory=Gem)
    master_title_ids: List[int] = field(default_factory=list)
    card_list: List[Card] = field(default_factory=list)
    card_sub_list: List[CardSub] = field(default_factory=list)
    deck_list: List[Deck] = field(default_factory=list)
    costume_list: List[Costume] = field(default_factory=list)
    item_list: List[Item] = field(default_factory=list)
    point_list: List[Point] = field(default_factory=list)
    stamina: Stamina = field(default_factory=Stamina)
    area_item_list: List[AreaItem] = field(default_factory=list)
    area_item_setting_list: List[AreaItemSetting] = field(default_factory=list)
    released_master_area_item_ids: List[int] = field(default_factory=list)
    live_list: List[Any] = field(default_factory=list)
    live_mission_list: List[Any] = field(default_factory=list)
    master_music_ids: List[int] = field(default_factory=list)
    music_shop_releases: List[int] = field(default_factory=list)
    master_live_music_video_ids: List[int] = field(default_factory=list)
    master_live_three_d_ids: List[int] = field(default_factory=list)
    master_talk_ids: List[int] = field(default_factory=list)
    story_list: List[Any] = field(default_factory=list)
    character_list: List[Group] = field(default_factory=list)
    master_stamp_ids: List[int] = field(default_factory=list)
    model_costume_list: List[Any] = field(default_factory=list)
    # TUTORIAL_PROGRESS.DONE in the recovered v2.4.0 client.
    # Returning NONE (0) makes ordinary song selections enter the tutorial
    # live flow, which suppresses the normal note judgement sequence.
    tutorial_progress: int = 70
    event_point_list: List[Any] = field(default_factory=list)
    master_live_lane_skin_ids: List[int] = field(default_factory=list)
    subscription_info: Optional[Any] = None
    start_time: int = 0
    take_over_id: str = ""
    social_id: str = ""
    local_movie_detail_list: List[Any] = field(default_factory=list)
    multi_live_open_time: int = 0
    npcount: int = 0
    live_battle_point: Optional[Any] = None
    piece_list: List[Any] = field(default_factory=list)
    member_multi_live: Optional[Any] = None
    billing_list: List[Any] = field(default_factory=list)
    ability_card_list: List[Any] = field(default_factory=list)
    backstage_background_setting_list: List[Any] = field(default_factory=list)
    match_live: Optional[Any] = None

    @classmethod
    def create_default(cls, user_id: int = 100001) -> "UserGetData":
        # Build the local preservation account from IDs proven to exist in the
        # recovered v2.4.0 bundle index. Protocol card IDs are per-account
        # instance IDs, while master_card_id identifies the actual content.
        catalog = _load_unlock_catalog()
        master_card_ids = catalog.get("card_ids") or [
            10110001,
            10210001,
            10310001,
            10410001,
            10510001,
        ]
        # Business IDs come from MusicMst.  The older resource scan populated
        # this field with 611*/612* preview BGM IDs; those are CDN identifiers,
        # not protocol music ownership IDs.
        music_ids = master_music_ids()
        character_ids = catalog.get("card_character_ids") or [
            10100000,
            10200000,
            10300000,
            10400000,
            10500000,
        ]
        cards = [
            Card(
                id=index,
                master_card_id=master_card_id,
                # Every recovered card is owned, but starts at level 1.  This
                # also avoids requesting evolved CardPanel art that is absent
                # from some recovered player caches.
                exp=0,
                skill_exp=0,
                evolve=0,
                illust_change=0,
                breakthrough_level=0,
                episode=list(
                    catalog.get("card_episode_types", {}).get(str(master_card_id), [])
                ),
            )
            for index, master_card_id in enumerate(master_card_ids, 1)
        ]
        # Result processing builds a dictionary keyed by character.  A deck
        # made from the first five catalog rows is invalid because the master
        # catalog is grouped by character, so every member would be 101xxxxx.
        # Pick the first owned card for five distinct characters instead.
        live_deck_card_ids: List[int] = []
        live_deck_characters = set()
        for card in cards:
            character_id = card.master_card_id // 100_000
            if character_id in live_deck_characters:
                continue
            live_deck_characters.add(character_id)
            live_deck_card_ids.append(card.id)
            if len(live_deck_card_ids) == 5:
                break
        if len(live_deck_card_ids) != 5:
            raise ValueError("the preservation catalog needs five distinct Live characters")
        title_ids = catalog.get("title_ids", [])
        stamp_ids = catalog.get("stamp_ids", [])
        area_item_ids = catalog.get("area_item_ids", [])
        item_catalog = _load_item_catalog()
        stories = story_inventory()
        two_d_costumes = [
            Costume(
                master_character_id=int(group["master_character_id"]),
                master_costume_ids=list(group["master_costume_ids"]),
                # A non-zero selection is required when a character has an
                # acquired costume group. Existing selections are preserved
                # by storage migrations.
                master_costume_id=int(group["master_costume_ids"][0]),
            )
            for group in available_costume_inventory()
            if group["master_costume_ids"]
        ]
        return cls(
            user=User(
                id=user_id,
                # Exact cumulative EXP for rank 50 in UserRankMst.
                exp=920_320,
                favorite_master_card_id=master_card_ids[0],
                # This nested field is the small set equipped on the profile;
                # the outer UserGetData.master_title_ids is the owned catalog.
                master_title_ids=list(title_ids[:3]),
                last_login_time=int(time.time()),
            ),
            master_title_ids=list(title_ids),
            card_list=cards,
            gem=Gem(total=50_000, charge=0, free=50_000),
            item_list=[
                Item(
                    id=index,
                    master_item_id=int(item["id"]),
                    amount=int(item["amount"]),
                )
                for index, item in enumerate(item_catalog, 1)
            ],
            point_list=[
                Point(type=1, amount=50_000),
                # POINT_TYPE.TICKET: 壁ちゃん exchange currency.
                Point(type=2, amount=100_000),
                # POINT_TYPE.SEAL is retained for preservation completeness.
                Point(type=3, amount=100_000),
            ],
            # A live deck must contain five members even though the account owns
            # every recovered card.
            deck_list=[Deck(slot=1, main_card_ids=live_deck_card_ids)],
            master_music_ids=music_ids,
            music_shop_releases=music_shop_ids(),
            master_live_music_video_ids=live_music_video_ids(),
            master_live_three_d_ids=live_three_d_ids(),
            # This protocol list is read history, not an unlock inventory.
            # Home talk scripts are unlocked by the installed master/assets;
            # pre-filling this list suppresses every character speech bubble.
            master_talk_ids=[],
            # Only evolution costumes with a complete, exact-size Live2D
            # resource chain are advertised to the client.
            costume_list=two_d_costumes,
            area_item_list=[
                AreaItem(id=index, master_area_item_id=master_id, level=10)
                for index, master_id in enumerate(area_item_ids, 1)
            ],
            released_master_area_item_ids=list(area_item_ids),
            story_list=stories,
            master_stamp_ids=list(stamp_ids),
            model_costume_list=[],
            character_list=[
                Group(master_character_id=character_id)
                for character_id in character_ids
            ],
            # StampSettingScene is reached from Match Live's room-select UI.
            # A null MatchLive object suppresses that entry before the local
            # stamp collection can be opened.
            match_live={
                "match_point": 0,
                "rank": 1,
                "is_set_match_title": 0,
                "is_set_ranking_title": 0,
            },
            start_time=int(time.time()),
        )

    def to_dict(self) -> dict:
        """递归转换为字典"""
        def _convert(obj):
            if isinstance(obj, (int, float, str, bool, type(None))):
                return obj
            if isinstance(obj, list):
                return [_convert(item) for item in obj]
            if hasattr(obj, "__dataclass_fields__"):
                return {k: _convert(v) for k, v in asdict(obj).items()}
            return obj
        return _convert(self)


# ═══════════════════════════════════════════════════════════
# HOME 响应 — RecvHomeRData (TypeDefIndex: 17149)
# ═══════════════════════════════════════════════════════════

@dataclass
class HomeData:
    """主页数据"""
    gift_list: List[Any] = field(default_factory=list)
    unreceived_gift_count: int = 0
    latest_pending_friend_user_id: int = 0
    latest_frined_user_id: int = 0
    clear_mission_count: int = 0
    latest_announcement: Optional[Any] = None
    monthly_ranking_score_list: Optional[Any] = None
    monthly_ranking_result_score_list: List[Any] = field(default_factory=list)
    banner_list: List[Any] = field(default_factory=list)
    nanacomi_shop_dialog_unconfirmed: int = 0


# ═══════════════════════════════════════════════════════════
# 请求解析
# ═══════════════════════════════════════════════════════════

@dataclass
class ParsedRequest:
    """解析后的请求"""
    func_id: int
    method: str  # GET or POST
    uri: str
    json_data: Optional[dict]
    raw_body: Optional[bytes]
    headers: Dict[str, str]
    # 常见的登录/会话参数
    access_token: Optional[str] = None
    user_id: Optional[int] = None
    client_version: Optional[str] = None
    timestamp: Optional[int] = None
    uuid_param: Optional[str] = None
    appsflyer_id: Optional[str] = None
