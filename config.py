"""主动私聊 Pro — 配置模型"""

from pydantic import Field

from maibot_sdk.config import PluginConfigBase


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class ScopeConfig(PluginConfigBase):
    """谁能用、能找谁。"""

    __ui_label__ = "范围"
    __ui_icon__ = "shield"
    __ui_order__ = 1

    requester_whitelist: list[str] = Field(
        default_factory=list,
        description="允许触发主动私聊的用户 QQ。留空表示不限制。",
        json_schema_extra={"label": "触发者白名单", "hint": "只有名单里的人能让你去私聊别人。留空不限制。"},
    )
    target_whitelist: list[str] = Field(
        default_factory=list,
        description="允许被私聊的对象 QQ。留空表示不限制。",
        json_schema_extra={"label": "目标白名单", "hint": "留空则除黑名单外都能找。"},
    )
    target_blacklist: list[str] = Field(
        default_factory=list,
        description="永远不主动私聊的 QQ。优先级高于白名单。",
        json_schema_extra={"label": "目标黑名单", "hint": "不管谁要求、什么理由都不找这些人。"},
    )
    max_group_scan: int = Field(
        default=8,
        description="名字匹配时最多扫几个群找成员，从最活跃的群开始",
        json_schema_extra={"label": "扫描群数上限", "min": 1, "max": 30},
    )


class LimitsConfig(PluginConfigBase):
    """频次与时段。防止把人烦到。"""

    __ui_label__ = "频控"
    __ui_icon__ = "clock"
    __ui_order__ = 2

    daily_per_target: int = Field(
        default=2,
        description="对同一个人每天最多主动私聊几次",
        json_schema_extra={"label": "每人每日上限", "min": 1, "max": 20},
    )
    min_interval_minutes: int = Field(
        default=180,
        description="对同一个人两次主动私聊之间的最小间隔（分钟）",
        json_schema_extra={"label": "最小间隔（分钟）", "min": 0, "max": 1440},
    )
    active_start: str = Field(
        default="08:00",
        description="允许主动私聊的起始时间（HH:MM）",
        json_schema_extra={"label": "起始时间", "placeholder": "08:00"},
    )
    active_end: str = Field(
        default="23:30",
        description="允许主动私聊的结束时间（HH:MM），支持跨夜",
        json_schema_extra={"label": "结束时间", "placeholder": "23:30"},
    )
    global_cooldown_seconds: int = Field(
        default=45,
        description="两次主动私聊之间的全局间隔（秒），避免同一时刻连着找好几个人",
        json_schema_extra={"label": "全局间隔（秒）", "min": 0, "max": 1800},
    )


class StyleConfig(PluginConfigBase):
    """说话方式。这块决定像不像人。"""

    __ui_label__ = "口吻"
    __ui_icon__ = "type"
    __ui_order__ = 3

    max_chars: int = Field(
        default=48,
        description="生成内容的字数上限，超过会被要求重写",
        json_schema_extra={"label": "字数上限", "min": 12, "max": 200},
    )
    temperature: float = Field(
        default=1.05,
        description="生成温度，越高越松弛",
        json_schema_extra={"label": "生成温度", "min": 0.2, "max": 1.6, "step": 0.05},
    )
    model_slot: str = Field(
        default="replyer",
        description="使用哪个模型槽位生成（replyer/utils/planner）",
        json_schema_extra={"label": "模型槽位", "placeholder": "replyer"},
    )
    use_private_history: bool = Field(
        default=True,
        description="生成前读取与该对象的私聊历史，让语气接得上",
        json_schema_extra={"label": "读取私聊历史"},
    )
    history_limit: int = Field(
        default=8,
        description="读取多少条历史消息作为参考",
        json_schema_extra={"label": "历史条数", "min": 0, "max": 30},
    )
    allow_mention_source: bool = Field(
        default=True,
        description="允许说「刚在群里看到你…」这类交代出处的话；关掉则完全不带来源",
        json_schema_extra={"label": "允许提及来源"},
    )
    send_delay_seconds: float = Field(
        default=1.2,
        description="发送前的停顿，模拟打字",
        json_schema_extra={"label": "发送延迟（秒）", "min": 0, "max": 10, "step": 0.2},
    )


class BehaviorConfig(PluginConfigBase):
    __ui_label__ = "行为"
    __ui_icon__ = "send"
    __ui_order__ = 4

    platform: str = Field(default="qq", description="消息平台标识")
    dry_run: bool = Field(
        default=False,
        description="只生成不发送，用来试口吻",
        json_schema_extra={"label": "试运行（不发送）"},
    )
    ack_enabled: bool = Field(
        default=True,
        description="发送后给触发者回一句确认",
        json_schema_extra={"label": "回执"},
    )
    dedup_window_seconds: int = Field(
        default=120,
        description="同一触发者同一目标的去重窗口（秒）",
        json_schema_extra={"label": "去重窗口（秒）", "min": 0, "max": 3600},
    )


class ThoughtConfig(PluginConfigBase):
    """走神：忽然想到某人就说一句。这块单独控频，比受人所托更严。"""

    __ui_label__ = "走神"
    __ui_icon__ = "sparkles"
    __ui_order__ = 5

    enabled: bool = Field(
        default=True,
        description="是否允许它自己忽然想到某人就开口",
        json_schema_extra={"label": "启用走神"},
    )
    daily_limit: int = Field(
        default=2,
        description="对同一个人每天最多几次走神",
        json_schema_extra={"label": "每人每日上限", "min": 1, "max": 10},
    )
    min_interval_minutes: int = Field(
        default=120,
        description="对同一人两次走神的最小间隔（分钟）",
        json_schema_extra={"label": "最小间隔（分钟）", "min": 0, "max": 1440},
    )


class ReachOutConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    style: StyleConfig = Field(default_factory=StyleConfig)
    behavior: BehaviorConfig = Field(default_factory=BehaviorConfig)
    thought: ThoughtConfig = Field(default_factory=ThoughtConfig)


def create_config():
    return ReachOutConfig()
