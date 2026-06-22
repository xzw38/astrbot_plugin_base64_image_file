import base64
import binascii
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
except Exception:
    get_astrbot_temp_path = None


@register(
    "astrbot_plugin_base64_image_file",
    "Codex",
    "将 base64:// 图片转换为本地文件图片，兼容 KOOK 等平台",
    "0.1.0",
)
class Base64ImageFilePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context, config)
        self.config = dict(config or {})
        base_dir = Path(get_astrbot_temp_path()) if get_astrbot_temp_path else Path("data")
        self.cache_dir = base_dir / "base64_image_file"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_cleanup_time = 0.0
        logger.info(f"[base64_image_file] 插件已加载，缓存目录: {self.cache_dir}")
        self._cleanup_cache(force=True)
        self._patch_kook_image_upload()

    @filter.on_decorating_result(priority=100)
    async def convert_base64_images(self, event: AstrMessageEvent):
        result = event.get_result()
        chain = getattr(result, "chain", None) if result else None
        if not chain:
            logger.debug("[base64_image_file] on_decorating_result 触发，但没有 result.chain")
            return

        changed = False
        new_chain = []
        image_count = 0

        for component in chain:
            if isinstance(component, Comp.Image):
                image_count += 1
                converted = self._convert_image(component)
                if converted is not None:
                    new_chain.append(converted)
                    changed = True
                    continue
            new_chain.append(component)

        if changed:
            result.chain = new_chain
            logger.info("[base64_image_file] 已替换发送结果中的 base64 图片")
        elif image_count:
            logger.debug(f"[base64_image_file] 检测到 {image_count} 个图片组件，但不是 base64://")

    def _convert_image(self, image: Comp.Image):
        file_value = getattr(image, "file", "") or getattr(image, "url", "") or ""
        if not isinstance(file_value, str) or not file_value.startswith("base64://"):
            return None

        image_path = self._save_base64_image(file_value)
        if image_path is None:
            return None

        logger.info(f"[base64_image_file] 已将 base64 图片转换为本地文件: {image_path}")
        return Comp.Image.fromFileSystem(str(image_path))

    def _save_base64_image(self, file_value: str) -> Path | None:
        raw_base64 = file_value.removeprefix("base64://")
        try:
            image_bytes = base64.b64decode(raw_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            logger.warning(f"[base64_image_file] base64 图片解码失败: {exc}")
            return None

        suffix = self._guess_suffix(image_bytes)
        image_path = self.cache_dir / f"{uuid.uuid4().hex}{suffix}"
        image_path.write_bytes(image_bytes)
        self._cleanup_cache()
        return image_path

    def _cleanup_cache(self, *, force: bool = False) -> None:
        now = time.time()
        interval_minutes = self._get_int_config("cleanup.cleanup_interval_minutes", 10)
        if not force and interval_minutes > 0:
            if now - self.last_cleanup_time < interval_minutes * 60:
                return
        self.last_cleanup_time = now

        max_age_hours = self._get_int_config("cleanup.max_age_hours", 24)
        max_files = self._get_int_config("cleanup.max_files", 500)

        try:
            files = [
                item
                for item in self.cache_dir.iterdir()
                if item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            ]
        except Exception as exc:
            logger.warning(f"[base64_image_file] 扫描缓存目录失败: {exc}")
            return

        removed = 0
        if max_age_hours > 0:
            cutoff = now - max_age_hours * 3600
            for item in files:
                try:
                    if item.stat().st_mtime < cutoff:
                        item.unlink()
                        removed += 1
                except Exception as exc:
                    logger.debug(f"[base64_image_file] 删除过期缓存失败: {item}, {exc}")

        if max_files > 0:
            existing = []
            for item in files:
                try:
                    if item.exists():
                        existing.append((item.stat().st_mtime, item))
                except Exception:
                    continue
            existing.sort(key=lambda pair: pair[0])
            overflow = len(existing) - max_files
            if overflow > 0:
                for _, item in existing[:overflow]:
                    try:
                        item.unlink()
                        removed += 1
                    except Exception as exc:
                        logger.debug(f"[base64_image_file] 删除超量缓存失败: {item}, {exc}")

        if removed:
            logger.info(f"[base64_image_file] 已清理缓存图片 {removed} 张")

    def _get_int_config(self, path: str, default: int) -> int:
        value = self.config
        for key in path.split("."):
            if not isinstance(value, dict):
                return default
            value = value.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _patch_kook_image_upload(self) -> None:
        try:
            from astrbot.core.platform.sources.kook.kook_event import KookEvent
        except Exception as exc:
            logger.debug(f"[base64_image_file] 未加载 KOOK 适配器，跳过补丁: {exc}")
            return

        original_wrap = getattr(KookEvent, "_wrap_message", None)
        if not callable(original_wrap):
            logger.warning("[base64_image_file] 未找到 KookEvent._wrap_message，跳过补丁")
            return
        if getattr(KookEvent, "_base64_image_file_patched", False):
            logger.info("[base64_image_file] KOOK 图片补丁已存在，跳过重复安装")
            return

        plugin = self

        def patched_wrap(kook_event, index, message_component):
            if isinstance(message_component, Comp.Image):
                file_value = getattr(message_component, "file", "") or ""
                if isinstance(file_value, str) and file_value.startswith("base64://"):
                    image_path = plugin._save_base64_image(file_value)
                    if image_path is not None:
                        logger.info(
                            f"[base64_image_file] KOOK 发送前已将 base64 图片转换为本地文件: {image_path}"
                        )
                        message_component = Comp.Image(file=str(image_path), path=str(image_path))
            return original_wrap(kook_event, index, message_component)

        KookEvent._wrap_message = patched_wrap
        KookEvent._base64_image_file_patched = True
        logger.info("[base64_image_file] 已安装 KOOK base64 图片发送补丁")

    @staticmethod
    def _guess_suffix(data: bytes) -> str:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return ".gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return ".webp"
        return ".png"
