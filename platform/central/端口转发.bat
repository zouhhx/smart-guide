@echo off
:: 以管理员身份运行端口转发
echo 正在配置端口转发...
netsh interface portproxy add v4tov4 listenport=5173 listenaddress=192.168.20.158 connectport=5173 connectaddress=172.20.149.146
netsh interface portproxy add v4tov4 listenport=5180 listenaddress=192.168.20.158 connectport=5180 connectaddress=172.20.149.146
netsh interface portproxy add v4tov4 listenport=3000 listenaddress=192.168.20.158 connectport=3000 connectaddress=172.20.149.146
echo.
echo 配置完成！手机可以访问：
echo   http://192.168.20.158:5173 (移动端)
echo   http://192.168.20.158:5180 (管理端)
echo   ws://192.168.20.158:3000 (中转服务)
echo.
pause
