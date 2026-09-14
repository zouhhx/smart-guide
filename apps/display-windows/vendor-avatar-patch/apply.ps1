param([Parameter(Mandatory=$true)][string]$Archive,[Parameter(Mandatory=$true)][string]$Candidate,[Parameter(Mandatory=$true)][string]$Backup,[Parameter(Mandatory=$true)][string]$AvatarExe,[string]$StartTask='ExhibitionAvatarStartOnce')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$expected='1331D61D1C66DE76155CCEB572863008B97090F533FE188D4EDBA97FCC8F2F34'
$f=[IO.File]::OpenRead($archive);$br=New-Object IO.BinaryReader($f)
$null=$br.ReadUInt32();$hs=$br.ReadUInt32();$null=$br.ReadUInt32();$js=$br.ReadUInt32()
$headerBytes=$br.ReadBytes($js);$header=[Text.Encoding]::UTF8.GetString($headerBytes);$tree=$header|ConvertFrom-Json
$meta=$tree.files.out.files.renderer.files.assets.files.'index-CPRpiHM5.js'
$offset=8+$hs+[long]$meta.offset;$f.Position=$offset;$old=$br.ReadBytes($meta.size);$f.Close()
$sha=[Security.Cryptography.SHA256]::Create();$oldHash=([BitConverter]::ToString($sha.ComputeHash($old))).Replace('-','')
if($oldHash -ne $expected){throw 'Renderer changed; refusing patch'}
$new=[IO.File]::ReadAllBytes($candidate);if($new.Length -ne $old.Length){throw 'Candidate size mismatch'}
$newHash=([BitConverter]::ToString($sha.ComputeHash($new))).Replace('-','').ToLowerInvariant()
if($newHash -ne '04540b0bf96a8498927d75ccd80606fbe23d41299943ecd31298420f705c6054'){throw 'Candidate hash does not match the verified v21 renderer'}
$null=Get-ScheduledTask -TaskName $StartTask -ErrorAction Stop
if($meta.integrity){
 if($meta.integrity.algorithm -ne 'SHA256' -or $meta.integrity.blocks.Count -ne 1 -or $meta.integrity.blockSize -lt $new.Length){throw 'Unexpected integrity layout'}
 $header=$header.Replace($oldHash.ToLowerInvariant(),$newHash)
}
$newHeader=[Text.Encoding]::UTF8.GetBytes($header);if($newHeader.Length -ne $headerBytes.Length){throw 'Header size changed'}
if(Test-Path -LiteralPath $backup){if((Get-FileHash -LiteralPath $archive).Hash -ne (Get-FileHash -LiteralPath $backup).Hash){throw 'Archive differs from existing backup'}}
if(-not (Test-Path -LiteralPath $backup)){Copy-Item -LiteralPath $archive -Destination $backup}
Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $AvatarExe} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue}
$f=[IO.File]::Open($archive,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None)
try {$f.Position=16;$f.Write($newHeader,0,$newHeader.Length);$f.Position=$offset;$f.Write($new,0,$new.Length);$f.Flush()}finally{$f.Close()}
Start-ScheduledTask -TaskName $StartTask
Write-Output ('Patched renderer SHA256: '+$newHash)
Write-Output ('Rollback archive: '+$backup)
