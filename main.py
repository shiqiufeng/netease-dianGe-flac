"""AstrBot 网易云音乐点歌-flac 插件（网易云直链版）。

工作流程：
1. 通过网易云音乐公开 Web 接口按关键词搜索歌曲；
2. 选中歌曲后调用自部署的 UnblockNeteaseMusic-utils 服务（/match）获取受限歌曲直链；
3. aiocqhttp 平台（NapCat / Lagrange / go-cqhttp）发送 QQ 自定义音乐卡片，
   其他平台发送文本链接。
"""

import asyncio
import json
import os
import random
import re
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

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
ORIGINAL_SCAN_LIMIT = 15
"""搜索时额外拉取并检查的候选数量：网易云前排常被翻唱占据，原唱可能排得很后"""
DEEP_SCAN_PAGES = 7
"""前排找不到原唱时的翻页深扫页数（每页 30 条，覆盖前 210 条）"""
DEEP_SCAN_PAGE_SIZE = 30
"""深扫每页条数"""
DEEP_SCAN_PAUSE_SECONDS = 0.2
"""深扫每页之间的间隔，降低触发网易云限流的概率"""
SEARCH_CACHE_SECONDS = 120
"""同一关键词的搜索结果缓存时长（秒）：群里多人点同一首歌时省掉重复请求"""
UNRESOLVED_COVER_HINT = (
    "⚠️ 这些结果都是翻唱／改编版，网易云没给出原唱条目。\n"
    "要原唱可以：①「歌手 <歌手名>」看热门歌曲后回复序号；②「直链 <歌曲ID>」精确点歌。"
)
"""确认前排全是翻唱、又定位不到原唱时的提示"""
PICK_HINT = f"想听哪首：回复序号直接点歌（{SELECT_TIMEOUT_SECONDS} 秒内有效，回复 0 取消）"
"""列表类命令末尾的统一提示"""
NETEASE_LEVEL = "exhigh"
"""走官方 /song/url/v1 时请求的音质（账号不支持会自动降级）"""
ORIGINAL_SHORTLIST = 3
"""深扫最多留几个同名候选去比评论数"""
POPULAR_RATIO = 5
"""评论数高出这么多倍才认定是原唱（原唱通常比翻唱高几个数量级）"""
POPULAR_MIN_COMMENTS = 20
"""评论数至少这么多才有资格当原唱，避免冷门歌乱换"""

# ---------------------------------------------------------------------- #
# 用自己的洛雪(LX Music)音源脚本取直链
#   音源脚本只提供 musicUrl（搜索由调用方做），所以插件先用酷我按
#   「歌名+歌手」查到歌曲 ID，再把它交给音源脚本换成可播放直链。
# ---------------------------------------------------------------------- #
KUWO_SEARCH_API = (
    "http://search.kuwo.cn/r.s?&correct=1&vipver=1&stype=comprehensive&encoding=utf8"
    "&rformat=json&mobi=1&show_copyright_off=1&searchapi=6&all={}"
)
KUWO_SOURCE_KEY = "kw"
"""音源脚本里酷我的平台名"""
DURATION_TOLERANCE_MS = 3000
"""判定「同一个版本」的时长容差"""
LX_DEFAULT_QUALITY = "320k"
"""向音源脚本请求的音质"""
LX_TIMEOUT_SECONDS = 45.0
"""单个音源脚本的执行超时（音源要连它自己的后端，给宽一点）"""
LX_PLATFORM_CN = {"wy": "网易云", "kw": "酷我", "kg": "酷狗", "tx": "QQ", "mg": "咪咕"}
MATCH_FAIL_HINT = (
    "💡 音源服务取不到就发不出歌。版权下架的原唱（如周杰伦）要靠音源服务里的"
    "跨平台音源去酷我/酷狗找；服务若部署在境外（如 Vercel 默认美国节点）会连不上，"
    "把服务跑在国内网络即可。"
)
"""直链获取失败的排查提示"""
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
TOP_LIST_API = "https://music.163.com/api/toplist"
PLAYLIST_DETAIL_API = "https://music.163.com/api/playlist/detail"
COMMENTS_API = "https://music.163.com/api/v1/resource/comments/R_SO_4_{sid}"
NEW_SONGS_API = "https://music.163.com/api/discovery/new/songs"
RADIO_API = "https://music.163.com/api/v1/radio/get"
NEW_SONG_AREAS = {"全部": 0, "华语": 7, "欧美": 96, "日本": 8, "韩国": 16}
SONG_LINK = "https://music.163.com/#/song?id={}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
}

_ID_FROM_URL = re.compile(r"[?&]id=(\d+)")
_BRACKET_RE = re.compile(r"[（(\[【《][^）)\]】》]*[）)\]】》]")
_COVER_ASCII_RE = re.compile(r"\b(cover|remix|dj|live|ai|instrumental|karaoke)\b")
_CJK_COVER_MARKERS = (
    "翻唱", "原唱", "深情版", "治愈版", "女声版", "男声版", "童声", "钢琴版",
    "吉他版", "纯音乐", "伴奏", "混音", "加速版", "减速版", "烟嗓", "低音版",
    "高音版", "合唱版", "改编", "串烧", "铃声", "片段", "清唱", "哼唱",
    "口哨", "八音盒", "尤克里里", "现场版", "抖音", "完整版", "正式版",
)


class RateLimited(Exception):
    """网易云返回「操作频繁」时抛出，供上层给出可读提示。"""


