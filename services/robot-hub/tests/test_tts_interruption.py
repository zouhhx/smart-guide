import asyncio
from unittest.mock import AsyncMock

import pytest

from app.tcp import server
from app.engine import flow_executor


@pytest.mark.asyncio
@pytest.mark.parametrize('data,listen', [({'success': False, 'reason': 'TTS已停止'}, False),
                                        ({'success': False, 'reason': 'tts_timeout'}, False),
                                        ({'success': True}, True), ({}, True)])
async def test_interrupted_tts_releases_wait_without_restarting_asr(monkeypatch, data, listen):
    conn = server.RobotConnection()
    conn.robot_id = 'test-barge-in'
    conn.send = AsyncMock(return_value=True)
    conn.tts_done_event.clear()
    monkeypatch.setitem(server._robot_connections, conn.robot_id, conn)
    monkeypatch.setattr(server, '_update_robot_status', AsyncMock())
    monkeypatch.setattr(server, '_get_robot_config_data', AsyncMock(return_value={'listenTimeout': 5}))
    monkeypatch.setattr(flow_executor, 'get_active_flow', lambda _: None)
    waiter = asyncio.create_task(conn.tts_done_event.wait())
    await server._handle_event(conn, 'evt_tts_done', 42, data)
    await asyncio.wait_for(waiter, .5)
    await asyncio.sleep(0)
    sent = [call.args[0] for call in conn.send.await_args_list]
    assert any(m['type'] == 'cmd_listen' for m in sent) is listen
