"""
数据库模型与初始化
展厅流程中枢系统 v5.0

核心数据层级：展馆 → 展区 → 展项(terminal) → 专场 → 资源
关键关系：展项资源列表 = f(展项, 专场)  -- 动态关联
"""

import aiosqlite
import os
import logging

logger = logging.getLogger("hub.database")

DB_PATH = "data/exhibition_hub.db"


async def get_db() -> aiosqlite.Connection:
    """获取数据库连接"""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_database(db_path: str = None):
    """初始化数据库，创建全部表结构"""
    global DB_PATH
    if db_path:
        DB_PATH = db_path

    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = await get_db()

    try:
        await db.executescript(SCHEMA_SQL)
        # 已存在库的 ALTER: resources.narration_edited
        cols = await db.execute_fetchall("PRAGMA table_info(resources)")
        if not any(c["name"] == "narration_edited" for c in cols):
            await db.execute(
                "ALTER TABLE resources ADD COLUMN narration_edited INTEGER DEFAULT 0"
            )
            logger.info("resources 增加 narration_edited 字段")
        # 已存在库的 ALTER: unitree_maps.display_name (本地中文别名)
        cols = await db.execute_fetchall("PRAGMA table_info(unitree_maps)")
        if cols and not any(c["name"] == "display_name" for c in cols):
            await db.execute(
                "ALTER TABLE unitree_maps ADD COLUMN display_name TEXT DEFAULT ''"
            )
            logger.info("unitree_maps 增加 display_name 字段")
        # 已存在库的 ALTER: tour_points 拍照流程配置
        cols = await db.execute_fetchall("PRAGMA table_info(tour_points)")
        if cols:
            if not any(c["name"] == "is_photo_point" for c in cols):
                await db.execute(
                    "ALTER TABLE tour_points ADD COLUMN is_photo_point INTEGER DEFAULT 0"
                )
                logger.info("tour_points 增加 is_photo_point 字段")
            if not any(c["name"] == "photo_suggestion_enabled" for c in cols):
                await db.execute(
                    "ALTER TABLE tour_points ADD COLUMN photo_suggestion_enabled INTEGER DEFAULT 0"
                )
                logger.info("tour_points 增加 photo_suggestion_enabled 字段")
            if not any(c["name"] == "photo_suggestion_text" for c in cols):
                await db.execute(
                    "ALTER TABLE tour_points ADD COLUMN photo_suggestion_text TEXT DEFAULT ''"
                )
                logger.info("tour_points 增加 photo_suggestion_text 字段")
            if not any(c["name"] == "photo_download_base_url" for c in cols):
                await db.execute(
                    "ALTER TABLE tour_points ADD COLUMN photo_download_base_url TEXT DEFAULT ''"
                )
                logger.info("tour_points 增加 photo_download_base_url 字段")
        await db.commit()
        logger.info("数据库初始化完成: %s", DB_PATH)
    finally:
        await db.close()


# ============================================================
# 完整数据库 Schema
# ============================================================

