"""AstrBot 网易云音乐点歌-flac 插件（UnblockNeteaseMusic 解锁版）。

工作流程：
1. 通过网易云音乐公开 Web 接口按关键词搜索歌曲；
2. 选中歌曲后调用自部署的 UnblockNeteaseMusic-utils 服务（/match）解锁受限歌曲直链；
3. aiocqhttp 平台（NapCat / Lagrange / go-cqhttp）发送 QQ 自定义音乐卡片，
   其他平台发送文本链接。
"""

import asyncio
import os
import re
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx

try:
    import mutagen  # 歌词/标签内嵌用，缺失时自动跳过内嵌
except ImportError:
    mutagen = None

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Music, Plain, Record
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.star.filter.command import GreedyStr

SELECT_TIMEOUT_SECONDS = 60
"""选歌序号的有效期（秒）"""
CACHE_JANITOR_SECONDS = 300
"""兜底清理阈值：超过该时长的会话缓存在任意消息到来时被清除"""
DOWNLOAD_TIMEOUT_SECONDS = 300.0
"""音乐文件下载超时（独立于 API 请求超时，无损格式体积大）"""
STALE_FILE_SECONDS = 3600
"""下载目录中残留文件（发送中断未清理）的回收阈值"""

AUDIO_EXTS = {"mp3", "flac", "m4a", "aac", "ogg", "wav", "ape", "wma"}
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

SEND_MODE_CN = {"card": "卡片", "file": "文件", "text": "文本", "voice": "语音"}

SEARCH_API = "https://music.163.com/api/search/get/web"
SONG_DETAIL_API = "https://music.163.com/api/song/detail"
LYRIC_API = "https://music.163.com/api/song/lyric"
SONG_LINK = "https://music.163.com/#/song?id={}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}

_ID_FROM_URL = re.compile(r"[?&]id=(\d+)")


class CustomMusic(Music):
    """QQ 自定义音乐卡片。

    AstrBot 自带的 Music 组件在 pydantic v2 下 _type 字段会被当作私有属性
    丢弃，序列化出的 data 里缺少 type=custom，卡片无法播放，这里覆写
    toDict() 保证段结构完整。
    """

    def toDict(self) -> dict:
        data = {
            "type": "custom",
            "url": self.url or "",
            "audio": self.audio or "",
            "title": self.title or "",
        }
        if self.content:
            data["content"] = self.content
        if self.image:
            data["image"] = self.image
        return {"type": "music", "data": data}


class NeteaseCardMusic(Music):
    """网易云官方 163 音乐卡片（协议端按歌曲 ID 自行渲染）。

    自定义卡片被协议端拒绝时的第二重尝试；pydantic v2 会丢弃 _type，
    同样需要覆写 toDict()。
    """

    def toDict(self) -> dict:
        return {"type": "music", "data": {"type": "163", "id": int(self.id or 0)}}


