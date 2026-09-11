"""
数据库 v2 迁移 — 智能接待引擎 7 张新表 + robots 扩展
"""
import aiosqlite
import logging

logger = logging.getLogger("hub.database_migration_v2")

NEW_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS exhibit_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exhibit_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    narrations TEXT DEFAULT '{}',
    key_data TEXT DEFAULT '{}',
    faq TEXT DEFAULT '[]',
    related_exhibits TEXT DEFAULT '[]',
    walk_time_from_lobby INTEGER DEFAULT 60,
    talk_time INTEGER DEFAULT 180,
    is_core INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ek_name ON exhibit_knowledge(name);
CREATE INDEX IF NOT EXISTS idx_ek_exhibit_id ON exhibit_knowledge(exhibit_id);

CREATE TABLE IF NOT EXISTS person_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT DEFAULT '访客',
    local_face_id INTEGER,
    face_photo_path TEXT DEFAULT '',
    face_photo_uploaded TIMESTAMP,
    personality_tags TEXT DEFAULT '[]',
    hobbies TEXT DEFAULT '[]',
    communication_style TEXT DEFAULT '',
    dialogue_summary TEXT DEFAULT '',
    key_facts TEXT DEFAULT '[]',
    first_seen TIMESTAMP DEFAULT (datetime('now','localtime')),
    last_seen TIMESTAMP DEFAULT (datetime('now','localtime')),
    visit_count INTEGER DEFAULT 1,
    total_interactions INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pp_name ON person_profiles(name);
CREATE INDEX IF NOT EXISTS idx_pp_verified ON person_profiles(verified);

CREATE TABLE IF NOT EXISTS face_registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    robot_uid TEXT NOT NULL,
    local_face_id INTEGER NOT NULL,
    registered_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(person_id, robot_uid),
    FOREIGN KEY(person_id) REFERENCES person_profiles(id) ON DELETE CASCADE,
    FOREIGN KEY(robot_uid) REFERENCES robots(id)
);

CREATE TABLE IF NOT EXISTS interaction_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_uid TEXT DEFAULT '',
    person_id INTEGER,
    person_name TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    location TEXT DEFAULT '',
    exhibit_id TEXT DEFAULT '',
    user_input TEXT DEFAULT '',
    robot_response TEXT DEFAULT '',
    intent TEXT DEFAULT '',
    extracted_facts TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(person_id) REFERENCES person_profiles(id)
);
CREATE INDEX IF NOT EXISTS idx_im_person ON interaction_memories(person_id);

CREATE TABLE IF NOT EXISTS learned_faq (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exhibit_id TEXT DEFAULT '',
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    hit_count INTEGER DEFAULT 1,
    auto_generated INTEGER DEFAULT 1,
    verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_faq_exhibit ON learned_faq(exhibit_id);

CREATE TABLE IF NOT EXISTS global_experience (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT DEFAULT '',
    trigger_condition TEXT DEFAULT '',
    action TEXT DEFAULT '{}',
    hit_count INTEGER DEFAULT 1,
    auto_learned INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS robot_meta (
    robot_uid TEXT PRIMARY KEY,
    robot_sn TEXT DEFAULT '',
    ip_address TEXT DEFAULT '',
    world_state_json TEXT,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(robot_uid) REFERENCES robots(id)
);

INSERT OR IGNORE INTO system_config (key, value, description)
VALUES
    ('reception_engine_enabled', '1', '智能接待引擎开关 1=启用 0=关闭'),
    ('intent_llm_enabled', '1', '意图识别LLM兜底开关'),
    ('default_time_budget_minutes', '30', '默认参观时间预算(分钟)'),
    ('listen_timeout_seconds', '30', '收音超时(秒)'),
    ('idle_timeout_seconds', '120', '空闲超时(秒)回待机'),
    ('robot_display_name', '小豹', '机器人显示名称（欢迎语/社交/意图 prompt/讨论等场景使用）'),
    ('digital_human_display_name', '艾琳', '数字人显示名称（讨论等场景使用）'),
    ('openclaw_enabled', '1', 'OpenClaw决策桥开关 1=启用 0=关闭(改回minimax直连)');
"""


async def add_robot_uid_column(db):
    try:
        columns = await db.execute_fetchall("PRAGMA table_info(robots)")
        col_names = [c["name"] for c in columns]
        if "robot_uid" not in col_names:
            await db.execute("ALTER TABLE robots ADD COLUMN robot_uid TEXT")
            await db.execute("UPDATE robots SET robot_uid = id WHERE robot_uid IS NULL")
            await db.commit()
            logger.info("[migration_v2] robots 表增加了 robot_uid 字段")
        else:
            logger.debug("[migration_v2] robots.robot_uid 已存在")
        # person_count: evt_person_count 事件会写入此列, 没有则 SQL 报错丢事件
        if "person_count" not in col_names:
            await db.execute("ALTER TABLE robots ADD COLUMN person_count INTEGER DEFAULT 0")
            await db.commit()
            logger.info("[migration_v2] robots 表增加了 person_count 字段")
    except Exception as e:
        logger.warning("[migration_v2] 添加 robot 字段失败: %s", e)


async def migrate_v2(db_path="data/exhibition_hub.db"):
    logger.info("[migration_v2] 开始迁移...")
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        check = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='exhibit_knowledge'"
        )
        if check:
            await add_robot_uid_column(db)
            return {"status": "skipped"}
        await db.executescript(NEW_TABLES_SQL)
        await add_robot_uid_column(db)
        await db.commit()
        logger.info("[migration_v2] 7张新表创建完成 + robot_uid已添加")
        return {"status": "ok"}
    except Exception as e:
        await db.rollback()
        logger.error("[migration_v2] 迁移失败: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        await db.close()
