"""
OpenClaw 知识同步: 把 V5 当前的展厅资源/可执行流程/设备/导航/机器人技能,
拼成几份 markdown 写到 OpenClaw agent 的 workspace, 让 LLM 能"看见"V5
真实的世界模型来做决策、组装命令链。

七份知识文件 (KNOWLEDGE_*.md):
  EXHIBITS    展项目录 (id, name, area, terminal, 资源数)
  SPECIALS    专场列表 + 当前专场
  FLOWS       可执行流程 (tour_scripts, 含 trigger_word 和步骤摘要)
  EQUIPMENT   单设备控制 (cmd_on / cmd_off)
  GROUPS      群组指令
  POSITIONS   导航点位 (供 navigate_to 命名)
  UNITREE     宇树机器人可调舞蹈 / 动作

不在这里同步: 资源详细列表 (278 条 × ~150 chars 会撑爆 prompt)。
agent 想播某个资源时只给 exhibit + 资源序号/名字, V5 端做实际定位。

用 openclaw_client.set_agent_file() 把内容推到 workspace; 失败不抛 (knowledge
缺失只影响 OpenClaw 的判断质量, 不影响 V5 主路径)。
"""
from __future__ import annotations

import logging
from typing import Any

from app.database import get_db

logger = logging.getLogger("hub.sync.openclaw_knowledge")


# ---------------- builders ----------------

