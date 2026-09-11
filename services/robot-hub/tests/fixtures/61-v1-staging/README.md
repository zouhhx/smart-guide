# 61 V1 staging 实际响应（2026-09-07）

来源是 `http://192.168.16.61:3481`，不含教学 Token 或请求鉴权头。

- `qa-answered.json`：本方直接 HTTP 采集的响应。
- `qa-no-knowledge.json`、`qa-staff-only.json`：61 staging 部署目录的 `staging-results/v5-contract-integration-results.json` 中的开发方实测响应。本方随后用实际 V5 代码再次验证对应分流，记录在交付目录。
- `analysis-probe-01.json`、`draft-*.json`：本方直接 HTTP 采集，包含请求、状态码、响应和时间。
- `analysis-diagnostic-*.json`：本方针对多段分析失败的对照请求。多行样例返回 Ollama 5000ms 超时 / HTTP 503；单行对照返回成功。样本不足以证明换行是根因。

完整证据目录：`D:/robot/deliverables/v5-staging-integration-r1-20260907`。采样和诊断脚本不属于生产运行入口；诊断 HTTP 等待 8 秒用于看见服务端错误，应用原有 5 秒分析期限未修改。
