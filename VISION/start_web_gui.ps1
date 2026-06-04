$ErrorActionPreference = "Stop"

$python = "$env:USERPROFILE\.conda\envs\orbit\python.exe"
if (-not (Test-Path $python)) {
    throw "orbit conda environment not found. Expected: $python"
}

$hostName = if ($env:ORBIT_WEB_HOST) { $env:ORBIT_WEB_HOST } else { "0.0.0.0" }
$port = if ($env:ORBIT_WEB_PORT) { $env:ORBIT_WEB_PORT } else { "8765" }
$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir "web-gui.out.log"
$stderrLog = Join-Path $logDir "web-gui.err.log"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
$env:OLLAMA_MODEL = if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { "gemma3:4b" }
$env:OLLAMA_NUM_GPU = if ($env:OLLAMA_NUM_GPU) { $env:OLLAMA_NUM_GPU } else { "36" }
$env:OLLAMA_NUM_CTX = if ($env:OLLAMA_NUM_CTX) { $env:OLLAMA_NUM_CTX } else { "2048" }
$env:OLLAMA_NUM_PREDICT = if ($env:OLLAMA_NUM_PREDICT) { $env:OLLAMA_NUM_PREDICT } else { "192" }
$env:OLLAMA_TIMEOUT = if ($env:OLLAMA_TIMEOUT) { $env:OLLAMA_TIMEOUT } else { "150" }
$env:HOMEBOX_URL = if ($env:HOMEBOX_URL) { $env:HOMEBOX_URL } else { "http://192.168.31.3:3100" }
$env:ORBIT_SCAN_MODE = if ($env:ORBIT_SCAN_MODE) { $env:ORBIT_SCAN_MODE } else { "scale" }
$env:ORBIT_EXPOSURE_RETRY = if ($env:ORBIT_EXPOSURE_RETRY) { $env:ORBIT_EXPOSURE_RETRY } else { "1" }
$env:ORBIT_EXPOSURE_TARGET_MEAN = if ($env:ORBIT_EXPOSURE_TARGET_MEAN) { $env:ORBIT_EXPOSURE_TARGET_MEAN } else { "54" }
$env:ORBIT_EXPOSURE_HIGHLIGHT_P98 = if ($env:ORBIT_EXPOSURE_HIGHLIGHT_P98) { $env:ORBIT_EXPOSURE_HIGHLIGHT_P98 } else { "242" }
$env:ORBIT_EXPOSURE_CLIP_RATIO = if ($env:ORBIT_EXPOSURE_CLIP_RATIO) { $env:ORBIT_EXPOSURE_CLIP_RATIO } else { "0.012" }
$env:ORBIT_AI_ENHANCE = if ($env:ORBIT_AI_ENHANCE) { $env:ORBIT_AI_ENHANCE } else { "1" }
$env:ORBIT_AI_COMPOSITE_VIEW = if ($env:ORBIT_AI_COMPOSITE_VIEW) { $env:ORBIT_AI_COMPOSITE_VIEW } else { "0" }
$env:ORBIT_AI_IMAGE_MAX_SIZE = if ($env:ORBIT_AI_IMAGE_MAX_SIZE) { $env:ORBIT_AI_IMAGE_MAX_SIZE } else { "0" }
$env:ORBIT_AI_IMAGE_JPEG_QUALITY = if ($env:ORBIT_AI_IMAGE_JPEG_QUALITY) { $env:ORBIT_AI_IMAGE_JPEG_QUALITY } else { "80" }
$env:ORBIT_EXPOSURE_ROI = if ($env:ORBIT_EXPOSURE_ROI) { $env:ORBIT_EXPOSURE_ROI } else { "0" }
$env:ORBIT_AUX_CAMERA_ENABLED = if ($env:ORBIT_AUX_CAMERA_ENABLED) { $env:ORBIT_AUX_CAMERA_ENABLED } else { "1" }
$env:ORBIT_AUX_CAMERA_INDEX = if ($env:ORBIT_AUX_CAMERA_INDEX) { $env:ORBIT_AUX_CAMERA_INDEX } else { "0" }
$env:ORBIT_AUX_CAMERA_BACKEND = if ($env:ORBIT_AUX_CAMERA_BACKEND) { $env:ORBIT_AUX_CAMERA_BACKEND } else { "dshow" }
$env:ORBIT_AUX_CAMERA_AUTO_EXPOSURE = if ($env:ORBIT_AUX_CAMERA_AUTO_EXPOSURE) { $env:ORBIT_AUX_CAMERA_AUTO_EXPOSURE } else { "1" }
$env:ORBIT_AUX_CAMERA_CENTER_CROP = if ($env:ORBIT_AUX_CAMERA_CENTER_CROP) { $env:ORBIT_AUX_CAMERA_CENTER_CROP } else { "1" }
$env:ORBIT_AUX_CAMERA_WARMUP_SECONDS = if ($env:ORBIT_AUX_CAMERA_WARMUP_SECONDS) { $env:ORBIT_AUX_CAMERA_WARMUP_SECONDS } else { "1.2" }
$env:ORBIT_WRITE_RFID = if ($env:ORBIT_WRITE_RFID) { $env:ORBIT_WRITE_RFID } else { "1" }
$env:SCALE_PORT = if ($env:SCALE_PORT) { $env:SCALE_PORT } else { "COM9" }
$env:SCALE_BAUD = if ($env:SCALE_BAUD) { $env:SCALE_BAUD } else { "9600" }

$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Web GUI port already listening: http://${hostName}:$port"
    exit 0
}

Write-Host "Starting O.R.B.I.T. Web GUI: http://${hostName}:$port"
Write-Host "Logs:"
Write-Host "  $stdoutLog"
Write-Host "  $stderrLog"

$script = Join-Path $PSScriptRoot "web_gui.py"
$arguments = @("`"$script`"", "--host", $hostName, "--port", $port)

Start-Process `
    -FilePath $python `
    -ArgumentList $arguments `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

Start-Sleep -Seconds 2
$healthHost = if ($hostName -eq "0.0.0.0") { "127.0.0.1" } else { $hostName }
Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://${healthHost}:$port/api/status" | Select-Object -ExpandProperty Content