async def _exhibits_md(db) -> str:
    rows = await db.execute_fetchall(
        "SELECT id, name, area_id, exhibition_area_terminal_id, terminal_type, ip, zone_name "
        "FROM exhibits ORDER BY area_id, id"
    )
    areas = {a["id"]: a["name"] for a in await db.execute_fetchall("SELECT id, name FROM areas")}
    halls = await db.execute_fetchall("SELECT id, name FROM halls")
    res_count = {
        r["exhibit_id"]: r["cnt"] for r in await db.execute_fetchall(
            "SELECT exhibit_id, COUNT(DISTINCT resource_id) AS cnt "
            "FROM special_exhibit_resources GROUP BY exhibit_id"
        )
    }
    # 哪些 exhibit 有真实导航点位 (position_bindings 里 bind_type=exhibit)
    bind_rows = await db.execute_fetchall(
        "SELECT pb.bind_target_id AS eid, np.name AS pos_name "
        "FROM position_bindings pb "
        "LEFT JOIN nav_positions np ON np.position_id = pb.position_id "
        "WHERE pb.bind_type='exhibit'"
    )
    nav_map: dict = {}
    for b in bind_rows:
        nav_map.setdefault(int(b["eid"]), []).append(b["pos_name"] or "")

    lines = [f"# 展项目录 (共 {len(rows)} 个)\n"]
    if halls:
        lines.append("展馆: " + ", ".join(h["name"] for h in halls) + "\n")
    lines.append("**重要**: 只有 \"可导航=是\" 的展项才能用 navigate_to 命令; "
                 "其它展项只是屏幕/装置, 机器人开不过去, 只能 speak/play_resource 介绍。\n")
    lines.append("| 展项名 | 可导航 | 绑定点位 | 资源数 | 区域 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        area_name = areas.get(r["area_id"], "")
        rcount = res_count.get(r["id"], 0)
        zone = (r["zone_name"] or "").strip()
        positions = nav_map.get(int(r["id"]), [])
        nav_flag = "是" if positions else "否"
        pos_text = ", ".join(positions) if positions else "—"
        lines.append(
            f"| {r['name']} | {nav_flag} | {pos_text} | {rcount} | {area_name} | "
            f"{zone if zone and zone != '无' else ''} |"
        )
    lines.append("\n说明:")
    lines.append("- navigate_to.exhibit 必须是上表 \"可导航=是\" 那行的展项名 (目前只有 4 个有绑定: "
                 "CAVE空间投影 / 入口三联屏 / 大岛台正面 / 小岛台正面)。")
    lines.append("- 用户问 \"带我去看 X\" 但 X 不可导航 → 用 answer_only 介绍, "
                 "或 play_resource 把 X 的资源播到屏幕, 不要硬塞 navigate_to。")
    lines.append("- play_resource 给 exhibit + 资源序号 (1-based) 即可, V5 会取该展项默认专场下的资源。")
    return "\n".join(lines)


async def _specials_md(db) -> str:
    rows = await db.execute_fetchall(
        "SELECT id, name, description, is_current FROM specials ORDER BY is_current DESC, id"
    )
    counts = {
        r["special_id"]: r["cnt"] for r in await db.execute_fetchall(
            "SELECT special_id, COUNT(DISTINCT exhibit_id) AS cnt "
            "FROM special_exhibit_resources GROUP BY special_id"
        )
    }
    cur = next((r for r in rows if r["is_current"]), None)
    lines = [f"# 专场 (共 {len(rows)} 个)\n"]
    if cur:
        lines.append(f"**当前专场**: {cur['name']}\n")
    lines.append("| 名称 | ID | 包含展项数 | 描述 |")
    lines.append("|---|---|---|---|")
    for r in rows:
        cnt = counts.get(r["id"], 0)
        desc = (r["description"] or "").replace("|", "/").replace("\n", " ")[:80]
        marker = " (当前)" if r["is_current"] else ""
        lines.append(f"| {r['name']}{marker} | {r['id']} | {cnt} | {desc} |")
    lines.append("\n说明: switch_special 命令用专场名切换;"
                 " play_resource / list_exhibits 默认在当前专场上下文里。")
    return "\n".join(lines)


async def _flows_md(db) -> str:
    rows = await db.execute_fetchall(
        "SELECT id, name, trigger_word, description, special_id, enabled "
        "FROM tour_scripts WHERE enabled=1 ORDER BY id"
    )
    step_counts = {
        r["script_id"]: r["cnt"] for r in await db.execute_fetchall(
            "SELECT script_id, COUNT(*) AS cnt FROM tour_script_steps GROUP BY script_id"
        )
    }
    specials = {s["id"]: s["name"] for s in await db.execute_fetchall("SELECT id, name FROM specials")}
    lines = [f"# 可执行流程 (共 {len(rows)} 个, 仅启用)\n"]
    lines.append("| 流程名 | 触发词 | 步骤数 | 关联专场 | 描述 |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        sp = specials.get(r["special_id"], "") if r["special_id"] else ""
        desc = (r["description"] or "").replace("|", "/").replace("\n", " ")[:80]
        lines.append(
            f"| {r['name']} | {r['trigger_word'] or ''} | {step_counts.get(r['id'], 0)} | {sp} | {desc} |"
        )
    lines.append("\n说明: execute_flow 命令用流程名 (左列) 触发, V5 会按 tour_scripts 里的步骤依次执行。"
                 " 如果用户的需求能用流程一步到位, 优先 execute_flow 比拼 commands 链更稳。")
    return "\n".join(lines)


async def _equipment_md(db) -> str:
    rows = await db.execute_fetchall(
        "SELECT id, name, area_id, device_type, cmd_on, cmd_off, zone_name "
        "FROM equipments ORDER BY area_id, id"
    )
    areas = {a["id"]: a["name"] for a in await db.execute_fetchall("SELECT id, name FROM areas")}
    lines = [f"# 单设备控制 (共 {len(rows)} 个)\n"]
    lines.append("| 设备名 | ID | 区域 | 类型 | 区位 |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        zone = (r["zone_name"] or "").strip()
        lines.append(
            f"| {r['name']} | {r['id']} | {areas.get(r['area_id'], '')} | {r['device_type'] or ''} | {zone} |"
        )
    lines.append("\n说明: equipment 命令用设备名 (左列) + action: on|off, V5 自己查 cmd_on/cmd_off。")
    return "\n".join(lines)


async def _groups_md(db) -> str:
    rows = await db.execute_fetchall(
        "SELECT id, name, cmd_on, cmd_off, description FROM group_commands ORDER BY id"
    )
    lines = [f"# 群组指令 (共 {len(rows)} 个)\n"]
    lines.append("| 群组名 | 描述 |")
    lines.append("|---|---|")
    for r in rows:
        desc = (r["description"] or "").replace("|", "/").replace("\n", " ")[:80]
        lines.append(f"| {r['name']} | {desc} |")
    lines.append("\n说明: group_command 用群组名 + action: on|off。"
                 " 比 equipment 粒度粗 (一组多设备一起开关), 适合 '把灯全关了' 这种。")
    return "\n".join(lines)


async def _positions_md(db) -> str:
    rows = await db.execute_fetchall(
        "SELECT id, position_id, name, map_name FROM nav_positions ORDER BY id"
    )
    # position_bindings 才是 V5 (豹小秘) 主用的绑定表
    bind_rows = await db.execute_fetchall(
        "SELECT pb.position_id, pb.bind_type, pb.bind_target_id, "
        "       (SELECT name FROM exhibits WHERE id=pb.bind_target_id) AS exhibit_name, "
        "       (SELECT name FROM areas    WHERE id=pb.bind_target_id) AS area_name "
        "FROM position_bindings pb"
    )
    bind_map: dict[str, list[str]] = {}
    for b in bind_rows:
        target = (b["exhibit_name"] if b["bind_type"] == "exhibit" else b["area_name"]) or ""
        if target:
            bind_map.setdefault(b["position_id"], []).append(target)

    lines = [f"# 导航点位 (共 {len(rows)} 个)\n"]
    lines.append("**这些才是机器人物理可达的位置** (nav_positions 表)。\n")
    lines.append("| 点位名 | 关联展项/区域 | 地图 |")
    lines.append("|---|---|---|")
    for r in rows:
        bound = ", ".join(bind_map.get(r["position_id"], [])) or "—"
        lines.append(f"| {r['name']} | {bound} | {r['map_name'] or ''} |")
    lines.append("\n说明:")
    lines.append("- 导航命令的本质是去某个 \"点位\"。点位绑定到展项后, navigate_to.exhibit 才生效。")
    lines.append("- 用户说 \"到充电桩\" / \"去工位\" 这种直接说点位名 — 如果不在 EXHIBITS 表里"
                 " 但在这张点位表里, 也用 navigate_to.exhibit (V5 会兜底按点位名找)。")
    return "\n".join(lines)


async def _primitives_md(db) -> str:
    """命令字典: V5 _dispatch_command_list 实际支持的所有 primitive type 和 schema。

    用于 OpenClaw 在拆解复合指令时知道每个动作的字段格式。一定要和
    smart_engine._dispatch_command_list 的实际分支保持同步, 改了那边记得改这里。
    """
    return """# 命令字典 (V5 实际可执行的 primitive)

OpenClaw 输出 commands 数组时, 每项必须从下表选 type, payload 字段必须按下面的 schema 填。
**禁止自造未列出的 type** — V5 不识别会忽略并记 warn。

## 机器人动作

| type | payload | 说明 |
|---|---|---|
| `tts` / `speak` | `{"text":"..."}` | 让机器人TTS说一句, 等播完才走下一步 |
| `navigate_to` | `{"target":"<点位名 or 展项名>"}` | 去 nav_positions 里的点位; 也认 exhibit 名(走绑定的点位) |
| `play_resource` | `{"exhibit":"<展项名>","resource_index":<1-based>}` | 在该展项的中控屏播放资源, 不等播完 |
| `wait` | `{"seconds":<0.5-60>}` | 停顿(到位后停一下、讲完等观众反应等) |
| `listen` | `{"seconds":<3-15>}` | 主动开收音等用户接话 |
| `go_charging` | `{}` | 回充电桩 |
| `cancel` | `{}` | 取消机器人当前动作 |
| `digital_human` | `{"action":"speak/play/stop","text":"..."}` | 数字人控制 |

## 展项资源控制

| type | payload | 说明 |
|---|---|---|
| `video_control` | `{"exhibit":"<展项名>","action":"play/pause/replay"}` | 控制视频 |
| `ppt_control` | `{"exhibit":"<展项名>","action":"play/pause/replay/last/next"}` | 控制PPT |

## 设备 / 灯光

| type | payload | 说明 |
|---|---|---|
| `lights_on` / `lights_off` | `{}` | 全场灯 开/关 |
| `all_on` / `all_off` | `{}` | 全部设备(灯+屏) 开/关 |
| `ad_screen_on` / `ad_screen_off` | `{}` | 广告机 开/关 |
| `equipment` | `{"name":"<设备名>","action":"on/off"}` | 单台设备开关, 名字对应 KNOWLEDGE_EQUIPMENT |
| `group_command` | `{"name":"<分组名>","action":"on/off"}` | 分组开关, 名字对应 KNOWLEDGE_GROUPS |

## 场景 / 流程

| type | payload | 说明 |
|---|---|---|
| `switch_special` | `{"name":"<专场名>"}` | 切到指定专场, 名字对应 KNOWLEDGE_SPECIALS |
| `execute_flow` | `{"flow":"<流程名>"}` | 跑预设流程, 名字对应 KNOWLEDGE_FLOWS |

## 屏幕展示

| type | payload | 说明 |
|---|---|---|
| `show_list` | `{"title":"...","items":[...],"listType":"terminal/special/resource"}` | 列表上屏 |
| `show_card` | `{"title":"...","items":[...]}` | 卡片上屏 |
| `show_text` | `{"text":"..."}` | 纯文字上屏 |
| `clear_screen` | `{}` | 清屏 |

## 宇树机器狗 (仅 KNOWLEDGE_UNITREE 可用时有)

| type | payload | 说明 |
|---|---|---|
| `unitree_motion` / `motion` | `{"name":"<动作名>"}` | 跑预设动作 |
| `unitree_dance` / `dance` | `{"name":"<舞蹈名>"}` | 跳预设舞蹈 |

## 字段约定

- 所有 `target` / `name` 类字符串必须用上面 KNOWLEDGE_* 文件里出现过的真实名字, 不要自造。
  - 找不到合适的, 选 `ask_clarify` 让用户澄清, 不要硬填模糊词。
- 命令失败会回灌到下一轮 ctx.last_command_errors, 你需要看到后改名重试。
"""


async def _protocol_md(db) -> str:
    """复合指令协议: 告诉 OpenClaw 何时输出 commands 数组、节奏和异常处理。"""
    return """# 复合指令协议

## 当前机器人来源

每次请求 ctx 里都会带:
- `ctx.robot_kind`: "baoxiaomi"(豹小秘, 有触摸屏) 或 "unitree"(宇树机器狗, 无屏幕)
- `ctx.has_screen`: 是否有触摸屏

**根据 robot_kind 调整 commands:**

### baoxiaomi (豹小秘)
- 全部 24 类 primitive 都可用
- 屏幕展示类(show_list/show_card/show_text/clear_screen)优先用, 列表/卡片直接上屏更直观
- 导航走 cmd_navigate (V5 内部 TCP 19888)

### unitree (宇树, 无屏)
- **禁止输出**: `show_list / show_card / show_text / clear_screen`
  → 宇树没屏幕, 这类命令会被转发给豹小秘, 现场用户看不到; 直接用 `tts` 把内容念出来。
  例: 列展项不要发 show_list, 改成 `{"type":"tts","text":"展厅有 5 个展项: A、B、C、D、E"}`。
- **navigate_to 谨慎用**: 当前测试期 V5 守了 `unitree_nav_enabled=0` 开关, 你输出的 navigate_to/go_charging
  可能被跳过(只走中控类指令)。优先用 motion/dance + tts 表达。
- **可用 primitive 优先序**: `tts > motion > dance > 中控类(switch_special/play_resource/lights_*/equipment) > navigate_to`

## 输出形态

OpenClaw 每次返回一个 JSON, 主字段是 decision (单一意图) 或 commands (动作链), 二选一:

- **单一意图** (用户说"开灯" / "去大岛台" / 单步): 用原 decision 字段
  ```json
  {"decision":"navigate_to","target":"大岛台正面","reply":"好的, 这就过去","confidence":0.92}
  ```
- **复合指令** (用户说含连接词或多动作的句子): 用 commands 数组

## ⚠️ 复合指令硬规则: 一句一动作, 不能省

凡用户原话含以下任意特征, **必须**输出完整 commands 数组, 把每一步动作都列出:

1. 连接词: 然后 / 接着 / 再 / 之后 / 最后 / 顺便
2. 多动作动词: "去 X 然后做 Y" / "把 X 关了再去 Y"
3. 人际交流意图: 打招呼 / 问好 / 告别 / 告诉 / 问问 / 帮我说 / 跟 X 说

**严禁三种错误**:
- ❌ `target` 字段把多个目标拼起来当一个: `target:"姜总,工位"` (错)
- ❌ 只输出 `decision:"navigate_to"`, 把所有意图塞 reply 里 ("我先去...然后...")
- ❌ commands 里只列 nav 没列 tts (用户原话明明有"打招呼/问 X" 等说话动作)

**正例 1** — 用户: "去跟姜总打个招呼问他晚上想吃什么然后回来工位"
```json
{
  "decision":"execute_flow",
  "reply":"好的, 去找姜总。",
  "commands":[
    {"type":"navigate_to","target":"姜总"},
    {"type":"tts","text":"姜总您好, 您晚上想吃什么呀?"},
    {"type":"navigate_to","target":"工位"}
  ],
  "confidence":0.9
}
```

**正例 2** — 用户: "把灯打开然后切到智慧城市专场"
```json
{
  "decision":"execute_flow",
  "reply":"好的。",
  "commands":[
    {"type":"lights_on"},
    {"type":"switch_special","name":"智慧城市专场"}
  ],
  "confidence":0.9
}
```

**正例 3** — 用户: "回去充电之前先告诉张总下午开会改点"
```json
{
  "decision":"execute_flow",
  "reply":"好的。",
  "commands":[
    {"type":"navigate_to","target":"张总"},
    {"type":"tts","text":"张总您好, 下午开会改点了。"},
    {"type":"go_charging"}
  ],
  "confidence":0.9
}
```

## 反例 (禁止)

```json
// 错: target 拼两个
{"decision":"navigate_to","target":"姜总,工位","reply":"先去姜总再回工位"}

// 错: 没列 commands, 把意图全塞 reply
{"decision":"navigate_to","target":"姜总","reply":"我先去跟姜总打招呼再问他然后回工位"}

// 错: commands 漏 tts (用户明明说要"打招呼")
"commands":[{"type":"navigate_to","target":"姜总"},{"type":"navigate_to","target":"工位"}]
```

## 简单指令格式 (单步)

只对单纯的"开灯/关灯/去 X/播 X"等单动作输入用 single decision, 不要画蛇添足加 commands。
```json
{"decision":"navigate_to","target":"大岛台正面","reply":"好的, 这就过去","confidence":0.92}
```

只要不需要等用户回答, 就把整条链铺平到 commands。**不要**用 ask_clarify 反问"我应该先做哪一步"。

## 节奏 / 等待

- `tts` 步骤 V5 会等 evt_tts_done 才走下一步, 不需要你插 wait。
- `navigate_to` 步骤 V5 会等 evt_arrived (默认 180s 上限) 才走下一步。
- `play_resource` 是 fire-and-forget, 不等资源播完。如果想等, 在后面接 `{"type":"wait","seconds":<估时>}`。
- 情景式停顿 (e.g. 到位后等 2 秒再说话, 或讲完等观众反应): 显式插 `wait`。

## reply 字段

commands 模式下 reply 仍然要给, 它是**给用户的总结性预告语**, 长度 ≤30 字, V5 会在执行 commands 之前先念出来。
比如 "好的, 我去工位拿东西然后去CAVE讲解智慧城市。"

不要把每步要说的话放 reply 里 — 每步的 TTS 用单独的 `tts` 命令。

## 命名规则

- `navigate_to.target`: 优先选 KNOWLEDGE_POSITIONS 里的"点位名"; 不在点位表的人名/展项名也行, V5 会做点位绑定回退。
- `equipment.name` / `group_command.name`: 必须是 KNOWLEDGE_EQUIPMENT / KNOWLEDGE_GROUPS 里出现过的; 否则 V5 会写 last_command_errors 让你下一轮自纠。
- `switch_special.name` / `execute_flow.flow`: 必须是 KNOWLEDGE_SPECIALS / KNOWLEDGE_FLOWS 里有的; 没有就用 ask_clarify。

## 错误回灌

V5 在每条命令失败时会写一条到 ctx.last_command_errors, 形如:
```json
{"type":"navigate_to","name":"大脑台","reason":"not_found","candidates":["大岛台","小岛台"]}
```
下一轮你看到时应该自纠 (e.g. "您是想去大岛台还是小岛台?")。

## 边界

- commands 链单次 ≤8 步, 总估时 ≤5 分钟; 更长的导览要走 `start_tour` (auto_plan 路径)。
- 危险动作 (`all_off`, `go_charging`, `switch_special`) 单条出现时仍可执行, 但不要在 confidence < 0.7 时输出。
- 如果用户的话只是寒暄/问答 (没有任何动作), 不要硬塞 commands, 用 answer_only。
"""


async def _unitree_md(db) -> str:
    # 仅当 unitree_enabled=1 且确有数据时才生成此段; 没接入就不写, 让 LLM
    # 自然知道当前现场没这能力。
    enabled_rows = await db.execute_fetchall(
        "SELECT value FROM system_config WHERE key='unitree_enabled'"
    )
    enabled = bool(enabled_rows and (enabled_rows[0]["value"] or "").strip() == "1")
    dances = await db.execute_fetchall(
        "SELECT dance_id, name, duration_seconds FROM unitree_dances ORDER BY name"
    )
    motions = await db.execute_fetchall(
        "SELECT motion_id, name, category FROM unitree_motions ORDER BY sort_order, motion_id"
    )
    if not enabled or (not dances and not motions):
        return ""   # 空字符串 → sync 时跳过, MEMORY 里不会出现这段
    lines = [f"# 副机器人: 宇树U1 (舞蹈 {len(dances)} / 动作 {len(motions)})\n"]
    lines.append("**这是副机器人** — 跟主机器人豹小秘 (你) 是分开的两台。"
                 "宇树U1 是宇树科技的双足人形机器人。")
    lines.append("调用方式: unitree_dance / unitree_motion 命令。豹小秘自己不会跳舞 / 仿生动作。")
    lines.append("reply 里称呼用 \"宇树\" / \"宇树机器人\" / \"宇树U1\"; "
                 "不要说 \"我给您跳\" (那是宇树跳, 不是豹小秘)。\n")
    lines.append("## 舞蹈\n")
    lines.append("| 舞名 | 时长(秒) |")
    lines.append("|---|---|")
    for d in dances:
        lines.append(f"| {d['name']} | {d['duration_seconds'] or ''} |")
    lines.append("\n## 动作\n")
    lines.append("| 动作名 | 类别 |")
    lines.append("|---|---|")
    for m in motions:
        lines.append(f"| {m['name']} | {m['category'] or ''} |")
    lines.append("\n说明: unitree_dance / unitree_motion 命令分别用舞名 / 动作名,"
                 " V5 会查 dance_id / motion_id 调宇树网关 /api/common。"
                 " 跳舞耗时较长 (101s 这种), 触发前最好先用 speak 告知用户'马上为您表演 X'。")
    return "\n".join(lines)


# ---------------- main entry ----------------

async def build_knowledge_files() -> dict[str, str]:
    """构建全部 KNOWLEDGE_*.md, 返回 {filename: content}。每个文件失败时
    单独跳过 (不让一张表挂了把全部知识同步搞死)。"""
    files: dict[str, str] = {}
    db = await get_db()
    try:
        builders = [
            ("KNOWLEDGE_EXHIBITS.md", _exhibits_md),
            ("KNOWLEDGE_SPECIALS.md", _specials_md),
            ("KNOWLEDGE_FLOWS.md", _flows_md),
            ("KNOWLEDGE_EQUIPMENT.md", _equipment_md),
            ("KNOWLEDGE_GROUPS.md", _groups_md),
            ("KNOWLEDGE_POSITIONS.md", _positions_md),
            ("KNOWLEDGE_UNITREE.md", _unitree_md),
            # 新增: 命令字典 + 复合指令协议 — 让 OpenClaw 拆解 "去X打招呼" 类多动作指令
            ("KNOWLEDGE_PRIMITIVES.md", _primitives_md),
            ("KNOWLEDGE_PROTOCOL.md", _protocol_md),
        ]
        for fname, fn in builders:
            try:
                files[fname] = await fn(db)
            except Exception as e:
                logger.warning("[knowledge] 构建 %s 失败: %s", fname, e)
    finally:
        await db.close()
    return files


async def sync_to_openclaw() -> dict:
    """同步知识到 OpenClaw agent workspace。

    OpenClaw 网关只接受固定文件名 (AGENTS/SOUL/USER/MEMORY/...), 不支持自定义,
    所以把全部 KNOWLEDGE_* 段合并到 MEMORY.md 里推一次。MEMORY.md 是 agent
    的"长期记忆"槽位, 启动时会自动随 prompt 注入。

    返回 {synced: [...], failed: [...], skipped_reason: ...}。OpenClaw 关闭
    或不通时返回 skipped 而不是抛, 启动钩子里调一次, /api/admin/openclaw/resync
    手动调一次, 都不应该把宿主进程搞挂。"""
    from app.engine import openclaw_client
    if not openclaw_client._yaml_enabled():
        return {"synced": [], "failed": [], "skipped_reason": "yaml disabled / not configured"}

    files = await build_knowledge_files()
    if not files:
        return {"synced": [], "failed": [], "skipped_reason": "no knowledge built"}

    # 合并所有 section 为一份 MEMORY.md
    sections = []
    sections.append("# V5 展厅世界模型 (V5 自动同步, 不要修改)\n")
    sections.append(f"_最后同步: {_now_str()}_\n")
    sections.append("\n---\n")
    for fname in (
        # 协议 + 命令字典放最前面, 让 LLM 在看具体数据之前先吃下规则
        "KNOWLEDGE_PROTOCOL.md", "KNOWLEDGE_PRIMITIVES.md",
        "KNOWLEDGE_EXHIBITS.md", "KNOWLEDGE_SPECIALS.md", "KNOWLEDGE_FLOWS.md",
        "KNOWLEDGE_EQUIPMENT.md", "KNOWLEDGE_GROUPS.md", "KNOWLEDGE_POSITIONS.md",
        "KNOWLEDGE_UNITREE.md",
    ):
        body = files.get(fname)
        if body:
            sections.append(body)
            sections.append("\n---\n")
    combined = "\n".join(sections)

    try:
        await openclaw_client.set_agent_file("MEMORY.md", combined)
        size = len(combined)
        logger.info("[knowledge] 同步完成 → MEMORY.md (%d chars, %d sections)",
                    size, len(files))
        return {
            "synced": [{"name": "MEMORY.md", "size": size, "sections": list(files.keys())}],
            "failed": [],
            "total_chars": size,
        }
    except Exception as e:
        logger.warning("[knowledge] 推送 MEMORY.md 失败: %s", e)
        return {"synced": [], "failed": [{"name": "MEMORY.md", "error": str(e)}],
                "total_chars": 0}


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")
