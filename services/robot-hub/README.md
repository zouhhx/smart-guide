# 机器人 V5 中枢

Python/FastAPI 服务，负责机器人 TCP 接入、问答转发、教学会话、意图判断及管理页面。**它不是 61 上的 Node 中控**；历史现场部署是 `192.168.18.227:/opt/exhibition-hub`，本轮未连接确认。

源码入口 `app/main.py`；TCP 在 `app/tcp/`，流程在 `app/engine/`，管理页面在 `app/static/`，数据逻辑在 `app/database.py`。HTTP 8000，TCP 19888。

## 开发与配置

在隔离 Python 环境安装 `requirements-local-tested.txt` 后运行：

```powershell
python -m pytest -q
```

运行入口：`python -m app.main config.yaml`。先参考 `config.example.yaml` 配置当前环境；样例包含旧的关闭开关，不是可直接覆盖生产的配置。历史 systemd 服务是 `exhibition-hub.service`，SQLite 路径 `data/exhibition_hub.db`，真实配置/数据单独受控备份。

当前交付在 Windows Python3.12 验证过，历史现场 Python3.11.4。`requirements-local-tested.txt` 不是完整 Linux 生产锁；CI 和目标环境依赖锁待补齐。

## 业务边界

问答及教学分析/草稿对接 61:3001；教学仅写待审核草稿，由 61 审核发布。MiMo 用于意图判断，不用于替换现行讯飞 ASR 或 61 本地 TTS，不改写工作人员教学原文。机器人播放由机器人客户端负责，不随意转发成 2 号屏播报。

## 来源与状态

从机器人交付复制 104 文件。交付副本已清除历史默认凭据，真实密钥需通过原配置机制提供；本次没有再改业务文件。V5 无可追溯原 Git 历史，不能伪造旧提交。

交付方记录 190 项测试通过；本轮只做完整性验证，没有运行服务、数据库迁移或现场联调。