SCHEMA_SQL = """
-- ============================================================
-- 1. 展馆
-- ============================================================
CREATE TABLE IF NOT EXISTS halls (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 2. 展区
-- ============================================================
CREATE TABLE IF NOT EXISTS areas (
    id              INTEGER PRIMARY KEY,
    hall_id         INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (hall_id) REFERENCES halls(id)
);

-- ============================================================
-- 3. 展项 (对应水滴云的 terminal)
--    用户界面叫"展项"，底层是终端/terminal
-- ============================================================
CREATE TABLE IF NOT EXISTS exhibits (
    id                              INTEGER PRIMARY KEY,  -- 水滴云 terminal.id
    area_id                         INTEGER,
    name                            TEXT NOT NULL,         -- 显示名称 (terminalTitle)
    exhibition_area_terminal_id     INTEGER,               -- 中控ID，TCP指令用这个！
    ip                              TEXT DEFAULT '',
    port                            TEXT DEFAULT '',
    terminal_type                   TEXT DEFAULT '',       -- 终端类型
    zone_name                       TEXT DEFAULT '',       -- 区域标识
    raw_json                        TEXT DEFAULT '{}',
    updated_at                      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (area_id) REFERENCES areas(id)
);

-- ============================================================
-- 4. 专场
-- ============================================================
CREATE TABLE IF NOT EXISTS specials (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    is_current      INTEGER DEFAULT 0,   -- 1=当前激活专场
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 5. 资源
-- ============================================================
CREATE TABLE IF NOT EXISTS resources (
    id              INTEGER PRIMARY KEY,  -- exhibitId
    name            TEXT NOT NULL,         -- exhibitTitle
    category        TEXT DEFAULT '',       -- 类别: 图片/视频/程序
    file_url        TEXT DEFAULT '',       -- 文件URL(图片/视频)
    thumbnail_url   TEXT DEFAULT '',       -- 缩略图
    program_name    TEXT DEFAULT '',       -- 程序名称(程序类型)
    program_path    TEXT DEFAULT '',       -- 程序本地路径
    original_file   TEXT DEFAULT '',       -- 原始文件名
    description     TEXT DEFAULT '',       -- 资源描述
    narration_text  TEXT DEFAULT '',       -- 讲解词(可后台编辑)
    narration_edited INTEGER DEFAULT 0,    -- 1=已被用户/AI编辑过, 同步不再覆盖 narration_text
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 6. 专场-展项-资源 关系表 (核心！动态资源关联)
--    资源列表 = f(展项, 专场)
--    同一资源可出现在: 同一专场的不同展项 / 同一展项的不同专场
-- ============================================================
CREATE TABLE IF NOT EXISTS special_exhibit_resources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    special_id      INTEGER NOT NULL,
    exhibit_id      INTEGER NOT NULL,
    resource_id     INTEGER NOT NULL,
    sort_order      INTEGER DEFAULT 0,    -- 排序序号
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (special_id) REFERENCES specials(id),
    FOREIGN KEY (exhibit_id) REFERENCES exhibits(id),
    FOREIGN KEY (resource_id) REFERENCES resources(id),
    UNIQUE(special_id, exhibit_id, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_ser_special_exhibit
    ON special_exhibit_resources(special_id, exhibit_id);

-- ============================================================
-- 7. 设备 (继电器等，开关控制)
-- ============================================================
CREATE TABLE IF NOT EXISTS equipments (
    id              INTEGER PRIMARY KEY,
    area_id         INTEGER,
    name            TEXT NOT NULL,         -- equipmentTitle
    ip              TEXT DEFAULT '',
    port            TEXT DEFAULT '',
    device_type     TEXT DEFAULT '',       -- 设备类型
    zone_name       TEXT DEFAULT '',
    cmd_on          TEXT DEFAULT '',       -- TCP开指令 (如 0_r_26_on)
    cmd_off         TEXT DEFAULT '',       -- TCP关指令 (如 0_r_26_off)
    hex_on          TEXT DEFAULT '',       -- Hex开指令
    hex_off         TEXT DEFAULT '',       -- Hex关指令
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (area_id) REFERENCES areas(id)
);

-- ============================================================
-- 8. 组策略
-- ============================================================
CREATE TABLE IF NOT EXISTS group_commands (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    cmd_on          TEXT DEFAULT '',       -- 如 0_all_com_on
    cmd_off         TEXT DEFAULT '',       -- 如 0_all_com_off
    description     TEXT DEFAULT '',
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 9. 导航点位 (从 OrionStar 同步)
-- ============================================================
CREATE TABLE IF NOT EXISTS nav_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT NOT NULL UNIQUE,  -- OrionStar 点位ID
    name            TEXT NOT NULL,         -- 点位名称
    map_id          TEXT DEFAULT '',       -- 所属地图ID
    map_name        TEXT DEFAULT '',       -- 地图名称
    x               REAL DEFAULT 0,
    y               REAL DEFAULT 0,
    theta           REAL DEFAULT 0,
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 10. 点位绑定 (点位 → 展项/展区)
--     用于场景A: 机器人在某点位时，查当前屏幕有什么
-- ============================================================
CREATE TABLE IF NOT EXISTS position_bindings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     TEXT NOT NULL,         -- nav_positions.position_id
    bind_type       TEXT NOT NULL,         -- 'exhibit' 或 'area'
    bind_target_id  INTEGER NOT NULL,      -- exhibits.id 或 areas.id
    description     TEXT DEFAULT '',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(position_id, bind_type, bind_target_id)
);

-- ============================================================
-- 11. 意图命令映射
--     用于场景C: "开灯" → 意图 → 命令
-- ============================================================
CREATE TABLE IF NOT EXISTS intent_command_mappings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_name     TEXT NOT NULL UNIQUE,  -- 如 lights_on
    display_name    TEXT NOT NULL,         -- 如 "开灯"
    command_type    TEXT NOT NULL,         -- 'group_command' / 'equipment' / 'special_switch'
    command_target  TEXT NOT NULL,         -- 组策略name / 设备id / 专场id
    command_action  TEXT NOT NULL,         -- 'on' / 'off' / 'switch'
    reply_text      TEXT DEFAULT '',       -- 执行后回复文案
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 12. 参观流程脚本
-- ============================================================
CREATE TABLE IF NOT EXISTS tour_scripts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    trigger_word    TEXT NOT NULL UNIQUE,  -- 触发词
    description     TEXT DEFAULT '',
    special_id      INTEGER,              -- 适用专场(null=全部)
    enabled         INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (special_id) REFERENCES specials(id)
);

-- ============================================================
-- 13. 参观流程步骤
-- ============================================================
CREATE TABLE IF NOT EXISTS tour_script_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id       INTEGER NOT NULL,
    step_order      INTEGER NOT NULL,
    step_type       TEXT NOT NULL,         -- navigate/tts/show/play_resource/
                                           -- switch_special/group_command/equipment/
                                           -- delay/listen/branch/end
    config_json     TEXT DEFAULT '{}',     -- 步骤配置(JSON)
    wait_type       TEXT DEFAULT 'callback', -- callback/delay/none
    wait_timeout    INTEGER DEFAULT 30,    -- 超时秒数
    description     TEXT DEFAULT '',
    FOREIGN KEY (script_id) REFERENCES tour_scripts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_steps_script_order
    ON tour_script_steps(script_id, step_order);

-- ============================================================
-- 14. 机器人状态
-- ============================================================
CREATE TABLE IF NOT EXISTS robots (
    id              TEXT PRIMARY KEY,      -- robotId
    name            TEXT DEFAULT '',
    version         TEXT DEFAULT '',
    status          TEXT DEFAULT 'offline', -- offline/idle/navigating/speaking/
                                            -- listening/showing/charging/error
    battery         INTEGER DEFAULT 0,
    location        TEXT DEFAULT '',
    point_id        TEXT DEFAULT '',
    current_task    TEXT DEFAULT '',
    connected_at    TIMESTAMP,
    last_heartbeat  TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 15. 对话上下文 (机器人会话状态)
-- ============================================================
CREATE TABLE IF NOT EXISTS conversation_contexts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id        TEXT NOT NULL,
    intent_context  TEXT DEFAULT '',       -- 当前意图上下文
    context_data    TEXT DEFAULT '{}',     -- JSON: 候选资源等
    expire_at       TIMESTAMP,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 16. 执行日志
-- ============================================================
CREATE TABLE IF NOT EXISTS execution_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    log_type        TEXT NOT NULL,         -- tcp_command/flow_step/sync/robot_event/error
    source          TEXT DEFAULT '',       -- 来源模块
    target          TEXT DEFAULT '',       -- 目标
    action          TEXT DEFAULT '',       -- 具体动作
    detail          TEXT DEFAULT '',       -- 详情(JSON)
    status          TEXT DEFAULT 'ok',     -- ok/error/timeout
    duration_ms     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_logs_type_time
    ON execution_logs(log_type, created_at);

-- ============================================================
-- 16b. 宇树事件转发失败队列 (P1-② 防数据丢失)
-- 目标 URL 离线时, _forward_raw_event 失败的 payload 落到这里, 由后台
-- 任务定时重试; 成功转发则删除该行。
-- ============================================================
CREATE TABLE IF NOT EXISTS unitree_event_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    payload         TEXT NOT NULL,
    target_url      TEXT DEFAULT '',
    fail_count      INTEGER DEFAULT 0,
    last_error      TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    last_tried_at   TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_unitree_queue_time
    ON unitree_event_queue(created_at);

-- ============================================================
-- 17. 系统配置 (KV存储)
-- ============================================================
CREATE TABLE IF NOT EXISTS system_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    description     TEXT DEFAULT '',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- 预置系统配置
INSERT OR IGNORE INTO system_config (key, value, description)
VALUES
    ('current_special_id', '0', '当前激活专场ID'),
    ('current_special_name', '', '当前激活专场名称'),
    ('heartbeat_interval', '30', '机器人心跳间隔(秒)'),
    ('listen_timeout', '30', '机器人收音超时(秒)'),
    ('listen_language', 'zh', '机器人语音识别语言'),
    ('command_interval_ms', '2000', 'TCP指令间隔(毫秒)'),
    ('sync_interval_min', '5', '数据同步间隔(分钟)'),
    ('last_sync_time', '', '上次同步时间'),
    ('llm_enabled', '0', 'AI讲解词生成开关 0=关闭 1=开启'),
    ('llm_api_url', 'https://zhenze-huhehaote.cmecloud.cn/v1/chat/completions', 'LLM API地址'),
    ('llm_api_key', '', 'LLM API Key'),
    ('llm_model', 'minimax-2.5', 'LLM模型名称'),
    ('webhook_enabled', '0', 'Webhook告警开关 0=关闭 1=开启'),
    ('webhook_url', '', 'Webhook地址(企微/钉钉/飞书)'),
    ('webhook_events', 'robot_offline,robot_error,robot_low_battery', '告警事件类型(逗号分隔)'),
    ('unitree_enabled', '1', '宇树机器人网关 0=关闭 1=开启'),
    ('unitree_base_url', 'http://192.168.23.253:8000', '宇树机器人网关地址'),
    ('unitree_api_key', '', '宇树网关 X-API-KEY'),
    ('unitree_sync_interval_min', '60', '宇树动作/舞蹈/地图同步间隔(分钟)'),
    ('unitree_event_forward_url', 'http://192.168.23.76:8080/robot/api/event', '宇树回调转发地址 (空=关闭; fire-and-forget, 3秒超时, 不重试)');

-- ============================================================
-- 17b. 宇树机器人 动作库 / 舞蹈库 / 地图 / 点位 (从 HTTP 网关同步)
-- ============================================================
CREATE TABLE IF NOT EXISTS unitree_motions (
    motion_id       INTEGER PRIMARY KEY,       -- 动作 ID (如 2003)
    name            TEXT NOT NULL,             -- 动作名称 (如 "挥手")
    category        TEXT DEFAULT '',           -- 分类(备用)
    sort_order      INTEGER DEFAULT 0,
    raw_json        TEXT DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS unitree_dances (
    dance_id         TEXT PRIMARY KEY,         -- 舞蹈 ID (如 F1X)
    name             TEXT NOT NULL,            -- 舞蹈名称
    duration_seconds INTEGER DEFAULT 0,
    raw_json         TEXT DEFAULT '{}',
    updated_at       TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS unitree_maps (
    map_id           TEXT PRIMARY KEY,         -- 网关 map_id
    map_code         TEXT DEFAULT '',
    map_name         TEXT NOT NULL,            -- 网关原值 (通常是一串数字 ID)
    display_name     TEXT DEFAULT '',          -- V5 本地中文别名; sync 不覆盖; UI 优先显示
    map_index        INTEGER DEFAULT 0,
    is_current       INTEGER DEFAULT 0,        -- 1=当前工作地图
    raw_json         TEXT DEFAULT '{}',
    updated_at       TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS unitree_map_points (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id           TEXT NOT NULL,
    point_id         INTEGER NOT NULL,
    point_name       TEXT DEFAULT '',
    raw_json         TEXT DEFAULT '{}',
    updated_at       TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(map_id, point_id)
);

CREATE INDEX IF NOT EXISTS idx_unitree_points_map
    ON unitree_map_points(map_id);

-- 宇树点位绑定 (独立于豹小秘 position_bindings, 按 (map_id, point_id) 复合键)
--   用途: 机器人到达某宇树航点后自动查对应展项/展区, 同豹小秘侧逻辑对齐.
CREATE TABLE IF NOT EXISTS unitree_point_bindings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id          TEXT NOT NULL,
    point_id        INTEGER NOT NULL,
    bind_type       TEXT NOT NULL,         -- 'exhibit' 或 'area'
    bind_target_id  INTEGER NOT NULL,
    description     TEXT DEFAULT '',
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(map_id, point_id, bind_type, bind_target_id)
);

CREATE INDEX IF NOT EXISTS idx_unitree_pbind_point
    ON unitree_point_bindings(map_id, point_id);

-- ============================================================
-- 18. 定时任务
-- ============================================================
CREATE TABLE IF NOT EXISTS schedules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    schedule_type   TEXT NOT NULL,         -- cron/interval/daily/once
    schedule_expr   TEXT NOT NULL,         -- cron表达式 或 间隔秒数
    action_type     TEXT NOT NULL,         -- group_command/equipment/special_switch/
                                           -- resource_play/tour_script
    action_config   TEXT DEFAULT '{}',     -- JSON配置
    enabled         INTEGER DEFAULT 1,
    last_run        TIMESTAMP,
    next_run        TIMESTAMP,
    description     TEXT DEFAULT '',
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 19. 访客会话记录 (访客画像基础)
-- ============================================================
CREATE TABLE IF NOT EXISTS visitor_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL UNIQUE,  -- 唯一会话标识
    robot_id        TEXT DEFAULT '',       -- 接待机器人
    start_time      TIMESTAMP DEFAULT (datetime('now','localtime')),
    end_time        TIMESTAMP,
    visitor_name    TEXT DEFAULT '',        -- 访客名称(可选)
    visit_count     INTEGER DEFAULT 1,     -- 第几次来访
    summary         TEXT DEFAULT ''         -- 本次访问摘要
);

CREATE TABLE IF NOT EXISTS visitor_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    robot_id        TEXT DEFAULT '',
    interaction_type TEXT NOT NULL,         -- speech/navigate/resource_play/tts/show
    content         TEXT DEFAULT '',        -- 用户说的话 / 播放的资源名
    intent          TEXT DEFAULT '',        -- 识别的意图
    resource_id     INTEGER,               -- 关联资源
    exhibit_id      INTEGER,               -- 关联展项
    point_id        INTEGER,               -- 关联点位
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (session_id) REFERENCES visitor_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_vi_session
    ON visitor_interactions(session_id);

-- ============================================================
-- 16. 参观点位配置 (哪些点位支持语音触发参观讲解)
-- ============================================================
CREATE TABLE IF NOT EXISTS tour_points (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         TEXT NOT NULL,              -- nav_positions.position_id
    enabled             INTEGER DEFAULT 1,          -- 是否启用
    end_phrase          TEXT DEFAULT '感谢参观，欢迎下次再来！',
    return_position_id  TEXT DEFAULT '',             -- 参观结束后导航回的点位(如接待点)
    is_photo_point      INTEGER DEFAULT 0,           -- 是否作为拍照点
    photo_suggestion_enabled INTEGER DEFAULT 0,       -- 是否在该展项讲解结束后主动建议拍照(仅机械臂启用)
    photo_suggestion_text TEXT DEFAULT '',            -- 主动建议拍照话术
    photo_download_base_url TEXT DEFAULT '',          -- 照片二维码内容前缀/固定地址
    created_at          TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at          TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(position_id)
);

-- ============================================================
-- 18. 音乐曲库 (管理后台上传, "放一首歌" 随机命中)
-- ============================================================
CREATE TABLE IF NOT EXISTS music_tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,                              -- 显示名 (默认 = 上传文件原名去后缀)
    filename        TEXT NOT NULL UNIQUE,                       -- 落盘在 data/music/<filename>
    size_bytes      INTEGER DEFAULT 0,
    enabled         INTEGER DEFAULT 1,
    uploaded_at     TIMESTAMP DEFAULT (datetime('now','localtime'))
);

INSERT OR IGNORE INTO system_config (key, value, description)
VALUES ('music_url_base', '', '音乐下发给 APK 的 URL 前缀, 空=用请求 Host 自动拼; 例: http://192.168.22.1:8000');
"""
