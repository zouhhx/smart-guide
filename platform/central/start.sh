#!/bin/bash
# ============================================================
# 展厅智能中控 - 一键启动脚本
# ============================================================

set -e
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

echo "╔══════════════════════════════════════════════╗"
echo "║     展厅智能中控 - 启动脚本               ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# 1. 启动中转服务 (Phase 1)
echo "📡 [1/4] 启动中转服务..."
cd "$BASE_DIR/src"
if lsof -ti:3000 > /dev/null 2>&1; then
  echo "  ✅ 中转服务已在运行 (端口 3000)"
else
  node index.js &
  echo "  ✅ 中转服务已启动 (端口 3000, PID=$!)"
fi

# 2. 启动移动端 H5 (Phase 3)
echo "📱 [2/4] 启动移动端 H5..."
cd "$BASE_DIR/mobile"
if lsof -ti:5173 > /dev/null 2>&1; then
  echo "  ✅ 移动端已在运行 (端口 5173)"
else
  node_modules/.bin/vite &
  echo "  ✅ 移动端已启动 (端口 5173, PID=$!)"
fi

# 3. 启动管理端 Web (Phase 5)
echo "🖥️ [3/4] 启动管理端 Web..."
cd "$BASE_DIR/admin"
if lsof -ti:5180 > /dev/null 2>&1; then
  echo "  ✅ 管理端已在运行 (端口 5180)"
else
  node_modules/.bin/vite &
  echo "  ✅ 管理端已启动 (端口 5180, PID=$!)"
fi

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║           ✅ 全部服务已启动                  ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "📱 移动端（手机遥控器）：http://<电脑IP>:5173"
echo "🖥️ 管理端（后台管理）：   http://localhost:5180"
echo "🔌 中转服务 WebSocket：   ws://localhost:3000"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo ""

# 等待退出
wait
