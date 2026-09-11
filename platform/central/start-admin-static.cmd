@echo off
cd /d "D:\exhibition-hall-new\runtime\exhibition-hall-platform"
set ADMIN_DIST=D:\exhibition-hall-new\runtime\exhibition-hall-platform\admin-ui\dist
set DEVELOPER_DIST=D:\exhibition-hall-new\runtime\exhibition-hall-platform\admin\dist
set ADMIN_HOST=0.0.0.0
set ADMIN_PORT=5180
"C:\Program Files\nodejs\node.exe" "D:\exhibition-hall-new\runtime\exhibition-hall-platform\admin-static-server.mjs" >> admin-static.out.log 2>> admin-static.err.log