class SearchResult(list):
    """搜索结果列表，额外记一个标记：前排确认是翻唱但没能定位到原唱。"""

    unresolved_covers = False


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
    """网易云音乐点歌-flac：网易云搜索 + 音源服务直链。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # AstrBotConfig 不做类型校验，空值不合法的配置一律用 or 兜底。
        self.unlock_api = (config.get("unlock_api") or "").rstrip("/")
        self.source = (config.get("source") or "").strip()
        self.lx_source = str(config.get("lx_source") or "").strip()
        self.lx_node = str(config.get("lx_node") or "").strip() or "node"
        self.lx_quality = str(config.get("lx_quality") or "").strip() or LX_DEFAULT_QUALITY
        self._lx_runtime = Path(__file__).resolve().parent / "lx_runtime.js"
        self._lx_files: list[str] = []
        self._lx_checked = False
        self.auto_pick = bool(config.get("auto_pick") or False)
        _po = config.get("prefer_original")
        self.prefer_original = True if _po is None else bool(_po)
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
        self._search_cache: dict[str, tuple] = {}
        self._bad_paths: set[str] = set()  # 后端不存在的取链接口，记住后不再重试
        # 音源服务单独走代理：本机网络可能对音源域名 TLS 干扰（直连被重置），
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
            logger.warning("[netease_unblock] 未配置 API 地址 unlock_api，音源服务不可用。")

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
            "publish": album.get("publishTime") or song.get("publishTime") or 0,
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
    # 原唱识别（网易云搜索前排常年被翻唱占据）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _base_title(name: str) -> str:
        """去掉括号注释后的干净曲名：「稻香 (钢琴版) [原唱: 周杰伦]」→「稻香」。"""
        return _BRACKET_RE.sub("", name or "").strip().lower()

    @staticmethod
    def _looks_like_cover(name: str) -> bool:
        """曲名带翻唱/改编标记（深情版、女声版、钢琴版、Live、Cover…）。"""
        low = (name or "").lower()
        return any(m in low for m in _CJK_COVER_MARKERS) or bool(_COVER_ASCII_RE.search(low))

    @staticmethod
    def _cover_markers(name: str) -> set:
        """曲名里出现的翻唱/版本标记词。"""
        low = (name or "").lower()
        found = {m for m in _CJK_COVER_MARKERS if m in low}
        found |= {m.group(0) for m in _COVER_ASCII_RE.finditer(low)}
        return found

    @classmethod
    def _relevance(cls, keyword: str, song: dict) -> int:
        """搜索相关度：同名优先；没点名版本时压翻唱，点名了就让同款版本浮上来。"""
        target = cls._base_title(keyword)
        name = song.get("name") or ""
        base = cls._base_title(name)
        score = 0
        if base and base == target:
            score += 100
        elif target and target in base:
            score += 50
        elif base and base in target:
            score += 20
        wanted = cls._cover_markers(keyword)
        have = cls._cover_markers(name)
        if wanted:
            # 用户点名要版本（「稻香 深情版」）：带同款标记的加分，其余不额外压
            score += 40 * len(wanted & have)
        elif have:
            score -= 60
        return score - max(0, len(name) - len(keyword)) // 4

    async def _get_details(self, ids: list) -> dict:
        """批量取歌曲详情，返回 {id: 详情原始字典}（一次请求查多个候选）。"""
        clean = [str(i) for i in ids if i]
        if not clean:
            return {}
        try:
            resp = await self._search_client.get(
                SONG_DETAIL_API, params={"ids": "[" + ",".join(clean) + "]"}
            )
            resp.raise_for_status()
            songs = (resp.json() or {}).get("songs") or []
        except Exception as e:
            logger.warning(f"[netease_unblock] 批量歌曲详情失败: {e!r}")
            return {}
        return {s.get("id"): s for s in songs if s.get("id")}

    async def _comments_total(self, song_id) -> int:
        """歌曲评论数——判定「哪个版本才是原唱」最灵的指标。取不到按 0 算。"""
        data = await self._api_get(COMMENTS_API.format(sid=song_id), {"limit": 1})
        try:
            return int((data or {}).get("total") or 0)
        except (TypeError, ValueError):
            return 0

    async def _pick_most_popular(self, songs: list[dict], base: dict | None) -> dict | None:
        """在候选里挑评论数最高的当原唱，但必须**明显**高于当前第一条才换。

        原唱的评论数通常比翻唱高几个数量级（实测「告白气球」周杰伦 349102 条
        vs 翻唱 105 条），比单看发行时间可靠；差距不够大就不动，避免冷门歌乱换。
        """
        base_n = await self._comments_total(base["id"]) if base else 0
        best, best_n = None, 0
        for song in songs[:ORIGINAL_SHORTLIST]:
            if base and song["id"] == base["id"]:
                continue
            n = await self._comments_total(song["id"])
            if n > best_n:
                best, best_n = song, n
        if best is None:
            return None
        if best_n < POPULAR_MIN_COMMENTS or best_n < base_n * POPULAR_RATIO:
            return None
        logger.info(f"[netease_unblock] 按评论数认定原唱: {best['name']} - {best['artists']}"
                    f"（{best_n} 条 vs {base_n} 条）")
        return best

    async def _find_original_deep(self, keyword: str) -> list[dict]:
        """翻页深扫，返回「干净同名」的候选（按专辑发行时间由早到晚，最多几个）。

        网易云会把原唱压到很后面：「告白气球」的周杰伦原版在第 6 页，前 50 条里
        45 条是翻唱。判据是「曲名没有括号后缀」（带括号的几乎都是翻唱/改编/现场），
        再结合发行时间与评论数定夺。
        """
        target = self._base_title(keyword)
        found: dict = {}
        seen: set = set()
        for page in range(DEEP_SCAN_PAGES):
            if page:
                await asyncio.sleep(DEEP_SCAN_PAUSE_SECONDS)
            try:
                rows = await self._search_page(
                    keyword, page * DEEP_SCAN_PAGE_SIZE, DEEP_SCAN_PAGE_SIZE
                )
            except RateLimited:
                logger.warning("[netease_unblock] 深扫被限流，停止翻页")
                break
            except Exception as e:
                logger.warning(f"[netease_unblock] 深扫第 {page + 1} 页失败: {e!r}")
                break
            if not rows:
                break
            for row in rows:
                if row.get("id") in seen:
                    continue
                seen.add(row.get("id"))
                name = row.get("name") or ""
                if _BRACKET_RE.search(name) or self._base_title(name) != target:
                    continue
                ts = ((row.get("album") or {}).get("publishTime")
                      or row.get("publishTime") or 0)
                if not ts:
                    continue
                found[row["id"]] = (ts, row)
            if len(rows) < DEEP_SCAN_PAGE_SIZE:
                break
        ranked = [row for _, row in sorted(found.values(), key=lambda kv: kv[0])]
        if ranked:
            logger.info(f"[netease_unblock] 深扫拿到 {len(ranked)} 个同名候选，"
                        f"最早: {ranked[0].get('name')} id={ranked[0].get('id')}")
        return [self._normalize(row) for row in ranked[:ORIGINAL_SHORTLIST]]

    async def _promote_originals(self, keyword: str, songs: list[dict]) -> tuple[list[dict], bool]:
        """把前排翻唱替换成原唱，返回 (结果列表, 是否「全是翻唱且没找到原唱」)。

        搜索「稻香」时前排是「稻香(深情版) - Lucky小爱」，周杰伦的原唱甚至不进前 10。
        好在翻唱的歌曲详情里带 originSongSimpleData，直接给出原唱 songId，据此前移。
        """
        if not songs or self._looks_like_cover(keyword):
            return songs, False  # 用户点名要翻唱版（如「点歌 稻香 深情版」）时不干预
        head = songs[:ORIGINAL_SCAN_LIMIT]
        details = await self._get_details([s["id"] for s in head])
        if not details:
            return songs, False

        target = self._base_title(keyword)
        known = {s["id"] for s in songs}
        orig_ids: set = set()
        covers: set = set()
        replaced: dict = {}
        for song in head:
            detail = details.get(song["id"]) or {}
            origin = detail.get("originSongSimpleData") or {}
            oid = origin.get("songId")
            # originCoverType 2/3 = 网易云认定这是翻唱/改编（哪怕它没给原唱 songId）
            if detail.get("originCoverType") in (2, 3):
                covers.add(song["id"])
            if not oid:
                continue
            covers.add(song["id"])
            # 标注的原唱必须与本次搜索同名，否则可能是张冠李戴的转辑版本
            if oid in orig_ids or self._base_title(origin.get("name") or "") != target:
                continue
            if oid in known:
                # 原唱本来就在结果里：不用再拉一次详情，标记出来排到最前即可
                orig_ids.add(oid)
                continue
            original = await self._get_song_detail(oid)
            if original is None or self._base_title(original["name"]) != target:
                continue
            replaced[song["id"]] = original
            orig_ids.add(oid)
            known.add(oid)

        out = [replaced.get(s["id"], s) for s in songs]
        if not covers:
            return out, False

        # 网易云明确标注出的原唱直接排最前：同名但未标注的重录版（「稻香 - Lie」这类）
        # 相关度会打平，只靠打分排不到第一位。
        originals = [s for s in out if s["id"] in orig_ids]
        rest = [s for s in out if s["id"] not in orig_ids]
        rest.sort(
            key=lambda s: self._relevance(keyword, s) - (40 if s["id"] in covers else 0),
            reverse=True,
        )

        if originals:
            return originals + rest, False
        if not covers:
            return rest, False  # 前排没发现翻唱，不动它

        top = rest[0] if rest else None
        top_id = top["id"] if top else None
        # 前排没找到原唱，且这个曲名下翻唱扎堆 → 原唱多半被压到了后面，翻页深扫一次。
        # （「告白气球」的原版在第 6 页；前排还混着未标注的重录版，
        #   而 originCoverType=1 也不可信）
        deep_list = await self._find_original_deep(keyword)
        if not deep_list:
            return rest, top_id in covers

        # 深扫候选里同样混着翻唱，而且**有的翻唱评论数比原唱还高**，会被下面的
        # 「评论数判据」误当成原唱推上来（实测「伯虎说」：翻唱「玉狐若児/十三」
        # 评论数压过原唱「伯爵Johnny/唐伯虎Annie」，结果点歌发出去的成了翻唱）。
        # 所以先用详情里的 originCoverType 把网易云自己标注的翻唱/改编剔掉，
        # 并顺手收集它在 originSongSimpleData 里给出的原唱 songId。
        deep_details = await self._get_details([s["id"] for s in deep_list])
        clean: list = []
        declared: list = []
        for s in deep_list:
            detail = deep_details.get(s["id"]) or {}
            # 先取它标注的原唱：翻唱自己就带着正确答案（originSongSimpleData.songId）
            origin = detail.get("originSongSimpleData") or {}
            oid = origin.get("songId")
            if oid and self._base_title(origin.get("name") or "") == target:
                declared.append(str(oid))
            if detail.get("originCoverType") in (2, 3):
                continue  # 网易云标注的翻唱/改编，没资格当原唱
            clean.append(s)

        # 网易云明确标注出的原唱最可信：找到就直接排最前（已在列表里就用现成的）。
        for oid in declared:
            here = next((s for s in rest if s["id"] == oid), None)
            if here is not None:
                return [here] + [s for s in rest if s["id"] != oid], False
            original = await self._get_song_detail(oid)
            if original is not None and self._base_title(original["name"]) == target:
                return [original] + [s for s in rest if s["id"] != original["id"]], False

        if not clean:
            return rest, top_id in covers

        # 判据一：评论数。原唱通常比翻唱高几个数量级，这个最准（只在没被标成翻唱的候选里比）。
        if top is not None and top["id"] not in covers:
            popular = await self._pick_most_popular([top] + clean, top)
            if popular:
                return [popular] + [s for s in rest if s["id"] != popular["id"]], False

        # 判据二：发行时间。评论数持平（或拿不到）时，用「更早发行的同名版本」兜底。
        deep = clean[0]
        if top is None or (deep.get("publish") or 0) < (top.get("publish") or 0):
            return [deep] + [s for s in rest if s["id"] != deep["id"]], False
        return rest, top_id in covers

    # ------------------------------------------------------------------ #
    # API 请求
    # ------------------------------------------------------------------ #
    async def _search_page(self, keyword: str, offset: int, limit: int) -> list[dict]:
        """取一页搜索结果。被网易云限流时抛 RateLimited，其余异常照常抛出。"""
        resp = await self._search_client.get(
            SEARCH_API,
            params={
                "s": keyword,
                "type": 1,
                "offset": offset,
                "limit": limit,
                "total": "true",
            },
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        if payload.get("code") == 406 or "操作频繁" in str(payload.get("msg") or ""):
            raise RateLimited()
        return ((payload.get("result") or {}).get("songs")) or []

    async def _search(self, keyword: str, limit: int | None = None) -> list[dict]:
        """搜索歌曲：多拉候选 → 相关度排序 → 把翻唱换成原唱（结果带短时缓存）。"""
        want = limit or self.limit
        now = time.time()
        hit = self._search_cache.get(keyword)
        if hit and now - hit[0] < SEARCH_CACHE_SECONDS:
            rows, unresolved = hit[1], hit[2]
        else:
            rows, unresolved = await self._search_fresh(keyword)
            self._search_cache[keyword] = (now, rows, unresolved)
            if len(self._search_cache) > 100:  # 顺手清掉过期项，别让缓存无限涨
                for stale in [k for k, v in self._search_cache.items()
                              if now - v[0] >= SEARCH_CACHE_SECONDS]:
                    self._search_cache.pop(stale, None)
        out = SearchResult(rows[:want])
        out.unresolved_covers = unresolved
        return out

    async def _search_fresh(self, keyword: str) -> tuple[list[dict], bool]:
        """真正走网络的搜索：拉候选、排序、尝试换成原唱。"""
        fetch = max(self.limit, ORIGINAL_SCAN_LIMIT)
        songs = await self._search_page(keyword, 0, fetch)
        result = [self._normalize(s) for s in songs]
        result.sort(key=lambda s: self._relevance(keyword, s), reverse=True)
        unresolved = False
        if self.prefer_original:
            result, unresolved = await self._promote_originals(keyword, result)
        return result, unresolved

    async def _get_song_detail(self, song_id: str) -> dict | None:
        resp = await self._search_client.get(
            SONG_DETAIL_API, params={"id": song_id, "ids": f"[{song_id}]"}
        )
        resp.raise_for_status()
        songs = (resp.json() or {}).get("songs") or []
        return self._normalize(songs[0]) if songs else None

    async def _match(self, song_id) -> str | None:
        """取可播放直链，自动适配两种后端。

        ① UnblockNeteaseMusic-utils：`/match?id=&source=`
        ② 网易云 API Enhanced（自带解灰）：`/song/url/match?id=&source=`，
           再退回 `/song/url/v1?id=&level=&unblock=true`，最后是官方 `/song/url/v1`。
        哪个后端装在 API 地址上就用哪个，装错了也能自己认出来（打不通的路径记下来不再重试）。
        """
        if not self.unlock_api:
            return None

        # 解灰类接口：按配置的音源优先级逐个试
        for path in ("/match", "/song/url/match"):
            if path in self._bad_paths:
                continue
            for name in self._source_list():
                params = {"id": str(song_id)}
                if name:
                    params["source"] = name
                url = await self._api_url(path, params)
                if url:
                    return url

        # 官方接口兜底：配了 VIP Cookie 的部署能直接返回可听直链
        for params in (
            {"id": str(song_id), "level": NETEASE_LEVEL, "unblock": "true"},
            {"id": str(song_id), "level": NETEASE_LEVEL},
        ):
            url = await self._api_url("/song/url/v1", params)
            if url:
                return url
        return None

    def _source_list(self) -> list[str]:
        """配置里的音源优先级；auto 用空串表示「交给服务端自动选」。"""
        if not self.source:
            return [""]
        names = [s.strip() for s in self.source.replace("，", ",").split(",") if s.strip()]
        out: list[str] = []
        for name in names:
            token = "" if name.lower() == "auto" else name
            if token not in out:
                out.append(token)
        return out or [""]

    async def _api_url(self, path: str, params: dict) -> str | None:
        """请求一个取直链接口并校验，返回可用 URL；接口不存在就记下来不再重试。"""
        try:
            resp = await self._unlock_client.get(f"{self.unlock_api}{path}", params=params)
        except Exception as e:
            logger.warning(f"[netease_unblock] {path} 请求失败: {e!r}")
            return None
        if resp.status_code == 404:
            self._bad_paths.add(path)
            logger.info(f"[netease_unblock] {path} 在 {self.unlock_api} 上不存在，后续跳过")
            return None
        try:
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.warning(f"[netease_unblock] {path} 响应异常: {str(e)[:80]}")
            return None
        url = self._extract_url(payload)
        if not url:
            return None
        if self.verify_audio and not await self._is_audio(url):
            logger.warning(f"[netease_unblock] {path} 返回的不是音频（疑似 VIP 占位页），跳过")
            return None
        logger.info(f"[netease_unblock] 歌曲 {params.get('id')} 命中 {path}"
                    f"{'/' + params['source'] if params.get('source') else ''}")
        return url

    @staticmethod
    def _extract_url(payload) -> str | None:
        """兼容各家返回结构：data 直接是 URL / data 是 [{url}] / data 是 {url}。"""
        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        candidates = []
        if isinstance(data, str):
            candidates.append(data)
        elif isinstance(data, dict):
            candidates += [data.get("url"), data.get("proxyUrl")]
        elif isinstance(data, list) and data:
            head = data[0]
            if isinstance(head, dict):
                candidates += [head.get("url"), head.get("proxyUrl")]
        # 顶层也有的放最后
        candidates += [payload.get("url"), payload.get("proxyUrl")]
        for item in candidates:
            if isinstance(item, str) and item.startswith("http"):
                return item
        return None

    # ------------------------------------------------------------------ #
    # 自己的洛雪音源
    # ------------------------------------------------------------------ #
    def _lx_sources(self) -> list[str]:
        """解析配置里的音源脚本路径（支持目录、多个路径、逗号/换行分隔）。

        目录会展开成里面的 *.js；同一脚本可能既在目录里又被单独列出，
        按绝对路径去重，避免同一个音源被跑两遍。
        """
        if self._lx_checked:
            return self._lx_files
        self._lx_checked = True
        found: list[str] = []
        seen: set[str] = set()

        def _add(path: Path) -> None:
            key = str(path.resolve())
            if key in seen:
                return
            seen.add(key)
            found.append(str(path))

        raw = (self.lx_source or "").replace("，", ",").replace("\n", ",")
        for item in raw.split(","):
            item = item.strip().strip('"')
            if not item:
                continue
            path = Path(item)
            if path.is_dir():
                for js in sorted(path.glob("*.js")):
                    _add(js)
            elif path.is_file():
                _add(path)
            else:
                logger.warning(f"[netease_unblock] 音源脚本不存在: {item}")
        self._lx_files = found
        if found:
            logger.info(f"[netease_unblock] 已加载 {len(found)} 个洛雪音源脚本")
        return found

    @classmethod
    def _pick_same_version(cls, candidates: list[dict], song: dict) -> dict | None:
        """在别家搜索结果里挑「同一个版本」的那条：同名 + 歌手对得上 + 时长一致。"""
        want_name = cls._base_title(song.get("name") or "")
        want_artist = re.sub(r"\s|/", "", song.get("artists") or "")
        want_dur = int(song.get("duration") or 0)
        want_is_cover = cls._looks_like_cover(song.get("name") or "")
        best, best_score = None, 0
        for cand in candidates:
            cand_name = cand.get("name") or ""
            if cls._base_title(cand_name) != want_name:
                continue
            if not want_is_cover and cls._looks_like_cover(cand_name):
                continue
            artist = re.sub(r"\s|/", "", cand.get("artist") or "")
            artist_ok = bool(want_artist and artist
                             and (want_artist in artist or artist in want_artist))
            if want_artist and artist and not artist_ok:
                continue
            dur = int(cand.get("duration") or 0)
            if want_dur and dur and abs(dur - want_dur) > DURATION_TOLERANCE_MS * 3:
                continue
            score = 60 + (30 if artist_ok else 0)
            if want_dur and dur and abs(dur - want_dur) <= DURATION_TOLERANCE_MS:
                score += 40
            if score > best_score:
                best, best_score = cand, score
        return best

    async def _kuwo_songid(self, song: dict) -> str | None:
        """按「歌名 + 歌手」在酷我查出歌曲 ID，交给音源脚本用。"""
        keyword = f"{song.get('name') or ''} {song.get('artists') or ''}".strip()
        if not keyword:
            return None
        resp = await self._unlock_client.get(KUWO_SEARCH_API.format(quote(keyword)))
        resp.raise_for_status()
        content = (resp.json() or {}).get("content") or []
        page = content[1] if len(content) > 1 and isinstance(content[1], dict) else {}
        rows = []
        for item in ((page.get("musicpage") or {}).get("abslist") or []):
            rid = str(item.get("MUSICRID") or "").split("_")[-1]
            if not rid.isdigit():
                continue
            rows.append({
                "id": rid,
                "name": item.get("SONGNAME") or "",
                "artist": item.get("ARTIST") or "",
                "duration": int(item.get("DURATION") or 0) * 1000,
            })
        best = self._pick_same_version(rows, song)
        return best["id"] if best else None

    async def _call_lx(self, source_file: str, payload: dict) -> str | None:
        """跑一次音源脚本，返回它给出的直链（失败返回 None）。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.lx_node, str(self._lx_runtime), source_file,
                json.dumps(payload, ensure_ascii=False),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.warning(f"[netease_unblock] 找不到 node（{self.lx_node}），洛雪音源不可用")
            return None
        except Exception as e:
            logger.warning(f"[netease_unblock] 启动音源脚本失败: {e!r}")
            return None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(),
                                            timeout=LX_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning(f"[netease_unblock] 音源脚本超时: {Path(source_file).name}")
            return None
        text = (out or b"").decode("utf-8", "replace").strip()
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            url = data.get("url")
            if data.get("ok") and isinstance(url, str) and url.startswith("http"):
                return url
            if data.get("error"):
                logger.info(f"[netease_unblock] {Path(source_file).name}: {data['error']}")
            return None
        return None

    async def _lx_url(self, song: dict) -> str | None:
        """用自己的洛雪音源取直链：先查酷我歌曲 ID，再交给音源脚本换直链。"""
        files = self._lx_sources()
        if not files or not (song.get("name") or "").strip():
            return None
        try:
            rid = await self._kuwo_songid(song)
        except Exception as e:
            logger.warning(f"[netease_unblock] 酷我查歌曲 ID 失败: {e!r}")
            return None
        if not rid:
            logger.info(f"[netease_unblock] 酷我没查到「{song.get('name')}」，跳过洛雪音源")
            return None
        payload = {
            "source": KUWO_SOURCE_KEY,
            "quality": self.lx_quality,
            "musicInfo": {
                "songmid": rid, "rid": rid, "id": rid, "hash": rid,
                "name": song.get("name") or "",
                "singer": song.get("artists") or "",
                "albumName": song.get("album") or "",
                "interval": "",
            },
        }
        for source_file in files:
            url = await self._call_lx(source_file, payload)
            if not url:
                continue
            if self.verify_audio and not await self._is_audio(url):
                logger.info(f"[netease_unblock] {Path(source_file).name} 返回的不是音频，换下一个")
                continue
            logger.info(f"[netease_unblock] 洛雪音源 {Path(source_file).name} 取到直链")
            return url
        return None

    async def _is_audio(self, url: str) -> bool:
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

    async def _resolve_results(self, event: AstrMessageEvent, song: dict,
                               mode: str | None = None):
        """获取直链并构造发送结果（异步生成器）：按指定/默认模式发卡片 / 文件 / 语音 / 文本。

        直链优先问**自己的洛雪音源脚本**（配置 `lx_source` 指向的 .js），
        取不到再回落到 `unlock_api` 音源服务；两边都没有才报错，不换别的版本充数。
        """
        mode = mode or self.send_mode
        link = SONG_LINK.format(song["id"])
        audio = None
        try:
            audio = await self._lx_url(song)  # 先用你自己的洛雪音源
        except Exception as e:
            logger.warning(f"[netease_unblock] 洛雪音源异常: {e!r}")
        if not audio:
            try:
                audio = await self._match(song["id"])  # 再用音源服务兜底
            except Exception as e:
                hint = "（已配置代理，请确认代理进程存活）" if self.proxy else "（可尝试在插件配置里填写本机代理）"
                logger.error(f"[netease_unblock] 音源请求异常: {e!r} {hint}")
                audio = None

        if not audio:
            api = self.unlock_api or "（未配置 API 地址）"
            r = await self._say(event, 
                f"❌ {song['name']} - {song['artists'] or '未知歌手'}\n"
                f"获取直链失败：音源服务 {api} 没有可用音源或不可达\n"
                f"{MATCH_FAIL_HINT}\n"
                f"🔗 {link}"
            )
            if r is not None:
                yield r
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
            r = await self._say(event, text)
            if r is not None:
                yield r
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

        r = await self._say(event, text)
        if r is not None:
            yield r

    async def _progress(self, event: AstrMessageEvent, text: str):
        """进度提示：与 _say 一致，直发并定时撤回，否则普通发送。"""
        r = await self._say(event, text)
        if r is not None:
            yield r

    async def _send_file(self, event: AstrMessageEvent, song: dict, audio: str):
        """下载音乐并以文件形式发送，内嵌歌词后定时删除本地文件。"""
        async for r in self._progress(event, f"⏳ 正在下载「{song['name']}」，请稍候…"):
            yield r
        try:
            path = await self._download_song(song, audio)
        except Exception as e:
            logger.error(f"[netease_unblock] 文件下载失败: {e!r}")
            r = await self._say(event, f"❌ 文件下载失败：{e!r}\n▶️ 直链：{audio}")
            if r is not None:
                yield r
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
            r = await self._say(event, f"❌ 文件发送失败\n▶️ 直链：{audio}")
            if r is not None:
                yield r
        finally:
            if self.delete_file_seconds > 0:
                asyncio.create_task(self._delete_later(path, self.delete_file_seconds))

    async def _fetch_lyrics(self, song_id) -> str | None:
        """获取网易云 LRC 歌词，无歌词返回 None。"""
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
            r = await self._say(event, f"❌ 语音获取失败：{e!r}\n▶️ 直链：{audio}")
            if r is not None:
                yield r
            return
        try:
            yield event.chain_result([Record.fromFileSystem(str(path))])
        except Exception as e:
            logger.error(f"[netease_unblock] 语音发送失败: {e!r}")
            r = await self._say(event, f"❌ 语音发送失败\n▶️ 直链：{audio}")
            if r is not None:
                yield r
        finally:
            # 语音在发送时已被转 base64，临时文件立即清理，不占存储
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                logger.warning(f"[netease_unblock] 清理语音临时文件失败: {e}")

    async def _download_song(self, song: dict, url: str) -> Path:
        """流式下载歌曲，按「歌名.格式」命名。"""
        self._sweep_stale_downloads()
        ext = PurePosixPath(urlparse(url).path).suffix.lstrip(".").lower()
        if ext not in AUDIO_EXTS:
            ext = "mp3"
        stem = self._sanitize_filename(song["name"])
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

    async def _say(self, event: AstrMessageEvent, text: str):
        """**所有文字消息的统一出口**：能撤回就直发 + 定时撤回，撤回不了就普通发送。

        返回 None 表示已直发（调用方不要再 yield）；否则返回可 yield 的结果对象。
        """
        if (self.retract_seconds > 0 and event.get_platform_name() == "aiocqhttp"
                and hasattr(event, "bot")):
            if await self._send_and_schedule_retract(event, text, self.retract_seconds):
                return None
        return event.plain_result(text)

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
    # 发现音乐（公开接口）
    # ------------------------------------------------------------------ #
    async def _api_get(self, path: str, params: dict | None = None) -> dict | None:
        """请求网易云公开接口，失败返回 None。"""
        try:
            resp = await self._search_client.get(path, params=params or {})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"[netease_unblock] 接口 {path} 请求失败: {e!r}")
            return None

    async def _search_type(self, keyword: str, ntype: int, limit: int = 3) -> list[dict]:
        """按类型搜索：100 歌手 / 10 专辑 / 1000 歌单。"""
        resp = await self._search_client.get(
            SEARCH_API,
            params={"s": keyword, "type": ntype, "offset": 0, "limit": limit, "total": "true"},
        )
        resp.raise_for_status()
        result = (resp.json() or {}).get("result") or {}
        if ntype == 100:
            return result.get("artists") or []
        if ntype == 10:
            return result.get("albums") or []
        if ntype == 1000:
            return result.get("playlists") or []
        return result.get("songs") or []

    async def _resolve_song_id(self, text: str) -> int | None:
        """「关键词|ID」或纯数字 → 歌曲ID；否则按关键词搜索取第一首。"""
        text = text.strip()
        if "|" in text:
            text = text.split("|")[-1].strip()
        if text.isdigit():
            return int(text)
        try:
            songs = await self._search(text)
        except Exception:
            return None
        return songs[0]["id"] if songs else None

    def _remember_pick(self, event: AstrMessageEvent, songs: list[dict], mode: str | None = None) -> None:
        """登记序号选歌缓存：列表类命令发完后，用户直接回复数字即可点歌。"""
        self._pending[self._cache_key(event)] = {
            "stage": "pick_song",
            "songs": songs,
            "ts": time.time(),
            "mode": mode,
        }

    async def _lyrics_flow(self, event: AstrMessageEvent, text: str):
        await self._react(event)  # 命令回执
        if not text:
            r = await self._say(event, "用法：歌词 <歌名|ID>（如：歌词 晴天 或 歌词 晴天|186016）")
            if r is not None:
                yield r
            return
        sid = await self._resolve_song_id(text)
        if not sid:
            r = await self._say(event, f"❌ 未找到「{text}」对应的歌曲")
            if r is not None:
                yield r
            return
        lrc = await self._fetch_lyrics(sid)
        if not lrc:
            r = await self._say(event, "❌ 该歌曲暂无歌词（或为纯音乐）")
            if r is not None:
                yield r
            return
        lines = lrc.splitlines()
        if len(lines) > 80:
            lines = lines[:80] + ["……（歌词过长已截断）"]
        r = await self._say(event, "🎤 歌词：\n" + "\n".join(lines))
        if r is not None:
            yield r

    async def _toplist_flow(self, event: AstrMessageEvent, arg: str):
        await self._react(event)  # 命令回执
        data = await self._api_get(TOP_LIST_API)
        boards = (data or {}).get("list") or []
        if not boards:
            r = await self._say(event, "❌ 排行榜获取失败")
            if r is not None:
                yield r
            return
        if not arg:
            shown = boards[:15]
            lines = ["🏆 官方排行榜（回复序号查看曲目，或「排行 榜单名」）："]
            for i, b in enumerate(shown, 1):
                lines.append(f"{i}. {b.get('name')}（{b.get('updateFrequency') or ''}）")
            self._pending[self._cache_key(event)] = {
                "stage": "pick_board",
                "boards": [{"id": b.get("id"), "name": b.get("name")} for b in shown],
                "ts": time.time(),
            }
            r = await self._say(event, "\n".join(lines))
            if r is not None:
                yield r
            return
        target = next(
            (b for b in boards if arg in (b.get("name") or "") or (b.get("name") or "") in arg),
            None,
        )
        if not target:
            r = await self._say(event, f"❌ 未找到榜单「{arg}」，回复「排行」查看全部榜单")
            if r is not None:
                yield r
            return
        detail = await self._api_get(PLAYLIST_DETAIL_API, {"id": target.get("id")})
        tracks = ((detail or {}).get("result") or {}).get("tracks") or []
        if not tracks:
            r = await self._say(event, "❌ 榜单曲目获取失败")
            if r is not None:
                yield r
            return
        shown = [self._normalize(s) for s in tracks[:10]]
        lines = [f"🏆 {target.get('name')}（{target.get('updateFrequency') or ''}）"]
        for i, song in enumerate(shown, 1):
            lines.append(f"{i}. {self._fmt(song)}")
        self._remember_pick(event, shown)
        lines.append(PICK_HINT)
        r = await self._say(event, "\n".join(lines))
        if r is not None:
            yield r

    async def _artist_flow(self, event: AstrMessageEvent, kw: str):
        await self._react(event)  # 命令回执
        if not kw:
            r = await self._say(event, "用法：歌手 <名字>（如：歌手 周杰伦）")
            if r is not None:
                yield r
            return
        try:
            artists = await self._search_type(kw, 100, 3)
        except Exception as e:
            logger.warning(f"[netease_unblock] 歌手搜索失败: {e!r}")
            artists = []
        target = next((a for a in artists if a.get("name") == kw), artists[0] if artists else None)
        if not target:
            r = await self._say(event, f"❌ 未找到歌手「{kw}」")
            if r is not None:
                yield r
            return
        detail = await self._api_get(f"https://music.163.com/api/artist/{target.get('id')}")
        hot = (detail or {}).get("hotSongs") or []
        if not hot:
            r = await self._say(event, "❌ 热门歌曲获取失败")
            if r is not None:
                yield r
            return
        shown = [self._normalize(s) for s in hot[:10]]
        lines = [f"🎤 {target.get('name')} 的热门歌曲"]
        for i, song in enumerate(shown, 1):
            lines.append(f"{i}. {self._fmt(song)}")
        self._remember_pick(event, shown)
        lines.append(PICK_HINT)
        r = await self._say(event, "\n".join(lines))
        if r is not None:
            yield r

    async def _album_flow(self, event: AstrMessageEvent, kw: str):
        await self._react(event)  # 命令回执
        if not kw:
            r = await self._say(event, "用法：专辑 <名字>（如：专辑 叶惠美）")
            if r is not None:
                yield r
            return
        try:
            albums = await self._search_type(kw, 10, 3)
        except Exception as e:
            logger.warning(f"[netease_unblock] 专辑搜索失败: {e!r}")
            albums = []
        target = next((a for a in albums if a.get("name") == kw), albums[0] if albums else None)
        if not target:
            r = await self._say(event, f"❌ 未找到专辑「{kw}」")
            if r is not None:
                yield r
            return
        detail = await self._api_get(f"https://music.163.com/api/album/{target.get('id')}")
        songs = (detail or {}).get("songs") or ((detail or {}).get("album") or {}).get("songs") or []
        if not songs:
            r = await self._say(event, "❌ 专辑曲目获取失败")
            if r is not None:
                yield r
            return
        artist_name = ((target.get("artist") or {}).get("name")) or (
            ((detail or {}).get("album") or {}).get("artist") or {}
        ).get("name") or ""
        shown = []
        for s in songs[:10]:
            song = self._normalize(s)
            if not song.get("album"):
                song["album"] = target.get("name") or ""
            shown.append(song)
        lines = [f"💿 专辑《{target.get('name')}》- {artist_name}（共 {len(songs)} 首）"]
        for i, song in enumerate(shown, 1):
            lines.append(f"{i}. {self._fmt(song)}")
        self._remember_pick(event, shown)
        lines.append(PICK_HINT)
        r = await self._say(event, "\n".join(lines))
        if r is not None:
            yield r

    async def _playlist_flow(self, event: AstrMessageEvent, kw: str):
        await self._react(event)  # 命令回执
        if not kw:
            r = await self._say(event, "用法：歌单 <关键词>（如：歌单 华语）")
            if r is not None:
                yield r
            return
        try:
            playlists = await self._search_type(kw, 1000, 3)
        except Exception as e:
            logger.warning(f"[netease_unblock] 歌单搜索失败: {e!r}")
            playlists = []
        target = playlists[0] if playlists else None
        if not target:
            r = await self._say(event, f"❌ 未找到歌单「{kw}」")
            if r is not None:
                yield r
            return
        detail = await self._api_get(PLAYLIST_DETAIL_API, {"id": target.get("id")})
        tracks = ((detail or {}).get("result") or {}).get("tracks") or []
        if not tracks:
            r = await self._say(event, "❌ 歌单曲目获取失败")
            if r is not None:
                yield r
            return
        play = target.get("playCount")
        play = f"{play} 次播放" if play else "热门歌单"
        shown = [self._normalize(s) for s in tracks[:10]]
        lines = [f"📋 歌单《{target.get('name')}》（{play}）"]
        for i, song in enumerate(shown, 1):
            lines.append(f"{i}. {self._fmt(song)}")
        self._remember_pick(event, shown)
        lines.append(PICK_HINT)
        r = await self._say(event, "\n".join(lines))
        if r is not None:
            yield r

    async def _comments_flow(self, event: AstrMessageEvent, text: str):
        await self._react(event)  # 命令回执
        if not text:
            r = await self._say(event, "用法：评论 <歌名|ID>（如：评论 晴天 或 评论 186016）")
            if r is not None:
                yield r
            return
        sid = await self._resolve_song_id(text)
        if not sid:
            r = await self._say(event, f"❌ 未找到「{text}」对应的歌曲")
            if r is not None:
                yield r
            return
        data = await self._api_get(COMMENTS_API.format(sid=sid), {"limit": 5})
        hot = (data or {}).get("hotComments") or []
        if not hot:
            r = await self._say(event, "❌ 该歌曲暂无热评")
            if r is not None:
                yield r
            return
        total = (data or {}).get("total")
        lines = [f"💬 热评（共 {total} 条）：" if total else "💬 热评："]
        for i, c in enumerate(hot[:5], 1):
            user = ((c.get("user") or {}).get("nickname")) or "匿名"
            content = (c.get("content") or "").strip().replace("\n", " ")
            if len(content) > 90:
                content = content[:90] + "……"
            lines.append(f"{i}. {user}：{content}（赞 {c.get('likedCount', 0)}）")
        r = await self._say(event, "\n".join(lines))
        if r is not None:
            yield r

    async def _new_songs_flow(self, event: AstrMessageEvent, arg: str):
        await self._react(event)  # 命令回执
        area = (arg or "华语").strip()
        area_id = NEW_SONG_AREAS.get(area)
        if area_id is None:
            r = await self._say(event, "可选地区：全部 / 华语 / 欧美 / 日本 / 韩国（如：新歌 欧美）")
            if r is not None:
                yield r
            return
        data = await self._api_get(NEW_SONGS_API, {"areaId": area_id})
        songs = (data or {}).get("data") or []
        if not songs:
            r = await self._say(event, "❌ 新歌速递获取失败")
            if r is not None:
                yield r
            return
        shown = [self._normalize(s) for s in songs[:10]]
        lines = [f"🆕 {area}新歌速递："]
        for i, song in enumerate(shown, 1):
            lines.append(f"{i}. {self._fmt(song)}")
        self._remember_pick(event, shown)
        lines.append(PICK_HINT)
        r = await self._say(event, "\n".join(lines))
        if r is not None:
            yield r

    async def _random_flow(self, event: AstrMessageEvent):
        await self._react(event)  # 命令回执
        data = await self._api_get(RADIO_API)
        songs = (data or {}).get("data") or []
        if not songs:
            r = await self._say(event, "❌ 随机歌曲获取失败，请稍后重试")
            if r is not None:
                yield r
            return
        song = self._normalize(random.choice(songs))
        async for r in self._resolve_results(event, song):
            yield r

    async def _serial_flow(self, event: AstrMessageEvent, arg: str):
        """连播当前点歌列表：按语音顺序发送，最多 10 首（防刷屏）。"""
        key = self._cache_key(event)
        pending = self._pending.get(key)
        if not pending or pending.get("stage") != "pick_song" or not pending.get("songs"):
            r = await self._say(event, "❌ 没有待连播的列表，先「点歌 关键词」搜索一次再用连播")
            if r is not None:
                yield r
            return
        try:
            n = int(arg) if arg else 5
        except (TypeError, ValueError):
            n = 5
        n = max(1, min(n, 10))
        songs = pending["songs"][:n]
        self._pending.pop(key, None)
        await self._react(event)
        ok = 0
        for i, song in enumerate(songs, 1):
            r = await self._say(event, f"▶️ 连播 {i}/{len(songs)}：{song['name']} - {song['artists'] or '未知歌手'}")
            if r is not None:
                yield r
            try:
                async for r in self._resolve_results(event, song, mode="voice"):
                    yield r
                ok += 1
            except Exception as e:
                logger.warning(f"[netease_unblock] 连播第 {i} 首失败: {e!r}")
            await asyncio.sleep(2)
        r = await self._say(event, f"✅ 连播结束（成功 {ok}/{len(songs)}）")
        if r is not None:
            yield r

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
            r = await self._say(event, 
                "用法：点歌 <歌名>（直接发「点歌 歌名」即可，无需前缀）\n也支持：直链 <歌曲ID或分享链接>"
            )
            if r is not None:
                yield r
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
        except RateLimited:
            r = await self._say(event, "⏳ 网易云提示操作频繁，请稍等十几秒再点歌")
            if r is not None:
                yield r
            return
        except Exception as e:
            logger.error(f"[netease_unblock] 搜索失败: {e!r}", exc_info=True)
            r = await self._say(event, "❌ 搜索歌曲失败，请稍后重试")
            if r is not None:
                yield r
            return
        if not songs:
            r = await self._say(event, f"❌ 未找到「{kw}」相关的歌曲")
            if r is not None:
                yield r
            return

        if self.auto_pick or len(songs) == 1:
            if getattr(songs, "unresolved_covers", False):
                r = await self._say(event, UNRESOLVED_COVER_HINT)
                if r is not None:
                    yield r
            async for r in self._resolve_results(event, songs[0], mode):
                yield r
            return

        lines = ["🎵 搜索结果："]
        for i, song in enumerate(songs, 1):
            lines.append(f"{i}. {self._fmt(song)}")
        if getattr(songs, "unresolved_covers", False):
            lines.append(UNRESOLVED_COVER_HINT)
        lines.append(f"回复序号选择（{SELECT_TIMEOUT_SECONDS} 秒内有效，回复 0 取消）")
        self._pending[key] = {"stage": "pick_song", "songs": songs, "ts": time.time(), "mode": mode}

        text = "\n".join(lines)
        if self.retract_seconds > 0 and event.get_platform_name() == "aiocqhttp" and hasattr(event, "bot"):
            if await self._send_and_schedule_retract(event, text, self.retract_seconds):
                return
        r = await self._say(event, text)
        if r is not None:
            yield r

    @filter.command("直链")
    async def unlock(self, event: AstrMessageEvent, target: GreedyStr):
        """直链 <网易云歌曲ID或分享链接>：按 ID 或链接获取歌曲直链"""
        async for r in self._unlock_flow(event, str(target or "").strip()):
            yield r

    async def _unlock_flow(self, event: AstrMessageEvent, text: str):
        m = _ID_FROM_URL.search(text)
        song_id = m.group(1) if m else (text if text.isdigit() else "")
        if not song_id:
            r = await self._say(event, "用法：直链 <歌曲ID 或 歌曲分享链接>（直接发「直链 ID」即可）")
            if r is not None:
                yield r
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
        await self._react(event)  # 命令回执
        alias = {
            "卡片": "card", "音乐卡片": "card", "card": "card",
            "文件": "file", "音乐文件": "file", "file": "file",
            "文本": "text", "链接": "text", "消息": "text", "text": "text",
            "语音": "voice", "voice": "voice",
        }
        if not m:
            r = await self._say(event, 
                f"当前发送模式：{SEND_MODE_CN[self.send_mode]}（发送「点歌模式 卡片/文件/文本」可切换）"
            )
            if r is not None:
                yield r
            return
        target = alias.get(m)
        if not target:
            r = await self._say(event, "未知模式，可选：卡片 / 文件 / 文本")
            if r is not None:
                yield r
            return
        self.send_mode = target
        try:
            if hasattr(self._config, "save_config"):
                self._config["send_mode"] = target
                self._config.save_config()
        except Exception as e:
            logger.warning(f"[netease_unblock] 保存配置失败（本次运行内仍生效）: {e!r}")
        r = await self._say(event, f"✅ 发送模式已切换为：{SEND_MODE_CN[target]}")
        if r is not None:
            yield r

    @filter.command("帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        """点歌插件使用帮助"""
        async for r in self._help_flow(event):
            yield r

    async def _help_flow(self, event: AstrMessageEvent):
        await self._react(event)  # 命令回执
        mode = self.send_mode
        tips = (
            "🎵 网易云音乐点歌-flac v2.5.1\n"
            "══════════════════\n"
            "📖 命令（加不加 / 前缀均可）\n"
            "点歌 <歌名>　　　搜索歌曲，回复序号选择\n"
            "点歌 <序号>　　　直接选择上一次结果\n"
            "点歌卡片 <歌名>　以音乐卡片发送\n"
            "点歌文件 <歌名>　以音乐文件发送\n"
            "点歌语音 <歌名>　以语音发送\n"
            "点歌消息 <歌名>　以文本链接发送\n"
            "直链 <ID/链接>　按 ID 或分享链接获取直链\n"
            "点歌模式 [模式]　查看/切换默认发送方式\n"
            "帮助　　　　　　查看本帮助\n"
            "══════════════════\n"
            "🌐 发现音乐\n"
            "歌词 <歌名|ID>　查看歌词\n"
            "排行 [榜单名]　官方排行榜\n"
            "歌手 <名字>　　热门歌曲\n"
            "专辑 <名字>　　专辑曲目\n"
            "歌单 <关键词>　歌单曲目\n"
            "评论 <歌名|ID>　歌曲热评\n"
            "新歌 [地区]　　新歌速递(华语/欧美/日本/韩国)\n"
            "来首歌　　　　　随机来一首\n"
            "连播 [数量]　　连播当前列表(语音,≤10)\n"
            "（上面这些列表都能直接回复序号点歌）\n"
            "══════════════════\n"
            "💡 示例\n"
            "点歌 咏春\n"
            "点歌文件 咏春\n"
            "点歌语音 咏春\n"
            "直链 1498523311\n"
            "点歌模式 文件\n"
            "歌词 晴天\n"
            "排行\n"
            "歌手 周杰伦\n"
            "评论 晴天\n"
            "══════════════════\n"
            f"⚙️ 默认发送：{SEND_MODE_CN.get(mode, mode)}"
            + ("（卡片被拒自动回退）\n" if mode == "card" else "\n")
            + f"🕒 列表 {self.retract_seconds} 秒自动撤回｜免前缀：{'开启' if not self.require_prefix else '关闭'}\n"
            "══════════════════\n"
            "👥 作者\n"
            "流水 · 听雨的蛙 · 落雪 · GLM-5.3-Flash"
        )
        r = await self._say(event, tips)
        if r is not None:
            yield r

    @filter.command("歌词")
    async def lyrics_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """歌词 <歌名|ID>：获取网易云歌词"""
        async for r in self._lyrics_flow(event, str(keyword or "").strip()):
            yield r

    @filter.command("排行")
    async def toplist_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """排行 [榜单名]：官方排行榜列表或曲目"""
        async for r in self._toplist_flow(event, str(keyword or "").strip()):
            yield r

    @filter.command("歌手")
    async def artist_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """歌手 <名字>：歌手热门歌曲"""
        async for r in self._artist_flow(event, str(keyword or "").strip()):
            yield r

    @filter.command("专辑")
    async def album_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """专辑 <名字>：专辑曲目"""
        async for r in self._album_flow(event, str(keyword or "").strip()):
            yield r

    @filter.command("歌单")
    async def playlist_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """歌单 <关键词>：歌单曲目"""
        async for r in self._playlist_flow(event, str(keyword or "").strip()):
            yield r

    @filter.command("评论")
    async def comments_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """评论 <歌名|ID>：歌曲热评"""
        async for r in self._comments_flow(event, str(keyword or "").strip()):
            yield r

    @filter.command("新歌")
    async def new_songs_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """新歌 [地区]：新歌速递（华语/欧美/日本/韩国）"""
        async for r in self._new_songs_flow(event, str(keyword or "").strip()):
            yield r

    @filter.command("来首歌")
    async def random_cmd(self, event: AstrMessageEvent):
        """来首歌：随机来一首"""
        async for r in self._random_flow(event):
            yield r

    @filter.command("连播")
    async def serial_cmd(self, event: AstrMessageEvent, keyword: GreedyStr):
        """连播 [数量]：连播当前点歌列表（语音，最多 10 首）"""
        async for r in self._serial_flow(event, str(keyword or "").strip()):
            yield r

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_prefix_free(self, event: AstrMessageEvent):
        """免唤醒前缀触发：直接发「点歌 xx」「直链 xx」「点歌模式 xx」「帮助」即可使用。

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
        elif text.startswith("直链"):
            async for r in self._unlock_flow(event, text[len("直链"):].strip()):
                yield r
        elif text.startswith("歌词"):
            async for r in self._lyrics_flow(event, text[len("歌词"):].strip()):
                yield r
        elif text.startswith("排行"):
            async for r in self._toplist_flow(event, text[len("排行"):].strip()):
                yield r
        elif text.startswith("歌手"):
            async for r in self._artist_flow(event, text[len("歌手"):].strip()):
                yield r
        elif text.startswith("专辑"):
            async for r in self._album_flow(event, text[len("专辑"):].strip()):
                yield r
        elif text.startswith("歌单"):
            async for r in self._playlist_flow(event, text[len("歌单"):].strip()):
                yield r
        elif text.startswith("评论"):
            async for r in self._comments_flow(event, text[len("评论"):].strip()):
                yield r
        elif text.startswith("新歌"):
            async for r in self._new_songs_flow(event, text[len("新歌"):].strip()):
                yield r
        elif text == "来首歌":
            async for r in self._random_flow(event):
                yield r
        elif text.startswith("连播"):
            async for r in self._serial_flow(event, text[len("连播"):].strip()):
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
            r = await self._say(event, "❌ 选歌超时，请重新点歌")
            if r is not None:
                yield r
            event.stop_event()
            return

        idx = int(text)
        if idx == 0:
            self._pending.pop(key, None)
            r = await self._say(event, "已取消选歌")
            if r is not None:
                yield r
            event.stop_event()
            return

        # 「排行」列表：数字选的是榜单，进去看曲目
        if cache.get("stage") == "pick_board":
            boards = cache.get("boards") or []
            if not (1 <= idx <= len(boards)):
                return  # 超出范围的数字视为普通聊天，不拦截
            self._pending.pop(key, None)
            await self._react(event)
            async for r in self._toplist_flow(event, boards[idx - 1].get("name") or ""):
                yield r
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
