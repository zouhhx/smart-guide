$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$DemoDir = Join-Path $env:TEMP "voice_gateway_p1_demo"
$env:VOICE_GATEWAY_DATA_DIR = Join-Path $env:TEMP "voice_gateway_p1_runtime"
$env:PYTHONIOENCODING = "utf-8"

New-Item -ItemType Directory -Force -Path $DemoDir | Out-Null
Set-Location $Root

Add-Type -AssemblyName System.Speech
$Synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
$ChineseVoice = $Synthesizer.GetInstalledVoices() |
    Where-Object { $_.VoiceInfo.Culture.Name -eq "zh-CN" } |
    Select-Object -First 1

if (-not $ChineseVoice) {
    throw "No zh-CN Windows speech voice is installed. Use recorded WAV files instead."
}

$Synthesizer.SelectVoice($ChineseVoice.VoiceInfo.Name)
$Wake = Join-Path $DemoDir "wake.wav"
$Question = Join-Path $DemoDir "question.wav"
$Demo = Join-Path $DemoDir "demo.wav"
$WakeText = -join (0x4F60, 0x597D, 0x601D, 0x5FB7 | ForEach-Object { [char]$_ })
$QuestionText = -join (0x8BF7, 0x4ECB, 0x7ECD, 0x4E00, 0x4E0B, 0x8FD9, 0x4E2A, 0x41, 0x49, 0x5C55, 0x9879, 0x6709, 0x4EC0, 0x4E48, 0x529F, 0x80FD | ForEach-Object { [char]$_ })

try {
    $Synthesizer.SetOutputToWaveFile($Wake)
    $Synthesizer.Speak($WakeText)
    $Synthesizer.SetOutputToNull()
    $Synthesizer.SetOutputToWaveFile($Question)
    $Synthesizer.Speak($QuestionText)
    $Synthesizer.SetOutputToNull()
}
finally {
    $Synthesizer.Dispose()
}

& ".venv\Scripts\python.exe" "tools\make_demo_wav.py" $Wake $Question $Demo
& ".venv\Scripts\voice-gateway.exe" --config "config\default.json" file $Demo
