@echo off
echo Setting up portproxy...
netsh interface portproxy add v4tov4 listenport=5173 listenaddress=192.168.20.158 connectport=5173 connectaddress=172.20.149.146
netsh interface portproxy add v4tov4 listenport=5180 listenaddress=192.168.20.158 connectport=5180 connectaddress=172.20.149.146
netsh interface portproxy add v4tov4 listenport=3000 listenaddress=192.168.20.158 connectport=3000 connectaddress=172.20.149.146
echo Done! Press any key to exit.
pause
