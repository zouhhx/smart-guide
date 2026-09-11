"""
数据库迁移工具 — 阶段一
为智能接待引擎添加 7 张新表 + 改造 robots 表
2026-04-11 Phase 1
"""

import aiosqlite
import logging

logger = logging.getLogger("hub.database.migration_v2")

MIGRATION_SQL = """
-- ============================================================
-- 迁移1: robots 表增加 robot_uid 字段（全局唯一标识）
-- ============================================================
ALTER TABLE robots ADD COLUMN robot_uid TEXT;
-- 给现有数据生成 robot_uid
UPDATE robots SET robot_uid = 'r_' || id WHERE robot_uid IS NULL;
CREATE INDEX IF NOT EXISTS idx_robots_uid ON robots(robot_uid);

-- ============================================================
-- 20. 机器人扩展信息表 (robot_uid → 硬件序列号/IP等)
-- ============================================================
CREATE TABLE IF NOT EXISTS robot_meta (
    robot_uid       TEXT PRIMARY KEY,
    robot_sn        TEXT DEFAULT '',       -- 猎豹 SDK 硬件序列号
    ip_address      TEXT DEFAULT '',
    world_state_json TEXT,                 -- 当前世界模型快照
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (robot_uid) REFERENCES robots(id)
);

-- ============================================================
-- 21. 展项知识库 (exhibit_knowledge)
-- ============================================================
CREATE TABLE IF NOT EXISTS exhibit_knowledge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    exhibit_id      TEXT UNIQUE NOT NULL,  -- 关联 exhibits.id
    name            TEXT NOT NULL,
    category        TEXT DEFAULT '',       -- 展区/分类
    tags            TEXT DEFAULT '[]',     -- JSON: ["核心","互动"]
    narrations      TEXT DEFAULT '{}',     -- JSON: {"normal":"...", "vip":"...", "kid":"..."}
    key_data        TEXT DEFAULT '{}',     -- JSON: {"面积":"500㎡"}
    faq             TEXT DEFAULT '[]',     -- JSON: [{"q":"...","a":"..."}]
    related_exhibits TEXT DEFAULT '[]',    -- JSON: exhibit_ids
    walk_time_from_lobby INTEGER DEFAULT 60, -- 从大厅步行时间(秒)
    talk_time       INTEGER DEFAULT 180,   -- 讲解时长(秒)
    is_core         INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_ek_name ON exhibit_knowledge(name);

-- ============================================================
-- 22. 人员档案 (person_profiles)
-- ============================================================
CREATE TABLE IF NOT EXISTS person_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    category        TEXT DEFAULT '访客',   -- 访客/VIP/内部
    local_face_id   INTEGER,               -- 机器人本地 face ID（按机器隔离）
    face_photo_path TEXT DEFAULT '',
    face_photo_uploaded TIMESTAMP,
    personality_tags TEXT DEFAULT '[]',     -- JSON: ["外向","技术型"]
    hobbies         TEXT DEFAULT '[]',      -- JSON: ["AI","数字孪生"]
    communication_style TEXT DEFAULT '',    -- direct/verbose/casual/formal
    dialogue_summary TEXT DEFAULT '',       -- 对话摘要 (500字以内)
    key_facts       TEXT DEFAULT '[]',      -- JSON: 关键事实
    first_seen      TIMESTAMP DEFAULT (datetime('now','localtime')),
    last_seen       TIMESTAMP DEFAULT (datetime('now','localtime')),
    visit_count     INTEGER DEFAULT 1,
    total_interactions INTEGER DEFAULT 0,
    verified        INTEGER DEFAULT 0,      -- 0=未验证, 1=已确认
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pp_name ON person_profiles(name);
CREATE INDEX IF NOT EXISTS idx_pp_verified ON person_profiles(verified);

-- ============================================================
-- 23. 人脸注册映射表 (person ↔ robot 按机器隔离)
-- ============================================================
CREATE TABLE IF NOT EXISTS face_registrations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id       INTEGER NOT NULL,
    robot_uid       TEXT NOT NULL,
    local_face_id   INTEGER NOT NULL,
    registered_at   TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(person_id, robot_uid),
    FOREIGN KEY(person_id) REFERENCES person_profiles(id) ON DELETE CASCADE,
    FOREIGN KEY(robot_uid) REFERENCES robots(id)
);

-- ============================================================
-- 24. 交互记忆 (interaction_memories)
-- ============================================================
CREATE TABLE IF NOT EXISTS interaction_memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_uid       TEXT DEFAULT '',
    person_id       INTEGER,
    person_name     TEXT DEFAULT '',
    session_id      TEXT DEFAULT '',
    location        TEXT DEFAULT '',
    exhibit_id      TEXT DEFAULT '',
    user_input      TEXT DEFAULT '',
    robot_response  TEXT DEFAULT '',
    intent          TEXT DEFAULT '',
    extracted_facts TEXT DEFAULT '',       -- LLM 提取的事实 JSON
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(person_id) REFERENCES person_profiles(id)
);

CREATE INDEX IF NOT EXISTS idx_im_person ON interaction_memories(person_id);
CREATE INDEX IF NOT EXISTS idx_im_session ON interaction_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_im_time ON interaction_memories(created_at);

-- ============================================================
-- 25. 自学习 FAQ (learned_faq)
-- ============================================================
CREATE TABLE IF NOT EXISTS learned_faq (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    exhibit_id      TEXT DEFAULT '',
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    hit_count       INTEGER DEFAULT 1,
    auto_generated  INTEGER DEFAULT 1,     -- 1=自动生成, 0=手动录入
    verified        INTEGER DEFAULT 0,     -- 0=待审核, 1=已通过
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_faq_exhibit ON learned_faq(exhibit_id);

-- ============================================================
-- 26. 全局经验 (global_experience)
-- ============================================================
CREATE TABLE IF NOT EXISTS global_experience (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT DEFAULT '',        -- routing/interaction/fallback/preference
    trigger_condition TEXT DEFAULT '',
    action          TEXT DEFAULT '{}',       -- JSON action
    hit_count       INTEGER DEFAULT 1,
    auto_learned    INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at      TIMESTAMP DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 27. 系统配置扩展 (新键)
-- ============================================================
INSERT OR IGNORE INTO system_config (key, value, description)
VALUES
    ('reception_engine_enabled', '1', '智能接待引擎开关 1=启用 0=关闭'),
    ('intent_llm_enabled', '1', '意图识别LLM兜底开关'),
    ('default_time_budget_minutes', '30', '默认参观时间预算(分钟)'),
    ('listen_timeout_seconds', '30', '收音超时(秒)'),
    ('idle_timeout_seconds', '120', '空闲超时(秒)回待'),
    ('openclaw_enabled', '1', 'OpenClaw决策桥开关 1=启用 0=关闭(改回minimax直连)');
"""


async def run_migration_v2(db_path: str = "data/exhibition_hub.db"):
    """执行阶段一数据库迁移"""
    logger.info("[迁移v2] 开始...")
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        # 检查是否已迁移过（用表名判断）
        tables = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='exhibit_knowledge'"
        )
        if tables:
            logger.info("[迁移v2] 已迁移过，跳过")
            return {"status": "skipped", "message": "already migrated"}

        await db.executescript(MIGRATION_SQL)
        await db.commit()
        logger.info("[迁移v2] 7 张新表 + robot_uid 添加完成")
        return {"status": "ok", "message": "7 tables added, robots extended"}
    except Exception as e:
        await db.rollback()
        logger.error("[迁移v2] 失败: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        await db.close()
