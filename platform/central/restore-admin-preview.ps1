$own = $PID
Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $own -and $_.CommandLine -and (
    $_.CommandLine.Contains('admin-http-server.ps1') -or
    $_.CommandLine.Contains('admin-static-server.mjs') -or
    ($_.CommandLine.Contains('vite.js') -and $_.CommandLine.Contains('preview') -and $_.CommandLine.Contains('5180'))
  )
} | ForEach-Object {
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Start-Process -FilePath 'cmd.exe' -ArgumentList '/c D:\exhibition-hall-new\runtime\exhibition-hall-platform\start-admin-static.cmd' -WorkingDirectory 'D:\exhibition-hall-new\runtime\exhibition-hall-platform' -WindowStyle Hidden
Start-Sleep -Seconds 3
netstat -ano | findstr LISTENING | findstr :5180
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.Contains('admin-static-server.mjs') } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress
