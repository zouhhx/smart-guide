$ErrorActionPreference = 'Continue'
$root = 'D:\exhibition-hall-new\runtime\exhibition-hall-platform\admin\dist'
$prefix = 'http://*:5180/'
$log = 'D:\exhibition-hall-new\runtime\exhibition-hall-platform\admin-http-server.log'
function Log([string]$msg) { Add-Content -LiteralPath $log -Value "$(Get-Date -Format s) $msg" -Encoding UTF8 }
$types = @{
  '.html' = 'text/html; charset=utf-8'; '.js' = 'application/javascript; charset=utf-8'; '.css' = 'text/css; charset=utf-8'; '.json' = 'application/json; charset=utf-8'; '.png' = 'image/png'; '.jpg' = 'image/jpeg'; '.jpeg' = 'image/jpeg'; '.svg' = 'image/svg+xml; charset=utf-8'; '.ico' = 'image/x-icon'
}
function Resolve-AdminPath([string]$rawUrl) {
  $pathOnly = ($rawUrl -split '\?')[0]
  $decoded = [Uri]::UnescapeDataString($pathOnly).TrimStart('/')
  if ($decoded -eq 'admin') { $decoded = '' } elseif ($decoded.StartsWith('admin/')) { $decoded = $decoded.Substring(6) }
  if ([string]::IsNullOrWhiteSpace($decoded)) { $decoded = 'index.html' }
  $candidate = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $decoded))
  $rootFull = [IO.Path]::GetFullPath($root)
  if (-not $candidate.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) { return $null }
  if (-not (Test-Path -LiteralPath $candidate)) {
    if ($pathOnly.StartsWith('/assets/') -or $pathOnly.StartsWith('/admin/assets/')) { return '__404__' }
    return [IO.Path]::Combine($rootFull, 'index.html')
  }
  $item = Get-Item -LiteralPath $candidate
  if ($item.PSIsContainer) { return [IO.Path]::Combine($rootFull, 'index.html') }
  return $candidate
}
try {
  Log "starting prefix=$prefix root=$root"
  $listener = [System.Net.HttpListener]::new()
  $listener.Prefixes.Add($prefix)
  $listener.Start()
  Log "started"
  while ($listener.IsListening) {
    $ctx = $null
    try {
      $ctx = $listener.GetContext()
      $target = Resolve-AdminPath $ctx.Request.RawUrl
      Log "$($ctx.Request.HttpMethod) $($ctx.Request.RawUrl) -> $target"
      if ($null -eq $target) { $ctx.Response.StatusCode = 403; $bytes=[Text.Encoding]::UTF8.GetBytes('Forbidden') }
      elseif ($target -eq '__404__') { $ctx.Response.StatusCode = 404; $bytes=[Text.Encoding]::UTF8.GetBytes('Not found') }
      else {
        $ext = [IO.Path]::GetExtension($target).ToLowerInvariant()
        $ctx.Response.ContentType = if ($types.ContainsKey($ext)) { $types[$ext] } else { 'application/octet-stream' }
        if ($target.EndsWith('index.html')) { $ctx.Response.Headers['Cache-Control'] = 'no-store' } else { $ctx.Response.Headers['Cache-Control'] = 'public, max-age=31536000, immutable' }
        $bytes = [IO.File]::ReadAllBytes($target)
        $ctx.Response.StatusCode = 200
      }
      $ctx.Response.ContentLength64 = $bytes.Length
      if ($ctx.Request.HttpMethod -ne 'HEAD') { $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length) }
      $ctx.Response.OutputStream.Flush()
      $ctx.Response.Close()
    } catch {
      Log "request error: $($_.Exception.ToString())"
      try { if ($ctx -ne $null) { $ctx.Response.StatusCode = 500; $ctx.Response.Close() } } catch {}
    }
  }
} catch {
  Log "fatal: $($_.Exception.ToString())"
  Start-Sleep -Seconds 3
}