class NeteaseUnblockPlugin(Star):
    """网易云音乐点歌-flac：网易云搜索 + UnblockNeteaseMusic 解锁。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # AstrBotConfig 不做类型校验，空值不合法的配置一律用 or 兜底。
        self.unlock_api = (config.get("unlock_api") or "").rstrip("/")
        self.source = (config.get("source") or "").strip()
        self.auto_pick = bool(config.get("auto_pick") or False)
        self.limit = self._safe_int(config.get("limit"), 10)
        self.timeout = self._safe_float(config.get("timeout"), 15.0)
        self.proxy = (config.get("proxy") or "").strip()
        _va = config.get("verify_audio")
        self.verify_audio = True if _va is None else bool(_va)
        try:
            self.retract_seconds = int(config.get("retract_list_seconds", 60))
        except (TypeError, ValueError):
            self.retract_seconds = 60
        if self.retract_seconds < 0:
            self.retract_seconds = 60
        self.react_emoji = str(config.get("react_emoji") or "").strip()
        self.require_prefix = bool(config.get("require_prefix") or False)
        _el = config.get("embed_lyrics")
        self.embed_lyrics = True if _el is None else bool(_el)
        self.send_mode = (str(config.get("send_mode") or "card")).strip().lower()
        if self.send_mode not in ("card", "file", "text"):
            self.send_mode = "card"
        try:
            self.delete_file_seconds = int(config.get("delete_file_seconds", 60))
        except (TypeError, ValueError):
            self.delete_file_seconds = 60
        if self.delete_file_seconds < 0:
            self.delete_file_seconds = 60
        self._config = config
        try:
            self._download_dir = StarTools.get_data_dir("astrbot_plugin_netease_unblock") / "downloads"
        except Exception:
            self._download_dir = Path("data/astrbot_plugin_netease_unblock/downloads")
        self._download_dir.mkdir(parents=True, exist_ok=True)

        self._pending: dict[str, dict] = {}
        # 解锁服务单独走代理：本机网络可能对解锁域名 TLS 干扰（直连被重置），
        # 而网易云搜索接口直连更快更稳，不跟着走代理。
        self._search_client = httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=HEADERS
        )
        self._unlock_client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=HEADERS,
            proxy=self.proxy or None,
        )

        if not self.unlock_api:
            logger.warning("[netease_unblock] 未配置解锁服务地址 unlock_api，解锁功能不可用。")

    async def terminate(self):
        """插件停用/重载时关闭共享的 httpx 客户端。"""
        await self._search_client.aclose()
        await self._unlock_client.aclose()

    # ------------------------------------------------------------------ #
    # 工具函数
    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_int(value, default: int) -> int:
        try:
            n = int(value)
            return n if 0 < n <= 50 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value, default: float) -> float:
        try:
            f = float(value)
            return f if f > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _cache_key(event: AstrMessageEvent) -> str:
        # unified_msg_origin 在群聊里是整群共享的，必须再叠加发送者 ID，
        # 否则群内多人同时点歌会互相覆盖缓存。
        return f"{event.unified_msg_origin}:{event.get_sender_id()}"

    @staticmethod
    def _normalize(song: dict) -> dict:
        """兼容搜索接口的旧字段（artists/album）与详情接口的新字段（ar/al）。"""
        artists = song.get("artists") or song.get("ar") or []
        album = song.get("album") or song.get("al") or {}
        cover = (album.get("picUrl") or "").replace("http://", "https://")
        return {
            "id": song.get("id"),
            "name": song.get("name") or "未知",
            "artists": " / ".join(a.get("name", "") for a in artists if a.get("name")),
            "album": album.get("name") or "",
            "cover": cover,
            "duration": song.get("duration") or song.get("dt") or 0,
            "fee": song.get("fee", 0),
        }

    @staticmethod
    def _fmt(song: dict) -> str:
        text = f"{song['name']} - {song['artists'] or '未知歌手'}"
        if song.get("album"):
            text += f"｜专辑：{song['album']}"
        dur = int(song.get("duration") or 0)
        if dur > 0:
            text += f"｜{dur // 60000}:{dur // 1000 % 60:02d}"
        if song.get("fee") == 1:
            text += "｜VIP"
        return text

    # ------------------------------------------------------------------ #
    # API 请求
    # ------------------------------------------------------------------ #
    async def _search(self, keyword: str) -> list[dict]:
        resp = await self._search_client.get(
            SEARCH_API,
            params={"s": keyword, "type": 1, "offset": 0, "limit": self.limit, "total": "true"},
        )
        resp.raise_for_status()
        songs = ((resp.json() or {}).get("result") or {}).get("songs") or []
        return [self._normalize(s) for s in songs]

    async def _get_song_detail(self, song_id: str) -> dict | None:
        resp = await self._search_client.get(
            SONG_DETAIL_API, params={"id": song_id, "ids": f"[{song_id}]"}
        )
        resp.raise_for_status()
        songs = (resp.json() or {}).get("songs") or []
        return self._normalize(songs[0]) if songs else None

    async def _match(self, song_id) -> str | None:
        """调用自部署的 UnblockNeteaseMusic-utils 解锁服务，返回可播放直链。

        source 支持逗号分隔的优先级列表（如 "byfuns,ddyr,auto"），逐个尝试；
        auto 表示交给服务端自动选择（含 bugpk 兜底）。byfuns/ddyr 默认请求
        无损/Hi-Res，排前面可让免费歌直接吃无损。
        """
        if not self.unlock_api:
            return None
        if self.source:
            sources = [s.strip() for s in self.source.replace("，", ",").split(",") if s.strip()]
        else:
            sources = ["auto"]
        tried: set[str] = set()
        for name in sources:
            token = "" if name.lower() == "auto" else name
            key = token or "auto"
            if key in tried:
                continue
            tried.add(key)
            params = {"id": str(song_id)}
            if token:
                params["source"] = token
            try:
                resp = await self._unlock_client.get(f"{self.unlock_api}/match", params=params)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                logger.warning(f"[netease_unblock] 音源 {key} 请求失败: {e!r}")
                continue
            data = payload.get("data") if isinstance(payload, dict) else None
            url = data.get("url") if isinstance(data, dict) else None
            if not (isinstance(url, str) and url.startswith("http")):
                continue
            if self.verify_audio and not await self._is_audio(url):
                logger.warning(f"[netease_unblock] 音源 {key} 返回的不是音频（疑似 VIP 占位页），跳过")
                continue
            logger.info(f"[netease_unblock] 歌曲 {song_id} 命中音源: {key}")
            return url
        return None

    async def _is_audio(self, url: str) -> bool:
        """HEAD 探测直链内容类型，过滤网易云 VIP 占位 HTML 页。"""
        try:
            resp = await self._unlock_client.head(url)
            ctype = (resp.headers.get("content-type") or "").lower()
        except Exception as e:
            logger.warning(f"[netease_unblock] 音频校验请求失败: {e!r}")
            return True  # 探测失败时不拦截，交给播放端自行验证
        if not ctype or "audio" in ctype or "video" in ctype or "octet-stream" in ctype:
            return True
        return not ctype.startswith("text/")

    # ------------------------------------------------------------------ #
    # 发送歌曲
    # ------------------------------------------------------------------ #
    async def _react(self, event: AstrMessageEvent) -> None:
        """在用户命令消息上贴表情回应（NapCat/Lagrange 扩展接口，尽力而为）。

        AstrBot 自带的 event.react() 默认只是发一条文字消息，不是原生回应，
        所以这里直接调 OneBot 扩展动作 set_msg_emoji_like。
        """
        if not self.react_emoji or not hasattr(event, "bot"):
            return
        msg_obj = getattr(event, "message_obj", None)
        mid = getattr(msg_obj, "message_id", None)
        raw = getattr(msg_obj, "raw_event", None)
        if mid is None and isinstance(raw, dict):
            mid = raw.get("message_id")
        if mid is None:
            return
        try:
            await event.bot.call_action(
                "set_msg_emoji_like", message_id=mid, emoji_id=str(self.react_emoji)
            )
        except Exception as e:
            logger.warning(f"[netease_unblock] 表情回应失败: {e!r}")

    async def _resolve_results(self, event: AstrMessageEvent, song: dict, mode: str | None = None):
        """解锁并构造发送结果（异步生成器）：按指定/默认模式发卡片 / 文件 / 语音 / 文本。"""
        mode = mode or self.send_mode
        link = SONG_LINK.format(song["id"])
        try:
            audio = await self._match(song["id"])
        except Exception as e:
            hint = "（已配置代理，请确认代理进程存活）" if self.proxy else "（可尝试在插件配置里填写本机代理）"
            logger.error(f"[netease_unblock] 解锁请求异常: {e!r} {hint}")
            audio = None

        if not audio:
            yield event.plain_result(
                f"❌ {song['name']} - {song['artists'] or '未知歌手'}\n"
                "解锁失败：解锁服务无可用音源或站点不可达\n"
                f"🔗 {link}"
            )
            return

        if mode == "file":
            async for r in self._send_file(event, song, audio):
                yield r
            return

        if mode == "voice":
            async for r in self._send_voice(event, song, audio):
                yield r
            return

        text = f"🎵 {song['name']} - {song['artists']}\n🔗 {link}\n▶️ 直链：{audio}"

        if mode == "text":
            yield event.plain_result(text)
            return

        if event.get_platform_name() == "aiocqhttp" and hasattr(event, "bot"):
            # 两重尝试：自定义卡片 → 网易云官方 163 卡片 → 回退文本。
            # 直接发送并捕获异常，避免 AstrBot 只记日志、群里毫无反馈。
            cards = [
                CustomMusic(
                    url=link,
                    audio=audio,
                    title=song["name"],
                    content=song["artists"],
                    image=song["cover"],
                ),
                NeteaseCardMusic(id=song["id"]),
            ]
            for card in cards:
                try:
                    await event.send(MessageChain([card]))
                    return
                except Exception as e:
                    logger.warning(f"[netease_unblock] 音乐卡片({type(card).__name__})发送失败: {e!r}")
            logger.error("[netease_unblock] 两种音乐卡片均被协议端拒绝，回退文本")

        yield event.plain_result(text)

    async def _progress(self, event: AstrMessageEvent, text: str):
        """进度提示：aiocqhttp 平台直发并定时撤回，否则普通发送。"""
        if self.retract_seconds > 0 and event.get_platform_name() == "aiocqhttp" and hasattr(event, "bot"):
            if await self._send_and_schedule_retract(event, text, self.retract_seconds):
                return
        yield event.plain_result(text)

    async def _send_file(self, event: AstrMessageEvent, song: dict, audio: str):
        """下载音乐并以文件形式发送，内嵌歌词后定时删除本地文件。"""
        async for r in self._progress(event, f"⏳ 正在下载「{song['name']}」，请稍候…"):
            yield r
        try:
            path = await self._download_song(song, audio)
        except Exception as e:
            logger.error(f"[netease_unblock] 文件下载失败: {e!r}")
            yield event.plain_result(f"❌ 文件下载失败：{e!r}\n▶️ 直链：{audio}")
            return
        if self.embed_lyrics:
            lrc = await self._fetch_lyrics(song["id"])
            cover = await self._fetch_cover(song)
            if not cover:
                # 搜索接口对部分歌曲不给封面地址，从歌曲详情接口补
                try:
                    detail = await self._get_song_detail(song["id"])
                    if detail:
                        cover = await self._fetch_cover(detail)
                except Exception as e:
                    logger.warning(f"[netease_unblock] 详情接口补封面失败: {e!r}")
            if lrc or cover:
                ok = await asyncio.to_thread(self._embed_tags, path, song, lrc, cover)
                if ok:
                    logger.info(f"[netease_unblock] 已内嵌歌词/封面/标签: {path.name}")
        try:
            yield event.chain_result([File(name=path.name, file=str(path))])
        except Exception as e:
            logger.error(f"[netease_unblock] 文件发送失败: {e!r}")
            yield event.plain_result(f"❌ 文件发送失败\n▶️ 直链：{audio}")
        finally:
            if self.delete_file_seconds > 0:
                asyncio.create_task(self._delete_later(path, self.delete_file_seconds))

    async def _fetch_lyrics(self, song_id) -> str | None:
        """抓取网易云 LRC 歌词，无歌词返回 None。"""
        try:
            resp = await self._search_client.get(LYRIC_API, params={"id": str(song_id), "lv": 1})
            resp.raise_for_status()
            lrc = ((resp.json() or {}).get("lrc") or {}).get("lyric")
        except Exception as e:
            logger.warning(f"[netease_unblock] 歌词获取失败: {e!r}")
            return None
        return lrc.strip() or None

    async def _fetch_cover(self, song: dict):
        """下载专辑封面原图（去掉缩略图参数取最高画质），返回 (bytes, mime) 或 None。"""
        url = song.get("cover") or ""
        if not url.startswith("http") or "5639395138885805" in url:
            # 5639395138885805 是网易云的默认占位封面，等于没有封面
            return None
        url = url.split("?")[0]  # 剥掉缩略图参数，取原始分辨率
        try:
            resp = await self._search_client.get(url)
            resp.raise_for_status()
            data = resp.content
        except Exception as e:
            logger.warning(f"[netease_unblock] 封面下载失败: {e!r}")
            return None
        if not data or len(data) < 1024:
            return None
        mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        return data, mime

    def _embed_tags(self, path: Path, song: dict, lrc: str | None, cover: tuple | None) -> bool:
        """把 LRC 歌词、封面原图与标题/歌手/专辑标签写入音频文件（同步阻塞，调用方放线程里跑）。"""
        if mutagen is None:
            logger.warning("[netease_unblock] 未安装 mutagen，跳过歌词内嵌")
            return False
        cover_bytes = cover[0] if cover else None
        cover_mime = cover[1] if cover else "image/jpeg"
        title = song.get("name") or ""
        artist = song.get("artists") or ""
        album = song.get("album") or ""
        try:
            from mutagen.flac import FLAC, Picture
            from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1, USLT
            from mutagen.mp4 import MP4, MP4Cover

            suffix = path.suffix.lower()
            if suffix == ".flac":
                f = FLAC(str(path))
                if lrc:
                    # LYRICS 兼容飞傲/foobar 等播放器，UNSYNCEDLYRICS 是标准字段名
                    f["LYRICS"] = lrc
                    f["UNSYNCEDLYRICS"] = lrc
                f["title"] = title
                f["artist"] = artist
                f["album"] = album
                if cover_bytes:
                    pic = Picture()
                    pic.type = 3  # front cover
                    pic.mime = cover_mime
                    pic.desc = "Cover"
                    pic.data = cover_bytes
                    f.clear_pictures()
                    f.add_picture(pic)
                f.save()
                return True
            if suffix == ".mp3":
                try:
                    tags = ID3(str(path))
                except ID3NoHeaderError:
                    tags = ID3()
                if lrc:
                    tags.setall("USLT", [USLT(encoding=3, lang="chi", desc="歌词", text=lrc)])
                tags.add(TIT2(encoding=3, text=title))
                tags.add(TPE1(encoding=3, text=artist))
                tags.add(TALB(encoding=3, text=album))
                if cover_bytes:
                    tags.delall("APIC")
                    tags.add(APIC(encoding=3, mime=cover_mime, type=3, desc="Cover", data=cover_bytes))
                tags.save(str(path), v2_version=3)
                return True
            if suffix in (".m4a", ".mp4"):
                f = MP4(str(path))
                if lrc:
                    f["\xa9lyr"] = [lrc]
                f["\xa9nam"] = [title]
                f["\xa9ART"] = [artist]
                f["\xa9alb"] = [album]
                if cover_bytes:
                    img_fmt = MP4Cover.FORMAT_PNG if cover_mime == "image/png" else MP4Cover.FORMAT_JPEG
                    f["covr"] = [MP4Cover(cover_bytes, imageformat=img_fmt)]
                f.save()
                return True
            logger.info(f"[netease_unblock] 格式 {suffix} 暂不支持内嵌歌词，跳过")
            return False
        except Exception as e:
            logger.warning(f"[netease_unblock] 歌词内嵌失败: {e!r}")
            return False

    async def _send_voice(self, event: AstrMessageEvent, song: dict, audio: str):
        """下载并以语音（Record）形式发送，发送完成立即清理临时文件。"""
        async for r in self._progress(event, f"⏳ 正在获取「{song['name']}」语音，请稍候…"):
            yield r
        try:
            path = await self._download_song(song, audio)
        except Exception as e:
            logger.error(f"[netease_unblock] 语音下载失败: {e!r}")
            yield event.plain_result(f"❌ 语音获取失败：{e!r}\n▶️ 直链：{audio}")
            return
        try:
            yield event.chain_result([Record.fromFileSystem(str(path))])
        except Exception as e:
            logger.error(f"[netease_unblock] 语音发送失败: {e!r}")
            yield event.plain_result(f"❌ 语音发送失败\n▶️ 直链：{audio}")
        finally:
            # 语音在发送时已被转 base64，临时文件立即清理，不占存储
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"[netease_unblock] 清理语音临时文件失败: {e}")

    async def _download_song(self, song: dict, url: str) -> Path:
        """流式下载歌曲，按「歌名 - 歌手.格式」命名。"""
        self._sweep_stale_downloads()
        ext = PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower()
        if ext not in AUDIO_EXTS:
            ext = "mp3"
        stem = self._sanitize_filename(
            f"{song['name']} - {song['artists']}" if song.get("artists") else song["name"]
        )
        target = self._download_dir / f"{stem}.{ext}"

        tmp = self._download_dir / f".{uuid.uuid4().hex}.part"
        try:
            async with self._unlock_client.stream(
                "GET", url, timeout=httpx.Timeout(DOWNLOAD_TIMEOUT_SECONDS)
            ) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        f.write(chunk)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target

    async def _delete_later(self, path: Path, seconds: int):
        """定时删除已发送的本地文件，回收存储空间。"""
        try:
            await asyncio.sleep(seconds)
            path.unlink(missing_ok=True)
            logger.info(f"[netease_unblock] 已删除本地文件: {path.name}")
        except OSError as e:
            logger.warning(f"[netease_unblock] 删除本地文件失败: {e}")

    def _sweep_stale_downloads(self) -> None:
        """回收发送中断遗留的旧文件。"""
        now = time.time()
        try:
            for f in self._download_dir.iterdir():
                try:
                    if f.is_file() and now - f.stat().st_mtime > STALE_FILE_SECONDS:
                        f.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        name = _ILLEGAL_FILENAME_CHARS.sub("_", name).strip(" .")
        return name[:120] or "song"

    async def _send_and_schedule_retract(self, event: AstrMessageEvent, text: str, seconds: int) -> bool:
        """直发列表消息并安排到期撤回，返回是否成功直发。"""
        try:
            gid = str(event.get_group_id() or "")
            if gid.isdigit():
                resp = await event.bot.send_group_msg(
                    group_id=int(gid), message=[{"type": "text", "data": {"text": text}}]
                )
            else:
                sid = str(event.get_sender_id() or "")
                if not sid.isdigit():
                    return False
                resp = await event.bot.send_private_msg(
                    user_id=int(sid), message=[{"type": "text", "data": {"text": text}}]
                )
            mid = resp.get("message_id") if isinstance(resp, dict) else None
            if mid is None:
                return False
            asyncio.create_task(self._auto_retract(event, mid, seconds))
            return True
        except Exception as e:
            logger.warning(f"[netease_unblock] 列表消息直发失败，回退普通发送: {e!r}")
            return False

    async def _auto_retract(self, event: AstrMessageEvent, message_id, seconds: int):
        """到期撤回列表消息。"""
        try:
            await asyncio.sleep(seconds)
            await event.bot.delete_msg(message_id=message_id)
            logger.info(f"[netease_unblock] 列表消息 {message_id} 已自动撤回")
        except Exception as e:
            logger.warning(f"[netease_unblock] 自动撤回失败: {e!r}")

    # ------------------------------------------------------------------ #
    # 指令与序号分发
    # ------------------------------------------------------------------ #
    @filter.command("点歌")
    async def dian_ge(self, event: AstrMessageEvent, keyword: GreedyStr):
        """点歌 <歌名>：搜索网易云音乐并发送可播放的音乐卡片"""
        async for r in self._dian_ge_flow(event, str(keyword or "").strip()):
            yield r

    async def _dian_ge_flow(self, event: AstrMessageEvent, kw: str, mode: str | None = None):
        if not kw:
            yield event.plain_result(
                "用法：点歌 <歌名>（直接发「点歌 歌名」即可，无需前缀）\n也支持：解锁 <歌曲ID或分享链接>"
            )
            return

        await self._react(event)  # 收到命令先贴个表情当回执

        key = self._cache_key(event)
        pending = self._pending.get(key)

        # 「点歌 2」：序号选择上一次的搜索结果
        if kw.isdigit() and pending and pending["stage"] == "pick_song":
            songs = pending["songs"]
            idx = int(kw)
            if 1 <= idx <= len(songs):
                self._pending.pop(key, None)
                async for r in self._resolve_results(event, songs[idx - 1], pending.get("mode")):
                    yield r
                event.stop_event()
                return

        try:
            songs = await self._search(kw)
        except Exception as e:
            logger.error(f"[netease_unblock] 搜索失败: {e!r}", exc_info=True)
            yield event.plain_result("❌ 搜索歌曲失败，请稍后重试")
            return
        if not songs:
            yield event.plain_result(f"❌ 未找到「{kw}」相关的歌曲")
            return

        if self.auto_pick or len(songs) == 1:
            async for r in self._resolve_results(event, songs[0], mode):
                yield r
            return

        lines = ["🎵 搜索结果："]
        for i, song in enumerate(songs, 1):
            lines.append(f"{i}. {self._fmt(song)}")
        lines.append(f"回复序号选择（{SELECT_TIMEOUT_SECONDS} 秒内有效，回复 0 取消）")
        self._pending[key] = {"stage": "pick_song", "songs": songs, "ts": time.time(), "mode": mode}

        text = "\n".join(lines)
        if self.retract_seconds > 0 and event.get_platform_name() == "aiocqhttp" and hasattr(event, "bot"):
            if await self._send_and_schedule_retract(event, text, self.retract_seconds):
                return
        yield event.plain_result(text)

    @filter.command("解锁")
    async def unlock(self, event: AstrMessageEvent, target: GreedyStr):
        """解锁 <网易云歌曲ID或分享链接>：直接解锁指定歌曲"""
        async for r in self._unlock_flow(event, str(target or "").strip()):
            yield r

    async def _unlock_flow(self, event: AstrMessageEvent, text: str):
        m = _ID_FROM_URL.search(text)
        song_id = m.group(1) if m else (text if text.isdigit() else "")
        if not song_id:
            yield event.plain_result("用法：解锁 <歌曲ID 或 歌曲分享链接>（直接发「解锁 ID」即可）")
            return

        await self._react(event)

        try:
            song = await self._get_song_detail(song_id)
        except Exception as e:
            logger.warning(f"[netease_unblock] 获取歌曲详情失败: {e}")
            song = None
        if song is None:
            song = {
                "id": song_id,
                "name": f"网易云歌曲 {song_id}",
                "artists": "",
                "album": "",
                "cover": "",
                "duration": 0,
                "fee": 0,
            }
        async for r in self._resolve_results(event, song):
            yield r

    @filter.command("点歌卡片")
    async def dian_ge_card(self, event: AstrMessageEvent, keyword: GreedyStr):
        """点歌卡片 <歌名>：本次以音乐卡片发送"""
        async for r in self._dian_ge_flow(event, str(keyword or "").strip(), mode="card"):
            yield r

    @filter.command("点歌文件")
    async def dian_ge_file(self, event: AstrMessageEvent, keyword: GreedyStr):
        """点歌文件 <歌名>：本次以音乐文件发送"""
        async for r in self._dian_ge_flow(event, str(keyword or "").strip(), mode="file"):
            yield r

    @filter.command("点歌语音")
    async def dian_ge_voice(self, event: AstrMessageEvent, keyword: GreedyStr):
        """点歌语音 <歌名>：本次以语音发送"""
        async for r in self._dian_ge_flow(event, str(keyword or "").strip(), mode="voice"):
            yield r

    @filter.command("点歌消息")
    async def dian_ge_text(self, event: AstrMessageEvent, keyword: GreedyStr):
        """点歌消息 <歌名>：本次以文本链接发送"""
        async for r in self._dian_ge_flow(event, str(keyword or "").strip(), mode="text"):
            yield r

    @filter.command("点歌模式")
    async def set_send_mode(self, event: AstrMessageEvent):
        """点歌模式 [卡片|文件|文本]：查看或切换歌曲发送方式"""
        text = (event.message_str or "").strip()
        pos = text.find("点歌模式")
        m = (text[pos + len("点歌模式"):] if pos >= 0 else "").strip().lower()
        async for r in self._set_mode_flow(event, m):
            yield r

    async def _set_mode_flow(self, event: AstrMessageEvent, m: str):
        alias = {
            "卡片": "card", "音乐卡片": "card", "card": "card",
            "文件": "file", "音乐文件": "file", "file": "file",
            "文本": "text", "链接": "text", "消息": "text", "text": "text",
            "语音": "voice", "voice": "voice",
        }
        if not m:
            yield event.plain_result(
                f"当前发送模式：{SEND_MODE_CN[self.send_mode]}（发送「点歌模式 卡片/文件/文本」可切换）"
            )
            return
        target = alias.get(m)
        if not target:
            yield event.plain_result("未知模式，可选：卡片 / 文件 / 文本")
            return
        self.send_mode = target
        try:
            if hasattr(self._config, "save_config"):
                self._config["send_mode"] = target
                self._config.save_config()
        except Exception as e:
            logger.warning(f"[netease_unblock] 保存配置失败（本次运行内仍生效）: {e!r}")
        yield event.plain_result(f"✅ 发送模式已切换为：{SEND_MODE_CN[target]}")

    @filter.command("帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        """点歌插件使用帮助"""
        async for r in self._help_flow(event):
            yield r

    async def _help_flow(self, event: AstrMessageEvent):
        mode = self.send_mode
        tips = (
            "🎵 网易云音乐点歌-flac\n"
            "══════════════════\n"
            "📖 命令（加不加 / 前缀均可）\n"
            "点歌 <歌名>　　　搜索歌曲，回复序号选择\n"
            "点歌 <序号>　　　直接选择上一次结果\n"
            "点歌卡片 <歌名>　以音乐卡片发送\n"
            "点歌文件 <歌名>　以音乐文件发送\n"
            "点歌语音 <歌名>　以语音发送\n"
            "点歌消息 <歌名>　以文本链接发送\n"
            "解锁 <ID/链接>　按 ID 或分享链接解锁\n"
            "点歌模式 [模式]　查看/切换默认发送方式\n"
            "帮助　　　　　　查看本帮助\n"
            "══════════════════\n"
            "💡 示例\n"
            "点歌 咏春\n"
            "点歌文件 咏春\n"
            "点歌语音 咏春\n"
            "解锁 1498523311\n"
            "点歌模式 文件\n"
            "══════════════════\n"
            f"⚙️ 默认发送：{SEND_MODE_CN.get(mode, mode)}"
            + ("（卡片被拒自动回退）\n" if mode == "card" else "\n")
            + f"🕒 列表 {self.retract_seconds} 秒自动撤回｜免前缀：{'开启' if not self.require_prefix else '关闭'}\n"
            "══════════════════\n"
            "👥 作者\n"
            "流水 · 听雨的蛙 · 落雪 · GLM-5.3-Flash"
        )
        yield event.plain_result(tips)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_prefix_free(self, event: AstrMessageEvent):
        """免唤醒前缀触发：直接发「点歌 xx」「解锁 xx」「点歌模式 xx」「帮助」即可使用。

        带前缀的消息（如 /点歌）仍由 AstrBot 命令系统处理，不会重复响应。
        配置「命令必须加 / 前缀」开启后本分发器停用。
        """
        if self.require_prefix:
            return
        text = (event.message_str or "").strip()
        if not text or text.startswith(("/", "#", "@")):
            return
        if text.startswith("点歌模式"):
            rest = text[len("点歌模式"):].strip().lower()
            async for r in self._set_mode_flow(event, rest):
                yield r
        elif text.startswith("点歌文件"):
            async for r in self._dian_ge_flow(event, text[len("点歌文件"):].strip(), mode="file"):
                yield r
        elif text.startswith("点歌语音"):
            async for r in self._dian_ge_flow(event, text[len("点歌语音"):].strip(), mode="voice"):
                yield r
        elif text.startswith("点歌卡片"):
            async for r in self._dian_ge_flow(event, text[len("点歌卡片"):].strip(), mode="card"):
                yield r
        elif text.startswith("点歌消息"):
            async for r in self._dian_ge_flow(event, text[len("点歌消息"):].strip(), mode="text"):
                yield r
        elif text.startswith("点歌"):
            async for r in self._dian_ge_flow(event, text[len("点歌"):].strip()):
                yield r
        elif text.startswith("解锁"):
            async for r in self._unlock_flow(event, text[len("解锁"):].strip()):
                yield r
        elif text == "帮助":
            async for r in self._help_flow(event):
                yield r
        else:
            return
        event.stop_event()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_selection(self, event: AstrMessageEvent):
        """处理选歌的纯数字回复（仅对发起点歌的用户生效，非序号消息不拦截）。"""
        now = time.time()
        if self._pending:
            expired = [
                k for k, v in self._pending.items() if now - v["ts"] > CACHE_JANITOR_SECONDS
            ]
            for k in expired:
                self._pending.pop(k, None)

        key = self._cache_key(event)
        cache = self._pending.get(key)
        if not cache:
            return

        text = event.message_str.strip()
        if not text.isdigit():
            # 非序号消息不拦截，放行给其他插件 / LLM
            return

        if now - cache["ts"] > SELECT_TIMEOUT_SECONDS:
            self._pending.pop(key, None)
            yield event.plain_result("❌ 选歌超时，请重新点歌")
            event.stop_event()
            return

        idx = int(text)
        if idx == 0:
            self._pending.pop(key, None)
            yield event.plain_result("已取消选歌")
            event.stop_event()
            return

        songs = cache["songs"]
        if not (1 <= idx <= len(songs)):
            return  # 超出范围的数字视为普通聊天，不拦截

        self._pending.pop(key, None)  # 进入执行即销毁，防止重复触发
        await self._react(event)  # 选中序号同样贴表情回执
        async for r in self._resolve_results(event, songs[idx - 1], cache.get("mode")):
            yield r
        event.stop_event()
