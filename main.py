import base64
import binascii
import time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
except Exception:
    get_astrbot_temp_path = None


@register(
    "astrbot_plugin_base64_image_file",
    "Codex",
    "Convert base64 images to local files for KOOK and other platforms",
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
        logger.info(f"[base64_image_file] loaded, cache_dir={self.cache_dir}")
        self._cleanup_cache(force=True)
        self._patch_kook_image_upload()
        self._patch_kook_send_chain()

    @filter.on_decorating_result(priority=100)
    async def convert_base64_images(self, event: AstrMessageEvent):
        result = event.get_result()
        chain = getattr(result, "chain", None) if result else None
        if not chain:
            return

        platform = ""
        try:
            platform = event.get_platform_name()
        except Exception:
            pass

        new_chain, changed = self._convert_chain(chain, flatten_nodes=(platform == "kook"))
        if changed:
            result.chain = new_chain
            logger.info(
                f"[base64_image_file] converted outgoing chain for platform={platform or 'unknown'}"
            )

    def _convert_chain(self, chain, *, flatten_nodes: bool) -> tuple[list, bool]:
        changed = False
        new_chain = []
        for component in chain:
            converted_items, item_changed = self._convert_component(
                component,
                flatten_nodes=flatten_nodes,
            )
            new_chain.extend(converted_items)
            changed = changed or item_changed
        return new_chain, changed

    def _convert_component(self, component, *, flatten_nodes: bool) -> tuple[list, bool]:
        if isinstance(component, Comp.Image):
            converted = self._convert_image(component)
            return ([converted], True) if converted is not None else ([component], False)

        if isinstance(component, Comp.Node):
            content = list(getattr(component, "content", []) or [])
            converted_content, changed = self._convert_chain(
                content,
                flatten_nodes=flatten_nodes,
            )
            if flatten_nodes:
                return (converted_content, True)
            if changed:
                component.content = converted_content
            return ([component], changed)

        if isinstance(component, Comp.Nodes):
            nodes = list(getattr(component, "nodes", []) or [])
            if flatten_nodes:
                flattened = []
                changed = True
                for node in nodes:
                    items, _ = self._convert_component(node, flatten_nodes=True)
                    flattened.extend(items)
                return (flattened, changed)

            changed = False
            new_nodes = []
            for node in nodes:
                items, item_changed = self._convert_component(node, flatten_nodes=False)
                new_nodes.extend(items)
                changed = changed or item_changed
            if changed:
                component.nodes = new_nodes
            return ([component], changed)

        return ([component], False)

    def _convert_image(self, image: Comp.Image):
        file_value = getattr(image, "file", "") or getattr(image, "url", "") or ""
        if not isinstance(file_value, str) or not file_value.startswith("base64://"):
            return None

        image_path = self._save_base64_image(file_value)
        if image_path is None:
            return None

        logger.info(f"[base64_image_file] base64 image saved to {image_path}")
        return Comp.Image(file=str(image_path), path=str(image_path))

    def _save_base64_image(self, file_value: str) -> Path | None:
        raw_base64 = file_value.removeprefix("base64://")
        try:
            image_bytes = base64.b64decode(raw_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            logger.warning(f"[base64_image_file] failed to decode base64 image: {exc}")
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
                if item.is_file()
                and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            ]
        except Exception as exc:
            logger.warning(f"[base64_image_file] failed to scan cache dir: {exc}")
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
                    logger.debug(f"[base64_image_file] failed to remove old cache {item}: {exc}")

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
                        logger.debug(
                            f"[base64_image_file] failed to remove overflow cache {item}: {exc}"
                        )

        if removed:
            logger.info(f"[base64_image_file] cleaned cache files: {removed}")

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
            logger.debug(f"[base64_image_file] KOOK adapter not loaded, skip upload patch: {exc}")
            return

        original_wrap = getattr(KookEvent, "_wrap_message", None)
        if not callable(original_wrap):
            logger.warning("[base64_image_file] KookEvent._wrap_message not found")
            return
        if getattr(KookEvent, "_base64_image_file_upload_patched", False):
            logger.info("[base64_image_file] KOOK upload patch already installed")
            return

        plugin = self

        def patched_wrap(kook_event, index, message_component):
            if isinstance(message_component, Comp.Image):
                converted = plugin._convert_image(message_component)
                if converted is not None:
                    message_component = converted
            return original_wrap(kook_event, index, message_component)

        KookEvent._wrap_message = patched_wrap
        KookEvent._base64_image_file_upload_patched = True
        logger.info("[base64_image_file] KOOK upload patch installed")

    def _patch_kook_send_chain(self) -> None:
        try:
            from astrbot.core.platform.sources.kook.kook_event import KookEvent
        except Exception as exc:
            logger.debug(f"[base64_image_file] KOOK adapter not loaded, skip send patch: {exc}")
            return

        original_send = getattr(KookEvent, "send", None)
        if not callable(original_send):
            logger.warning("[base64_image_file] KookEvent.send not found")
            return
        if getattr(KookEvent, "_base64_image_file_send_patched", False):
            logger.info("[base64_image_file] KOOK send patch already installed")
            return

        plugin = self

        async def patched_send(kook_event, message: MessageChain):
            chain = list(getattr(message, "chain", []) or [])
            new_chain, changed = plugin._convert_chain(chain, flatten_nodes=True)
            if changed:
                logger.info("[base64_image_file] flattened/converted KOOK outgoing chain")
                message = MessageChain(new_chain)
            return await original_send(kook_event, message)

        KookEvent.send = patched_send
        KookEvent._base64_image_file_send_patched = True
        logger.info("[base64_image_file] KOOK send patch installed")

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
