"""Serve the recovered AssetBundle, movie, sound and compatibility files."""

import json
import logging
import mimetypes
import struct
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nanaon.cdn")


class AssetServer:
    """
    本地 CDN 服务器

    游戏通过 AssetServerURL 访问资源文件。
    需要将 ../main.5465.com.aniplex.nananiji/assets/ 映射到 /assets/
    """

    # Resource packs are siblings of private_server in the release root.
    ASSET_BASE = Path(__file__).parent.parent.parent / "main.5465.com.aniplex.nananiji" / "assets"

    # 额外的资源目录 (OBB 提取的缓存)
    CACHE_BASE = Path(__file__).parent.parent.parent / "com.aniplex.nananiji"
    DOWNLOAD_CACHE = CACHE_BASE / "files" / "DownloadCache"
    EXTRA_DOWNLOAD_CACHES: tuple[Path, ...] = ()
    # The recovered cache is nearly complete, but NOTICE_STORY_10000400 is
    # absent from every known archive. A valid ACB is used as a silent
    # compatibility substitute for that single unavailable voice cue.
    NOTICE_STORY_FALLBACK_KEY = (
        "android/sounds/634b22edc57e3e56b5fff2df1ecac20e/"
        "ca2045a475c0916f3b6321be189cf469.acb"
    )
    FALLBACK_ASSETS = {
        NOTICE_STORY_FALLBACK_KEY:
            DOWNLOAD_CACHE / "Android" / "sound"
            / "118b868d886c15a8383d0ea1064b1d58"
            / "Voice" / "PART_10600000_000.acb",
        # Imported full-song audio keeps each preview ACB's native CRI 1.31
        # metadata and cue name, replacing only its embedded single-track AWB.
        # HCA is encrypted with the recovered sound key (0x13416BA).
        "android/sounds/33ad50d0a9b2ae7fac27865787e3dfa2/"
        "113716a7cf3eec50e639f540b0f4a28e.acb":
            Path(__file__).parent.parent / "compat_assets" / "sounds"
            / "mp3_import" / "kaze"
            / "13100021_Kaze_ha_huiteruka_mp3_manifest.acb",
        "android/sounds/de3ea48d8f4438d512882fb6eae2350f/"
        "0beca2bc39ad21f3d2f519bd6303fbb5.acb":
            Path(__file__).parent.parent / "compat_assets" / "sounds"
            / "mp3_import" / "connect"
            / "13200001_Connect_mp3_manifest.acb",
        "android/sounds/060beb4ff3fe406cad3a875e38d99303/"
        "909f537f0d534a61d16885fc0fba8715.acb":
            Path(__file__).parent.parent / "compat_assets" / "sounds"
            / "native_repack" / "hiyashinsu"
            / "13100044_Hiyashinsu_full_manifest.acb",
    }
    # Read directly from recovered SoundFileData. Some downloader paths require
    # the transport response to have this exact length before it is retained.
    FALLBACK_SIZES = {
        NOTICE_STORY_FALLBACK_KEY: 260_384,
    }

    EVENT_SCRIPT_BASE = (
        Path(__file__).parent.parent / "compat_assets" / "bundles"
        / "eventscript" / "system"
    )
    EVENT_RESULT_BASE = EVENT_SCRIPT_BASE / "liveresult"
    EVENT_RESULT_CATALOG = EVENT_RESULT_BASE / "catalog.json"
    EVENT_SCRIPT_CATALOG = EVENT_SCRIPT_BASE / "catalog.json"
    EVENT_STORY_BASE = (
        Path(__file__).parent.parent / "compat_assets" / "bundles"
        / "eventscript" / "story"
    )
    EVENT_STORY_CATALOG = EVENT_STORY_BASE / "catalog.json"
    SOUND_OVERRIDE_CATALOG = (
        Path(__file__).parent.parent / "data" / "sound_overrides.json"
    )
    MOVIE_OVERRIDE_CATALOG = (
        Path(__file__).parent.parent / "data" / "movie_overrides.json"
    )
    # MIME 类型映射
    MIME_MAP = {
        ".acf": "application/octet-stream",
        ".bin": "application/octet-stream",
        ".usm": "video/x-usm",
        ".mp4": "video/mp4",
        ".acb": "audio/x-acb",
        ".awb": "audio/x-awb",
        ".hca": "audio/x-hca",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".json": "application/json",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir:
            self.ASSET_BASE = Path(base_dir)
        self._event_result_catalog = self._load_event_result_catalog()
        self._sound_overrides = self._load_sound_overrides()
        self._movie_overrides = self._load_asset_overrides(
            self.MOVIE_OVERRIDE_CATALOG
        )
        self._ensure_dirs()

    @staticmethod
    def _load_asset_overrides(catalog_path: Path) -> dict[str, Path]:
        if not catalog_path.is_file():
            return {}
        try:
            with catalog_path.open(encoding="utf-8") as stream:
                raw = json.load(stream)
            workspace = Path(__file__).resolve().parent.parent.parent
            return {
                key.lower(): (
                    Path(value) if Path(value).is_absolute()
                    else workspace / Path(value)
                )
                for key, value in raw.items()
            }
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Cannot load asset override catalog %s: %s", catalog_path, exc)
            return {}

    def _load_sound_overrides(self) -> dict[str, Path]:
        if not self.SOUND_OVERRIDE_CATALOG.is_file():
            return {}
        try:
            with self.SOUND_OVERRIDE_CATALOG.open(encoding="utf-8") as stream:
                raw = json.load(stream)
            return {key.lower(): Path(value) for key, value in raw.items()}
        except (OSError, ValueError, TypeError) as exc:
            logger.error("Cannot load sound override catalog: %s", exc)
            return {}

    def _load_event_result_catalog(self) -> dict:
        records = {}
        for catalog_path in (
            self.EVENT_RESULT_CATALOG,
            self.EVENT_SCRIPT_CATALOG,
            self.EVENT_STORY_CATALOG,
        ):
            if not catalog_path.is_file():
                continue
            with catalog_path.open(encoding="utf-8") as stream:
                records.update(json.load(stream))
        if not records:
            logger.warning("No system EventScript catalog is available")
        return records

    def _resolve_event_result_fallback(self, normalized_path: str) -> Optional[Path]:
        """Resolve a prebuilt manifest-compatible EventScript bundle."""
        record = self._event_result_catalog.get(normalized_path)
        if record is None:
            return None

        identifier = Path(record["identifier"])
        try:
            relative_identifier = identifier.relative_to("eventscript/system")
            output = self.EVENT_SCRIPT_BASE / relative_identifier.with_suffix(".unity3d")
        except ValueError:
            relative_identifier = identifier.relative_to("eventscript")
            output = self.EVENT_STORY_BASE / relative_identifier.with_suffix(".unity3d")
        expected_size = int(record.get("compat_size", record["size"]))
        expected_crc = int(record["crc"])

        # Prefer a recovered official asset whenever one exists.  Compatibility
        # generation is only for manifest entries absent from every archive.
        parts = normalized_path.split("/")
        if len(parts) == 4 and parts[0] == "android" and parts[1] == "assetbundle":
            wire = Path(parts[3])
            for cache in (self.DOWNLOAD_CACHE, *self.EXTRA_DOWNLOAD_CACHES):
                recovered = (
                    cache / "Android" / "bundle" / wire.stem
                    / f"{parts[2]}{wire.suffix}"
                )
                if recovered.is_file():
                    return None

        # Compatibility bundles are prebuilt release artifacts. Runtime never
        # compiles assets on the player's machine.
        if (
            output.is_file()
            and output.stat().st_size == expected_size
            and output.read_bytes().startswith(b"UnityFS")
        ):
            return output
        logger.error(
            "Missing prebuilt compatibility bundle: %s (size=%d crc=%d)",
            record["identifier"], expected_size, expected_crc,
        )
        return None

    def _ensure_dirs(self):
        """确保资源目录存在"""
        if not self.ASSET_BASE.exists():
            logger.warning(f"Asset base directory not found: {self.ASSET_BASE}")

    def resolve_path(self, asset_path: str) -> Optional[Path]:
        """
        解析资产路径为本地文件路径

        Args:
            asset_path: 请求的路径 (如 /assets/Movies/Android/opening.usm)

        Returns:
            本地文件绝对路径，如果不存在返回 None
        """
        # 去掉开头的 /assets/
        relative = asset_path.lstrip("/")
        if relative.startswith("assets/"):
            relative = relative[7:]

        parts = Path(relative).parts
        cache_candidates = []
        normalized_relative = relative.replace("\\", "/").lower()
        fallback = self._sound_overrides.get(normalized_relative)
        if fallback is None:
            fallback = self._movie_overrides.get(normalized_relative)
        if fallback is None:
            fallback = self.FALLBACK_ASSETS.get(normalized_relative)
        if fallback is None:
            fallback = self._resolve_event_result_fallback(normalized_relative)
        if fallback is not None:
            cache_candidates.append(fallback)
        download_caches = (self.DOWNLOAD_CACHE, *self.EXTRA_DOWNLOAD_CACHES)
        if (
            len(parts) == 4
            and parts[0].lower() == "android"
            and parts[1].lower() == "manifest"
            and parts[3].lower() == "manifest.unity3d"
        ):
            cache_candidates.extend(
                cache / "Android" / "manifest" / "manifest" / parts[2] / "__data"
                for cache in download_caches
            )

        if len(parts) >= 3 and parts[0].lower() == "android":
            category = parts[1].lower()
            if category in {"bundle", "movie", "sound"}:
                cache_candidates.extend(
                    cache / "Android" / category / Path(*parts[2:])
                    for cache in download_caches
                )
            elif category == "assetbundle" and len(parts) == 4:
                wire_name = Path(parts[3])
                cache_candidates.extend(
                    cache / "Android" / "bundle"
                    / wire_name.stem / f"{parts[2]}{wire_name.suffix}"
                    for cache in download_caches
                )
            elif category in {"sounds", "movies"} and len(parts) == 4:
                wire_name = Path(parts[3])
                cache_candidates.extend(
                    cache / "Android" / category.rstrip("s")
                    / wire_name.stem / f"{parts[2]}{wire_name.suffix}"
                    for cache in download_caches
                )
        candidates = [
            *cache_candidates,
            self.ASSET_BASE / relative,
            self.CACHE_BASE / "files" / relative,
            self.CACHE_BASE / "cache" / relative,
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate
            # 也尝试添加 Unity 哈希子目录
            if candidate.parent.exists():
                for child in candidate.parent.iterdir():
                    if child.is_file() and child.name.startswith(candidate.name[:8]):
                        return child

        return None

    def get_content_type(self, path: Path) -> str:
        """获取文件的 Content-Type"""
        suffix = path.suffix.lower()
        if suffix in self.MIME_MAP:
            return self.MIME_MAP[suffix]
        mime_type, _ = mimetypes.guess_type(str(path))
        return mime_type or "application/octet-stream"

    def list_available_assets(self, subdir: str = "") -> list:
        """列出可用的资产文件"""
        target = self.ASSET_BASE / subdir if subdir else self.ASSET_BASE
        if not target.exists():
            return []
        return [str(p.relative_to(self.ASSET_BASE)) for p in target.rglob("*") if p.is_file()]


# ═══════════════════════════════════════════════════════════
# Flask Blueprint for CDN
# ═══════════════════════════════════════════════════════════

from flask import Blueprint, Response, send_file, abort, jsonify

asset_bp = Blueprint("assets", __name__)
asset_server = AssetServer()


@asset_bp.route("/assets/", defaults={"subpath": ""})
@asset_bp.route("/assets/<path:subpath>")
@asset_bp.route("/<path:subpath>")
def serve_asset(subpath: str):
    """托管资产文件"""
    asset_path = f"/{subpath}" if subpath else "/"

    # 如果是目录列表请求
    if not subpath or subpath.endswith("/"):
        files = asset_server.list_available_assets(subpath.rstrip("/"))
        if files:
            return jsonify({"directory": subpath, "files": files[:100]})
        return jsonify({"error": "not found", "path": subpath}), 404

    # 查找文件
    local_path = asset_server.resolve_path(asset_path)
    if local_path is None:
        logger.warning(f"Asset not found locally: {asset_path}")
        abort(404)

    content_type = asset_server.get_content_type(local_path)
    logger.debug(f"Serving asset: {asset_path} → {local_path} ({content_type})")

    normalized_subpath = subpath.replace("\\", "/").lower()
    padded_size = asset_server.FALLBACK_SIZES.get(normalized_subpath)
    if padded_size is not None:
        source_size = local_path.stat().st_size
        if source_size > padded_size:
            logger.error(
                "Fallback source is larger than target: %s > %s",
                source_size,
                padded_size,
            )
            abort(500)

        def padded_stream():
            with local_path.open("rb") as stream:
                header = stream.read(8)
                if len(header) != 8 or header[:4] != b"@UTF":
                    raise ValueError(f"Fallback is not an ACB @UTF file: {local_path}")
                # @UTF stores the container length excluding the 8-byte
                # signature/length prefix. Keep the container internally
                # consistent after extending its padding area.
                yield header[:4] + struct.pack(">I", padded_size - 8)
                while chunk := stream.read(64 * 1024):
                    yield chunk
            remaining = padded_size - source_size
            zero_chunk = b"\0" * (64 * 1024)
            while remaining:
                count = min(remaining, len(zero_chunk))
                yield zero_chunk[:count]
                remaining -= count

        logger.warning(
            "Serving compatibility ACB: %s (%d -> %d bytes)",
            asset_path,
            source_size,
            padded_size,
        )
        return Response(
            padded_stream(),
            mimetype=content_type,
            headers={
                "Content-Length": str(padded_size),
                "Accept-Ranges": "none",
            },
            direct_passthrough=True,
        )

    return send_file(
        str(local_path),
        mimetype=content_type,
        as_attachment=False,
        conditional=True,
    )


@asset_bp.route("/assets", methods=["GET"])
def list_root():
    """列出根目录资产"""
    files = asset_server.list_available_assets()
    return jsonify({
        "status": "ok",
        "asset_base": str(asset_server.ASSET_BASE),
        "file_count": len(files),
    })
