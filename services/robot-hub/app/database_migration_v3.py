"""
数据库 v3 迁移 — 访客记忆中心 7 张新表 + system_config 扩展
"""
import aiosqlite
import logging

logger = logging.getLogger("hub.database_migration_v3")

NEW_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS visitor_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_user_id TEXT UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    display_name TEXT DEFAULT '',
    source TEXT DEFAULT 'face_register',
    visit_count INTEGER DEFAULT 0,
    first_seen_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    last_seen_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    last_robot_id TEXT DEFAULT '',
    category_id INTEGER DEFAULT NULL,
    profile_summary TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    avatar_url TEXT DEFAULT '',
    last_face_photo_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_vp_robot_user_id ON visitor_profiles(robot_user_id);
CREATE INDEX IF NOT EXISTS idx_vp_name ON visitor_profiles(name);
CREATE INDEX IF NOT EXISTS idx_vp_category ON visitor_profiles(category_id);
CREATE INDEX IF NOT EXISTS idx_vp_status ON visitor_profiles(status);

CREATE TABLE IF NOT EXISTS face_register_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT UNIQUE NOT NULL,
    robot_id TEXT NOT NULL,
    visitor_name TEXT NOT NULL DEFAULT '',
    visitor_id INTEGER,
    robot_user_id TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    error_code TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    created_by TEXT DEFAULT 'admin',
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_frt_request_id ON face_register_tasks(request_id);
CREATE INDEX IF NOT EXISTS idx_frt_status ON face_register_tasks(status);
CREATE INDEX IF NOT EXISTS idx_frt_robot_id ON face_register_tasks(robot_id);

CREATE TABLE IF NOT EXISTS visitor_robot_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robot_id TEXT NOT NULL,
    visitor_id INTEGER NOT NULL,
    status TEXT DEFAULT 'active',
    recognized_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    last_interaction_at TIMESTAMP,
    expires_at TIMESTAMP,
    binding_source TEXT DEFAULT 'face',
    binding_confidence REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(robot_id, visitor_id)
);
CREATE INDEX IF NOT EXISTS idx_vrb_robot ON visitor_robot_bindings(robot_id);
CREATE INDEX IF NOT EXISTS idx_vrb_visitor ON visitor_robot_bindings(visitor_id);
CREATE INDEX IF NOT EXISTS idx_vrb_status ON visitor_robot_bindings(status);

CREATE TABLE IF NOT EXISTS visitor_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visitor_id INTEGER NOT NULL,
    robot_id TEXT DEFAULT '',
    question TEXT DEFAULT '',
    answer TEXT DEFAULT '',
    intent TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    special_id INTEGER,
    exhibit_id INTEGER,
    memory_level TEXT DEFAULT 'short',
    raw_context_json TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    FOREIGN KEY(visitor_id) REFERENCES visitor_profiles(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_vm_visitor ON visitor_memories(visitor_id);
CREATE INDEX IF NOT EXISTS idx_vm_exhibit ON visitor_memories(exhibit_id);
CREATE INDEX IF NOT EXISTS idx_vm_level ON visitor_memories(memory_level);
CREATE INDEX IF NOT EXISTS idx_vm_created ON visitor_memories(created_at);

CREATE TABLE IF NOT EXISTS visitor_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '#64748b',
    sort_order INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS visitor_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '#94a3b8',
    type TEXT DEFAULT 'interest',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS visitor_profile_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visitor_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    score REAL DEFAULT 1.0,
    source TEXT DEFAULT 'manual',
    updated_at TIMESTAMP DEFAULT (datetime('now','localtime')),
    UNIQUE(visitor_id, tag_id),
    FOREIGN KEY(visitor_id) REFERENCES visitor_profiles(id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES visitor_tags(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_vpt_visitor ON visitor_profile_tags(visitor_id);

INSERT OR IGNORE INTO visitor_categories (name, color, sort_order) VALUES
    ('普通访客', '#64748b', 1),
    ('重点客户', '#d97706', 2),
    ('合作伙伴', '#2563eb', 3),
    ('内部人员', '#16a34a', 4);

INSERT OR IGNORE INTO visitor_tags (name, color, type, sort_order) VALUES
    ('机械臂',   '#94a3b8', 'interest', 1),
    ('数字人',   '#94a3b8', 'interest', 2),
    ('自动讲解', '#94a3b8', 'interest', 3),
    ('技术型',   '#94a3b8', 'behavior', 4),
    ('商务型',   '#94a3b8', 'behavior', 5),
    ('CAVE',     '#94a3b8', 'interest', 6),
    ('大岛台',   '#94a3b8', 'interest', 7);

INSERT OR IGNORE INTO system_config (key, value, description) VALUES
    ('visitor_welcome_enabled', '1', '访客自动欢迎开关'),
    ('visitor_welcome_cooldown', '300', '欢迎冷却时间(秒)'),
    ('visitor_welcome_mode', 'recognized', '欢迎模式: any=有人即欢迎 recognized=识别成功才欢迎'),
    ('visitor_welcome_generic', '您好，欢迎来到展厅！', '普通欢迎词'),
    ('visitor_welcome_returning', '欢迎回来，{name}！', '老访客欢迎词'),
    ('visitor_welcome_vip', '尊敬的{name}，欢迎您再次光临！', 'VIP欢迎词'),
    ('visitor_welcome_say_name', '1', '欢迎时是否播报姓名'),
    ('visitor_binding_ttl', '300', '访客绑定有效期(秒)'),
    ('visitor_lock_duration', '60', '识别后锁定时长(秒)不切换');
"""


async def migrate_v3(db_path="data/exhibition_hub.db"):
    logger.info("[migration_v3] 开始迁移...")
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        check = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='visitor_profiles'"
        )
        if check:
            logger.info("[migration_v3] visitor_profiles 已存在，跳过迁移")
            return {"status": "skipped"}
        await db.executescript(NEW_TABLES_SQL)
        await db.commit()
        logger.info("[migration_v3] 7张新表创建完成 + 预置数据已插入")
        return {"status": "ok"}
    except Exception as e:
        await db.rollback()
        logger.error("[migration_v3] 迁移失败: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}
    finally:
        await db.close()
