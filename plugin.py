"""主动私聊 Pro — Maibot Reach Out Plugin

和「带话插件」的区别：传话是替别人说话，接收方一眼能看出是转达；
这里是麦麦自己找上门，而且知道自己为什么来。

核心在这个工具的三个入参：谁、为什么、当时说了什么。
缘由和细节给得越具体，开场越像人。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from maibot_sdk import MaiBotPlugin
from maibot_sdk.components import Tool, ToolParameterInfo
from maibot_sdk.types import ToolParamType

from .config import ReachOutConfig

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_FALLBACK_DATA_DIR = os.path.join(_ROOT, "data", "plugins", "maibot-reach-out")

TONE_HINTS = {
    "care": "他最近状态不太好。语气放轻，问一句就够，不要追问，不要给建议，也不要说「能有多大事」「没事的」这类轻描淡写的话。",
    "casual": "随口一提，轻松，不给对方压力。",
    "followup": "接着之前没聊完的那件事，让对方知道你记得。",
    "invite": "叫他一起做点什么，具体点，别泛泛地问有没有空。",
}

STALE_DAYS = 10
MEMBER_CACHE_TTL = 300

# 常见繁体字，群友名字里出现频率高的那批
_T2S = str.maketrans(
    "魚閒圓蘭風飛雲龍鳳貓鳥馬來時個們這說會點開關學習愛樂書畫車門東見語認識還進遠邊過裡後",
    "鱼闲圆兰风飞云龙凤猫鸟马来时个们这说会点开关学习爱乐书画车门东见语认识还进远边过里后",
)


def _norm(text: str) -> str:
    """归一化：去掉 emoji、符号、空格，统一繁简与大小写。"""
    raw = str(text or "")
    out = []
    for ch in raw:
        code = ord(ch)
        # 跳过 emoji 与各类符号、变体选择符
        if 0x1F000 <= code <= 0x1FAFF:
            continue
        if 0x2600 <= code <= 0x27BF or 0xFE00 <= code <= 0xFE0F or code == 0x200D:
            continue
        out.append(ch)
    text = "".join(out).lower().translate(_T2S)
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _score(name: str, needle: str) -> int:
    """名字与查询词的契合度。0 表示不沾边。"""
    n, m = _norm(name), _norm(needle)
    if not n or not m:
        return 0
    if n == m:
        return 100
    if n.startswith(m):
        return 85
    if m in n:
        return 70
    if n in m and len(n) >= 2:
        return 45
    return 0


def _parse_hhmm(value: str, fallback: dtime) -> dtime:
    try:
        h, m = value.strip().split(":")
        return dtime(int(h), int(m))
    except Exception:
        return fallback


def _in_window(now: dtime, start: dtime, end: dtime) -> bool:
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def _unwrap_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("data", "messages", "result", "items"):
            inner = value.get(key)
            if isinstance(inner, list):
                return inner
    return []


def _extract_text(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for key in ("content", "text", "reply", "message"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        data = result.get("data")
        if isinstance(data, (str, dict)):
            return _extract_text(data)
    return ""


def _clean(text: str) -> str:
    text = (text or "").strip()
    for quote in ('"', "'", "「", "」", "『", "』", "“", "”", "‘", "’", "`"):
        text = text.strip(quote)
    text = text.replace("\n", " ").strip()
    # 去掉常见的自报家门前缀
    for prefix in ("御风：", "御风:", "麦麦：", "麦麦:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


class ReachOutPlugin(MaiBotPlugin):
    config_model = ReachOutConfig

    def __init__(self) -> None:
        super().__init__()
        self._stats: dict[str, dict[str, list[float]]] = {}
        self._bot_qq = ""
        self._last_global = 0.0
        self._last_pair: dict[tuple[str, str, str], float] = {}
        self._member_cache: dict[str, tuple[float, list[dict]]] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    async def on_load(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.info("[reach-out] 已在配置中禁用，跳过")
            return
        self._stats_path().parent.mkdir(parents=True, exist_ok=True)
        self._load_stats()
        try:
            self._bot_qq = str(await self.ctx.config.get("bot.qq_account", "") or "")
        except Exception:
            self._bot_qq = ""
        self.ctx.logger.info("[reach-out] 已加载，触发者白名单 %d 人", len(self.config.scope.requester_whitelist))

    async def on_unload(self) -> None:
        self._save_stats()
        self.ctx.logger.info("[reach-out] 已卸载")

    async def on_config_update(self, *args, **kwargs) -> None:
        pass

    # ── 统计文件 ──────────────────────────────────────────────

    def _stats_path(self) -> Path:
        """统计文件路径。优先用宿主分配的数据目录，不往插件目录里写。"""
        try:
            return Path(self.ctx.paths.data_dir) / "stats.json"
        except Exception:
            return Path(_FALLBACK_DATA_DIR) / "stats.json"

    def _load_stats(self) -> None:
        path = self._stats_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._stats = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            self._stats = {}
        self._prune_stats()

    def _save_stats(self) -> None:
        path = self._stats_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._stats, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as exc:
            self.ctx.logger.warning("[reach-out] 写入统计失败: %s", exc)

    def _prune_stats(self) -> None:
        keep = {
            datetime.now().strftime("%Y-%m-%d")
        }
        cutoff = time.time() - STALE_DAYS * 86400
        trimmed: dict[str, dict[str, list[float]]] = {}
        for day, per_target in self._stats.items():
            try:
                ts = datetime.strptime(day, "%Y-%m-%d").timestamp()
            except Exception:
                continue
            if ts < cutoff and day not in keep:
                continue
            fixed: dict[str, list[float]] = {}
            for uid, stamps in per_target.items():
                if isinstance(stamps, list):
                    fixed[str(uid)] = [float(s) for s in stamps if isinstance(s, (int, float))]
            trimmed[day] = fixed
        self._stats = trimmed

    # ── 工具 ──────────────────────────────────────────────────

    @Tool(
        "reach_out",
        description="受人所托，主动私聊某个人",
        brief_description="受人所托，带着由头私聊某人",
        detailed_description=(
            "当有人明确要你联系某个人时调用，比如「你去问问阿岚」「帮我跟他说一声」。"
            "occasion 写清为什么找他，context 填当时的原话或细节，这两项决定开场像不像人。"
            "不要把它当聊天中的顺手动作。如果你只是自己忽然想起某个人，用 share_thought。"
            "同一个人短时间内重复调用会被拦截。"
        ),
        parameters=[
            ToolParameterInfo(
                name="target_person",
                param_type=ToolParamType.STRING,
                description="目标的名字或 QQ 号",
                required=True,
            ),
            ToolParameterInfo(
                name="occasion",
                param_type=ToolParamType.STRING,
                description="为什么找他，一句话讲清缘由",
                required=True,
            ),
            ToolParameterInfo(
                name="context",
                param_type=ToolParamType.STRING,
                description="当时的原话或具体细节，可空",
                required=False,
            ),
            ToolParameterInfo(
                name="tone",
                param_type=ToolParamType.STRING,
                description="care / casual / followup / invite，默认 casual",
                required=False,
            ),
            ToolParameterInfo(
                name="dry_run",
                param_type=ToolParamType.BOOLEAN,
                description="只生成不发送，用于试口吻",
                required=False,
            ),
        ],
    )
    async def reach_out(
        self,
        target_person: str,
        occasion: str,
        context: str = "",
        tone: str = "casual",
        dry_run: bool | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        return await self._deliver(
            kind="reach",
            target_person=target_person,
            occasion=occasion,
            context=context,
            tone=tone,
            dry_run=dry_run,
            **kwargs,
        )

    @Tool(
        "share_thought",
        description="忽然想到某个人，顺手跟他说一句",
        brief_description="走神：忽然想到某人，去说一句",
        detailed_description=(
            "聊天时偶尔会这样：正说着话，某句话把你带到别处去，你想起某个人的近况，"
            "或者觉得某件事该让他知道，于是切出去发一条。没人托你，是你自己想到的。\n"
            "什么时候用：群聊里的话题让你联想到某个人；你刚说出口的话其实更想跟某个人讲；"
            "看到一件小事觉得某人会感兴趣；某人之前提过的事有了下文。\n"
            "什么时候别用：有人明确要你联系谁（那是 reach_out）、正经事等对方回话、"
            "只是想刷存在感、没有具体由头只想找人聊天。\n"
            "thought 写你想到的那件事，context 写是哪句话让你想到的，两者会直接进入开场。"
            "频率有限制，一天最多几次，被拦住时会告诉你原因。"
        ),
        parameters=[
            ToolParameterInfo(
                name="target_person",
                param_type=ToolParamType.STRING,
                description="想到的那个人，名字或 QQ 号",
                required=True,
            ),
            ToolParameterInfo(
                name="thought",
                param_type=ToolParamType.STRING,
                description="你想到的那件事，一句话",
                required=True,
            ),
            ToolParameterInfo(
                name="context",
                param_type=ToolParamType.STRING,
                description="是哪句话、哪件事让你想到的，可空",
                required=False,
            ),
            ToolParameterInfo(
                name="dry_run",
                param_type=ToolParamType.BOOLEAN,
                description="只生成不发送",
                required=False,
            ),
        ],
    )
    async def share_thought(
        self,
        target_person: str,
        thought: str,
        context: str = "",
        dry_run: bool | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        return await self._deliver(
            kind="thought",
            target_person=target_person,
            occasion=thought,
            context=context,
            tone="casual",
            dry_run=dry_run,
            **kwargs,
        )

    async def _deliver(
        self,
        *,
        kind: str,
        target_person: str,
        occasion: str,
        context: str,
        tone: str,
        dry_run: bool | None,
        **kwargs,
    ) -> dict[str, Any]:
        logger = self.ctx.logger
        cfg = self.config
        tag = "reach-out" if kind == "reach" else "reach-thought"

        if not cfg.plugin.enabled:
            return {"success": False, "reason": "插件已禁用"}
        if kind == "thought" and not cfg.thought.enabled:
            return {"success": False, "reason": "走神这个功能没开"}

        requester = str(kwargs.get("user_id") or "").strip()
        reply_stream = str(kwargs.get("stream_id") or "").strip()

        if cfg.scope.requester_whitelist and requester not in cfg.scope.requester_whitelist:
            logger.info("[%s] 拒绝：%s 不在触发者白名单", tag, requester)
            return {"success": False, "reason": "你不在允许使用这个功能的名单里"}

        target_uid, candidates = await self._resolve_target(target_person)
        if not target_uid:
            if candidates:
                listed = "、".join("%s(%s)" % (name, uid) for uid, name in candidates)
                return {
                    "success": False,
                    "reason": "有几个都像「%s」：%s。给个 QQ 号或说全一点" % (target_person, listed),
                    "candidates": [{"user_id": uid, "name": name} for uid, name in candidates],
                }
            return {"success": False, "reason": "找不到这个人，请直接给 QQ 号"}

        if self._bot_qq and target_uid == self._bot_qq:
            return {"success": False, "reason": "那是你自己"}
        if requester and target_uid == requester:
            return {"success": False, "reason": "对方就在眼前，直接说就行，不用切私聊"}

        if target_uid in cfg.scope.target_blacklist:
            logger.info("[%s] 拒绝：%s 在黑名单", tag, target_uid)
            return {"success": False, "reason": "这个人不能主动找他"}

        if cfg.scope.target_whitelist and target_uid not in cfg.scope.target_whitelist:
            return {"success": False, "reason": "这个人不在允许主动联系的名单里"}

        blocked = self._check_limits(target_uid, kind)
        if blocked:
            logger.info("[%s] 频控拦截 %s：%s", tag, target_uid, blocked)
            return {"success": False, "reason": blocked}

        if self._is_dup(requester, target_uid, kind):
            return {"success": False, "reason": "刚刚已经找过这个人了"}

        stream_id = await self._private_stream(target_uid)
        if not stream_id:
            return {"success": False, "reason": "无法打开与对方的私聊，可能不是好友"}

        target_name = await self._display_name(target_uid, target_person)
        history = await self._history(stream_id) if cfg.style.use_private_history else ""

        text = await self._compose(
            name=target_name,
            occasion=occasion,
            context=context,
            tone=tone,
            history=history,
            spontaneous=(kind == "thought"),
        )
        if not text:
            return {"success": False, "reason": "文案没生成出来，换个说法再试"}

        preview = dry_run if dry_run is not None else cfg.behavior.dry_run
        if preview:
            logger.info("[%s] 试运行 → %s(%s): %s", tag, target_name, target_uid, text)
            return {"success": True, "dry_run": True, "target": target_name, "text": text}

        if cfg.style.send_delay_seconds > 0:
            await asyncio.sleep(cfg.style.send_delay_seconds)

        try:
            await self.ctx.send.text(text, stream_id)
        except Exception as exc:
            logger.error("[%s] 发送失败 → %s: %s", tag, target_uid, exc)
            return {"success": False, "reason": "消息没发出去"}

        self._mark(target_uid, requester, kind)
        logger.info("[%s] 已私聊 %s(%s)：%s", tag, target_name, target_uid, text)

        if cfg.behavior.ack_enabled and reply_stream and reply_stream != stream_id:
            try:
                await self.ctx.send.text(
                    "我去跟他说两句" if kind == "reach" else "刚想起点事，跟他说一声",
                    reply_stream,
                )
            except Exception:
                pass

        return {"success": True, "target": target_name, "text": text}

    # ── 解析目标 ──────────────────────────────────────────────

    async def _resolve_target(self, person: str) -> tuple[str | None, list[tuple[str, str]]]:
        """返回 (QQ号, 歧义候选)。候选非空说明没法定下来，要问一句。"""
        person = (person or "").strip()
        if not person:
            return None, []
        if person.isdigit():
            return person, []

        scored: dict[str, int] = {}
        names: dict[str, str] = {}

        for uid, name, score in await self._scan_private(person):
            if score > scored.get(uid, 0):
                scored[uid] = score
                names[uid] = name

        if scored:
            top_uid, top = max(scored.items(), key=lambda kv: kv[1])
            if top >= 85 and sum(1 for v in scored.values() if v == top) == 1:
                return top_uid, []

        for uid, name, score in await self._scan_groups(person):
            if score > scored.get(uid, 0):
                scored[uid] = score
                names[uid] = name

        if not scored:
            return None, []

        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
        if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
            return None, [(uid, names.get(uid, uid)) for uid, _ in ranked[:3]]
        return ranked[0][0], []

    async def _scan_private(self, person: str) -> list[tuple[str, str, int]]:
        try:
            streams = await self.ctx.chat.get_private_streams()
        except Exception:
            return []
        hits: list[tuple[str, str, int]] = []
        for stream in streams or []:
            if not isinstance(stream, dict):
                continue
            uid = str(stream.get("user_id", "") or "")
            if not uid.isdigit():
                continue
            best, best_name = 0, ""
            for key in ("user_nickname", "user_cardname", "name", "nickname", "display_name"):
                label = str(stream.get(key, "") or "")
                if not label:
                    continue
                value = _score(label, person)
                if value > best:
                    best, best_name = value, label
            if best:
                hits.append((uid, best_name or uid, best))
        return hits

    async def _scan_groups(self, person: str) -> list[tuple[str, str, int]]:
        limit = max(1, int(self.config.scope.max_group_scan))
        try:
            groups = await self.ctx.chat.get_group_streams()
        except Exception:
            return []
        hits: list[tuple[str, str, int]] = []
        for group in (groups or [])[:limit]:
            if not isinstance(group, dict):
                continue
            gid = str(group.get("group_id", "") or "")
            if not gid.isdigit():
                continue
            for member in await self._members(gid):
                uid = str(member.get("user_id", "") or "")
                if not uid.isdigit():
                    continue
                best, best_name = 0, ""
                for key in ("card", "nickname", "display_name", "name", "remark"):
                    label = str(member.get(key, "") or "")
                    if not label:
                        continue
                    value = _score(label, person)
                    if value > best:
                        best, best_name = value, label
                if best:
                    hits.append((uid, best_name or uid, best))
        return hits

    async def _members(self, gid: str) -> list[dict]:
        now = time.time()
        cached = self._member_cache.get(gid)
        if cached and now - cached[0] < MEMBER_CACHE_TTL:
            return cached[1]
        try:
            raw = await self.ctx.api.call(
                "adapter.napcat.group.get_group_member_list", group_id=int(gid)
            )
        except Exception:
            return cached[1] if cached else []
        if isinstance(raw, dict):
            raw = raw.get("data") or raw.get("members") or []
        members = [m for m in (raw or []) if isinstance(m, dict)]
        self._member_cache[gid] = (now, members)
        return members

    async def _display_name(self, uid: str, fallback: str = "") -> str:
        try:
            info = await self.ctx.api.call(
                "adapter.napcat.account.get_stranger_info", user_id=int(uid), no_cache=False
            )
            if isinstance(info, dict):
                nick = str(info.get("nickname") or info.get("nick") or "").strip()
                if nick:
                    return nick
        except Exception:
            pass
        return fallback or uid

    async def _private_stream(self, uid: str) -> str | None:
        platform = self.config.behavior.platform
        try:
            stream = await self.ctx.chat.get_stream_by_user_id(uid, platform=platform)
            if stream:
                sid = stream.get("stream_id") or stream.get("id")
                if sid:
                    return str(sid)
        except Exception:
            pass
        try:
            session = await self.ctx.chat.open_session(platform=platform, chat_type="private", user_id=uid)
            if session:
                sid = session.get("stream_id") or session.get("id")
                if sid:
                    return str(sid)
        except Exception:
            pass
        return None

    # ── 历史 ──────────────────────────────────────────────────

    async def _history(self, stream_id: str) -> str:
        limit = self.config.style.history_limit
        if limit <= 0:
            return ""
        try:
            raw = await self.ctx.message.get_recent(stream_id, limit=limit)
        except Exception:
            return ""
        lines: list[str] = []
        for msg in _unwrap_list(raw):
            if not isinstance(msg, dict):
                continue
            info = msg.get("message_info") or {}
            user = info.get("user_info") or {}
            uid = str(user.get("user_id", "") or "")
            nick = str(user.get("user_nickname") or user.get("user_cardname") or "").strip()
            body = str(msg.get("processed_plain_text") or msg.get("plain_text") or "").strip()
            if not body:
                continue
            who = "我" if (self._bot_qq and uid == self._bot_qq) else (nick or "他")
            lines.append("%s：%s" % (who, body[:80]))
        return "\n".join(lines[-limit:])

    # ── 文案 ──────────────────────────────────────────────────

    async def _persona(self) -> str:
        try:
            value = await self.ctx.config.get("personality.personality", "")
            if isinstance(value, dict):
                value = value.get("value", "")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except Exception:
            pass
        return "你是一个在 QQ 上跟群友聊天的年轻人，说话短、口语、有自己的判断。"

    async def _compose(
        self, *, name: str, occasion: str, context: str, tone: str, history: str,
        spontaneous: bool = False,
    ) -> str:
        cfg = self.config.style
        persona = await self._persona()

        rules = [
            "你在 QQ 上私聊对方，是你自己想找他说，不是替任何人转达。",
            "像朋友忽然想起他，随手发一条，不是在执行任务。",
            "开口就说事，不寒暄，不用「在吗」「打扰了」「好久不见」「最近怎么样」。",
            "不出现「有人让我」「某某托我」「带话」「转达」「帮你问」这类词。",
            "不解释你为什么发这条，不写旁白、不加括号。",
            "只用上面给出的信息。不要编造对方没说过的话、没做过的事。",
            "不超过 %d 个字，一到两句，口语，可以有语气词。" % cfg.max_chars,
            "只输出要发送的那句话本身。",
        ]
        if not cfg.allow_mention_source:
            rules.append("不要提「在群里看到」「刚才听你说」之类交代出处的话。")
        if spontaneous:
            rules.append("这是你自己忽然想到的，不是谁让你说的，也不是在回应他刚才说的话。")
            rules.append("可以带一点「忽然想起」的随意感，但别直接写「我突然想起」这种句式。")
        hint = TONE_HINTS.get((tone or "casual").strip().lower())
        if hint:
            rules.append(hint)

        blocks = ["对方：%s" % name, "为什么现在找他：%s" % occasion.strip()]
        if context.strip():
            blocks.append("当时的细节：%s" % context.strip())
        if history:
            blocks.append("你们最近的私聊：\n%s" % history)

        task = "\n".join(blocks) + "\n\n写作要求：\n" + "\n".join("- " + r for r in rules)

        prompt = [
            {"role": "system", "content": persona},
            {"role": "user", "content": task},
        ]
        kwargs: dict[str, Any] = {"prompt": prompt, "temperature": cfg.temperature}
        slot = (cfg.model_slot or "").strip()
        if slot:
            kwargs["model"] = slot

        last_text = ""
        for attempt in range(2):
            try:
                result = await self.ctx.llm.generate(**kwargs)
            except Exception as exc:
                self.ctx.logger.warning("[reach-out] 生成失败（第 %d 次）: %s", attempt + 1, exc)
                return last_text
            text = _clean(_extract_text(result))
            if not text:
                continue
            last_text = text
            if len(text) <= int(cfg.max_chars * 1.8):
                return text
        return last_text

    # ── 频控 ──────────────────────────────────────────────────

    def _check_limits(self, uid: str, kind: str = "reach") -> str | None:
        cfg = self.config.limits
        now = time.time()

        if cfg.global_cooldown_seconds > 0 and self._last_global:
            if now - self._last_global < cfg.global_cooldown_seconds:
                return "刚找过人，缓一下"

        start = _parse_hhmm(cfg.active_start, dtime(8, 0))
        end = _parse_hhmm(cfg.active_end, dtime(23, 30))
        if not _in_window(datetime.now().time(), start, end):
            return "这个点不适合找人说话"

        if kind == "thought":
            conf = self.config.thought
            stamps = self._today("t:" + uid)
            if len(stamps) >= conf.daily_limit:
                return "今天想到他的次数用完了"
            if stamps and conf.min_interval_minutes > 0:
                if now - max(stamps) < conf.min_interval_minutes * 60:
                    return "刚刚就想到过他"
            return None

        stamps = self._today(uid)
        if len(stamps) >= cfg.daily_per_target:
            return "今天已经找过他了"
        if stamps and cfg.min_interval_minutes > 0:
            if now - max(stamps) < cfg.min_interval_minutes * 60:
                return "离上次太近了"
        return None

    def _today(self, uid: str) -> list[float]:
        day = datetime.now().strftime("%Y-%m-%d")
        return list(self._stats.get(day, {}).get(uid, []))

    def _mark(self, uid: str, requester: str, kind: str = "reach") -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        key = ("t:" + uid) if kind == "thought" else uid
        self._stats.setdefault(day, {}).setdefault(key, []).append(time.time())
        self._last_global = time.time()
        if requester:
            self._last_pair[(requester, uid, kind)] = time.time()
        self._save_stats()

    def _is_dup(self, requester: str, uid: str, kind: str = "reach") -> bool:
        window = self.config.behavior.dedup_window_seconds
        if window <= 0 or not requester:
            return False
        last = self._last_pair.get((requester, uid, kind))
        return bool(last and time.time() - last < window)


def create_plugin() -> ReachOutPlugin:
    return ReachOutPlugin()
