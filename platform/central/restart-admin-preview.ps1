$own = $PID
$procs = Get-CimInstance Win32_Process | Where-Object {
  $_.ProcessId -ne $own -and $_.CommandLine -and (
    $_.CommandLine -like '*admin-static-server.mjs*' -or
    $_.CommandLine -like '*vite*preview*5180*'
  )
}
foreach ($p in $procs) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Start-Process -FilePath 'D:\exhibition-hall-new\runtime\exhibition-hall-platform\start-admin-static.cmd' -WorkingDirectory 'D:\exhibition-hall-new\runtime\exhibition-hall-platform' -WindowStyle Hidden
Start-Sleep -Seconds 2
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*admin-static-server.mjs*' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress
