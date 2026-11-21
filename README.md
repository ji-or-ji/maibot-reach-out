# 麦麦主动私聊 Pro

让麦麦主动找人说话，并且知道自己为什么来。

和「带话插件」的分界线：带话是替别人转达，接收方一眼能看出是传话；这里是麦麦自己上门，带着具体由头开口。

## 两个工具

### `reach_out` — 受人所托

有人明确要它联系谁时用。比如「你去问问阿岚答辩怎么样」。

参数写清为什么找他、当时的原话是什么，开场才不像模板。

### `share_thought` — 走神

聊天时某句话把它带到别处，忽然想起某个人，于是切出去发一条。没人托它，是她自己想到的。

这是「忽然想到」和「执行任务」的区别：前者没有任务感，开口更随意。

## 生成的差别

泛泛地找话题会得到「最近怎么样呀」。带上由头之后：

```
他下午在群里说答辩被老师当场骂了，说可能延毕
→ 你那答辩后来咋样了，没真延毕吧？

他说周末搬家，群里没人接话，自己说算了找搬家公司
→ 搬家公司周末不一定排得开，喊我搭把手呗，中午管顿饭就行

上周私聊说考证没过，差三分，之后再没提
→ 上次差三分那事，后来有再考吗？
```

走神的语气又不同：

```
群里吹牛说整合包随便配，心里没底
→ 季啊，之前群里我说整合包随便配，其实有点吹了，别当真哈

群里有人问考证，想起他上次差三分
→ 那个证后来还考没，差三分怪可惜的
```

关键在它知道该提什么。「差三分」是当作 context 传进去的，不提这个，对方会觉得只是例行关心。

## 做法

生成前会做四件事：

1. 读一遍和这个人的私聊历史，让语气接得上
2. 把缘由和原话拼进提示词
3. 明确禁止「有人让我」「某某托我」「带话」「转达」这类字样
4. 禁止编造对方没说过的话

生成后清洗：去引号、去自报家门前缀、压掉换行。超长会重写一次，还超就用原句，不硬截断。

## 配置

复制 `config.example.toml` 为 `config.toml`（该文件被 gitignore，不会进版本控制）。

### 频控

两个工具各有一套，都能直接改：

```toml
[limits]                       # reach_out
daily_per_target = 2           # 对同一个人每天最多几次
min_interval_minutes = 180     # 对同一人两次之间至少隔多久

[thought]                      # share_thought
daily_limit = 2
min_interval_minutes = 120
```

另外还有时段窗口（`active_start` / `active_end`，支持跨夜）和全局间隔（`global_cooldown_seconds`）。

### 名单

```toml
[scope]
requester_whitelist = []   # 谁能叫它私聊别人，留空不限
target_whitelist = []      # 只允许联系谁，留空不限
target_blacklist = []      # 永远不联系谁，优先级最高
```

公开场合建议把 `requester_whitelist` 填成自己，否则任何人都能让麦麦给任意好友发消息。

### 口吻

```toml
[style]
max_chars = 48
temperature = 1.05
model_slot = "replyer"
use_private_history = true
history_limit = 8
allow_mention_source = true   # 是否允许说「刚在群里看到」
```

`dry_run = true` 时只生成不发送，可以先看看口吻对不对再放开。

## 名字匹配

支持模糊匹配，不要求写全名：

- 剥离 emoji 与符号：`🐾小满OwO🐾` 用「小满」就能找到
- 繁简归一：`阿魚` 用「阿鱼」能找到
- 括号备注不影响：`阿锦(周末勿扰🤓)` 用「阿锦」能找到
- 打分排序：完全相同 > 开头吻合 > 中间包含
- 有歧义时不瞎猜，会回一句「有几个都像「小鱼」：xxx、yyy，给个 QQ 号或说全一点」

## 防滥用

三层拦截，按顺序：

1. 触发者白名单：不在名单里的人调不动
2. 目标黑白名单：黑名单优先于白名单
3. 频控：每人每日上限、对同一人最小间隔、全局间隔、时段窗口

触发者跟目标相同时直接拒绝——那种情况在原地回就行。

## 能力

`send.text`、`chat.get_private_streams`、`chat.get_group_streams`、`chat.get_stream_by_user_id`、`chat.open_session`、`api.call`、`message.get_recent`、`config.get`、`llm.generate`

## 数据

频次统计写在 `data/plugins/maibot-reach-out/stats.json`，按日期分桶，保留十天。只记每人对某人的发送时间戳，不记内容。

## 安装

放在麦麦的 `plugins/` 目录下，重启或在 WebUI 里启用。`config.toml` 不存在时会按默认值生成。

## 许可

MIT
