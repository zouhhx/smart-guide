@echo off
setlocal

fltmc >nul 2>&1
if errorlevel 1 (
  echo Requesting administrator permission to configure Windows Firewall...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo Configuring Windows Firewall for Voice Gateway Web UI...
netsh advfirewall firewall delete rule name="VoiceGateway WebUI LAN" >nul 2>&1
netsh advfirewall firewall add rule name="VoiceGateway WebUI LAN" dir=in action=allow protocol=TCP localport=8765 profile=any remoteip=LocalSubnet

if errorlevel 1 (
  echo.
  echo Failed to add the firewall rule.
  pause
  exit /b 1
)

echo.
echo LAN access is enabled for TCP port 8765.
echo Other computers can open: http://%COMPUTERNAME%:8765/
echo If the computer name does not resolve, use this computer's IPv4 address.
pause
exit /b 0
