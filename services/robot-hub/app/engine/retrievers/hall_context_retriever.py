"""
HallContextRetriever — 展厅当前上下文回答

数据源优先级:
  1. browse 已选资源 (browse.selected_*)
  2. WorldState.exhibit.current_exhibit_*
  3. position_bindings 反查 (机器人当前 nav_position 名 → 绑定的 exhibit)
  4. 兜底引导

返回 dict:
  {"answer": str, "source": str, "confidence": float, "exhibit_id": int|None, "exhibit_name": str}
"""

from __future__ import annotations

import logging

from app.database import get_db

logger = logging.getLogger("hub.engine.retriever.hall_context")


async def _fetch_narration(exhibit_id: int, exhibit_name: str) -> str:
    """从 exhibit_knowledge / 兜底拼接 取讲解词"""
    if not exhibit_id:
        return ""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT narration_text, name FROM exhibit_knowledge WHERE exhibit_id=?",
            (str(exhibit_id),),
        )
        if rows:
            n = rows[0]["narration_text"]
            if n and n.strip():
                return n.strip()
    except Exception as e:
        logger.debug("[HallContext] narration fetch fail: %s", e)
    finally:
        await db.close()
    return ""


async def _lookup_by_robot_position(robot_id: str) -> dict | None:
    """根据机器人当前 nav_position.name 反查绑定的展项"""
    try:
        from app.tcp.server import get_robot
        robot = get_robot(robot_id)
    except Exception:
        robot = None
    position = getattr(robot, "position", "") if robot else ""
    if not position:
        return None
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """SELECT e.id, e.name FROM exhibits e
               JOIN position_bindings pb ON pb.bind_target_id = e.id
               JOIN nav_positions np ON np.id = pb.position_id
               WHERE pb.bind_type='exhibit' AND np.name=?
               LIMIT 1""",
            (position,),
        )
        if rows:
            return {"id": int(rows[0]["id"]), "name": rows[0]["name"]}
    except Exception as e:
        logger.debug("[HallContext] position lookup fail: %s", e)
    finally:
        await db.close()
    return None


async def retrieve(text: str, slots: dict, world_state, robot_id: str) -> dict:
    """主入口"""
    ws = world_state

    # 1) browse 选中资源/展项
    try:
        browse = ws.browse
        if browse and browse.mode in ("list_resources", "playing") and getattr(browse, "selected_exhibit_name", ""):
            name = browse.selected_exhibit_name
            ex_id = 0
            try:
                ex_id = int(getattr(browse, "selected_exhibit_id", "") or 0)
            except (TypeError, ValueError):
                pass
            narration = await _fetch_narration(ex_id, name) if ex_id else ""
            answer = narration or f"当前屏幕上是{name}。"
            return {
                "answer": answer,
                "source": "browse",
                "confidence": 0.95,
                "exhibit_id": ex_id,
                "exhibit_name": name,
            }
    except Exception:
        pass

    # 2) WorldState.exhibit
    ex_id_str = ws.exhibit.current_exhibit_id if ws else ""
    ex_name = ws.exhibit.current_exhibit_name if ws else ""
    if ex_id_str and ex_name:
        try:
            ex_id = int(ex_id_str)
        except (TypeError, ValueError):
            ex_id = 0
        narration = await _fetch_narration(ex_id, ex_name) if ex_id else ""
        answer = narration or f"当前是{ex_name}。"
        return {
            "answer": answer,
            "source": "world_state",
            "confidence": 0.92,
            "exhibit_id": ex_id,
            "exhibit_name": ex_name,
        }

    # 3) 机器人位置反查
    exhibit = await _lookup_by_robot_position(robot_id)
    if exhibit:
        narration = await _fetch_narration(exhibit["id"], exhibit["name"]) or ""
        answer = narration or f"我们在{exhibit['name']}。"
        return {
            "answer": answer,
            "source": "position_binding",
            "confidence": 0.85,
            "exhibit_id": exhibit["id"],
            "exhibit_name": exhibit["name"],
        }

    # 4) 文本里显式提到的展项 — 当作 hall_knowledge 的兜底也行,但既然路由进来了,这里也尝试
    name_hint = slots.get("exhibit_name") if slots else ""
    if name_hint:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT id, name FROM exhibits WHERE name LIKE ? LIMIT 1",
                (f"%{name_hint}%",),
            )
            if rows:
                ex_id, ex_name = int(rows[0]["id"]), rows[0]["name"]
                narration = await _fetch_narration(ex_id, ex_name) or f"{ex_name}。"
                return {
                    "answer": narration,
                    "source": "name_lookup",
                    "confidence": 0.78,
                    "exhibit_id": ex_id,
                    "exhibit_name": ex_name,
                }
        finally:
            await db.close()

    # 5) 兜底
    return {
        "answer": "您可以直接说展项名称我带您过去，或者说「有哪些展项」查看完整列表。",
        "source": "fallback",
        "confidence": 0.4,
        "exhibit_id": None,
        "exhibit_name": "",
    }
