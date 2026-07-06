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

$localConfig = @{}
$localConfigPath = Join-Path $PSScriptRoot "orbit_runtime.local.json"
if (Test-Path $localConfigPath) {
    try {
        $rawConfig = Get-Content -Raw -Encoding UTF8 $localConfigPath | ConvertFrom-Json
        $configNode = if ($rawConfig.config) { $rawConfig.config } else { $rawConfig }
        foreach ($property in $configNode.PSObject.Properties) {
            if ($null -ne $property.Value -and "$($property.Value)" -ne "") {
                $localConfig[$property.Name] = "$($property.Value)"
            }
        }
    } catch {
        Write-Warning "Failed to load local GUI config: $($_.Exception.Message)"
    }
}

function Set-OrbitEnvDefault {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowEmptyString()][string]$Fallback,
        [string]$ConfigKey
    )
    if (-not $ConfigKey) {
        $ConfigKey = $Name.ToLowerInvariant() -replace '^orbit_', '' -replace '^ollama_', 'ollama_' -replace '^homebox_', 'homebox_' -replace '^scale_', 'scale_' -replace '^rfid_', 'rfid_'
    }
    if ([Environment]::GetEnvironmentVariable($Name, "Process")) {
        return
    }
    if ($localConfig.ContainsKey($ConfigKey)) {
        [Environment]::SetEnvironmentVariable($Name, $localConfig[$ConfigKey], "Process")
        return
    }
    [Environment]::SetEnvironmentVariable($Name, $Fallback, "Process")
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
Set-OrbitEnvDefault "OLLAMA_MODEL" "gemma3:4b" "ollama_model"
Set-OrbitEnvDefault "OLLAMA_NUM_GPU" "36" "ollama_num_gpu"
Set-OrbitEnvDefault "OLLAMA_NUM_CTX" "2048" "ollama_num_ctx"
Set-OrbitEnvDefault "OLLAMA_NUM_PREDICT" "192" "num_predict"
Set-OrbitEnvDefault "OLLAMA_TIMEOUT" "150" "ollama_timeout"
Set-OrbitEnvDefault "OLLAMA_KEEP_ALIVE" "15m" "ollama_keep_alive"
Set-OrbitEnvDefault "ORBIT_AI_TARGET" "local" "ai_target"
Set-OrbitEnvDefault "ORBIT_CLOUD_PROVIDER" "openai" "cloud_provider"
Set-OrbitEnvDefault "ORBIT_AI_API_BASE" "https://api.openai.com/v1" "ai_api_base"
Set-OrbitEnvDefault "HOMEBOX_URL" "http://192.168.31.3:3100" "homebox_url"
Set-OrbitEnvDefault "HOMEBOX_USERNAME" "" "homebox_username"
Set-OrbitEnvDefault "HOMEBOX_PASSWORD" "" "homebox_password"
Set-OrbitEnvDefault "HOMEBOX_TOKEN" "" "homebox_token"
Set-OrbitEnvDefault "ORBIT_AI_API_KEY" "" "ai_api_key"
Set-OrbitEnvDefault "ORBIT_SCAN_MODE" "auto" "mode"
$env:ORBIT_SEGMENT_MAX_SIZE = if ($env:ORBIT_SEGMENT_MAX_SIZE) { $env:ORBIT_SEGMENT_MAX_SIZE } else { "960" }
$env:ORBIT_EXPOSURE_RETRY = if ($env:ORBIT_EXPOSURE_RETRY) { $env:ORBIT_EXPOSURE_RETRY } else { "1" }
$env:ORBIT_EXPOSURE_TARGET_MEAN = if ($env:ORBIT_EXPOSURE_TARGET_MEAN) { $env:ORBIT_EXPOSURE_TARGET_MEAN } else { "54" }
$env:ORBIT_EXPOSURE_HIGHLIGHT_P98 = if ($env:ORBIT_EXPOSURE_HIGHLIGHT_P98) { $env:ORBIT_EXPOSURE_HIGHLIGHT_P98 } else { "242" }
$env:ORBIT_EXPOSURE_CLIP_RATIO = if ($env:ORBIT_EXPOSURE_CLIP_RATIO) { $env:ORBIT_EXPOSURE_CLIP_RATIO } else { "0.012" }
$env:ORBIT_AI_ENHANCE = if ($env:ORBIT_AI_ENHANCE) { $env:ORBIT_AI_ENHANCE } else { "1" }
$env:ORBIT_AI_COMPOSITE_VIEW = if ($env:ORBIT_AI_COMPOSITE_VIEW) { $env:ORBIT_AI_COMPOSITE_VIEW } else { "0" }
$env:ORBIT_AI_IMAGE_MAX_SIZE = if ($env:ORBIT_AI_IMAGE_MAX_SIZE) { $env:ORBIT_AI_IMAGE_MAX_SIZE } else { if ($localConfig.ContainsKey("image_max_size")) { $localConfig["image_max_size"] } else { "0" } }
$env:ORBIT_AI_IMAGE_JPEG_QUALITY = if ($env:ORBIT_AI_IMAGE_JPEG_QUALITY) { $env:ORBIT_AI_IMAGE_JPEG_QUALITY } else { "80" }
$env:ORBIT_EXPOSURE_ROI = if ($env:ORBIT_EXPOSURE_ROI) { $env:ORBIT_EXPOSURE_ROI } else { "0" }
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_ENABLED" "1" "aux_camera_enabled"
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_INDEX" "0" "aux_camera_index"
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_BACKEND" "dshow" "aux_camera_backend"
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_WIDTH" "1280" "aux_camera_width"
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_HEIGHT" "720" "aux_camera_height"
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_AUTO_EXPOSURE" "1" "aux_camera_auto_exposure"
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_CENTER_CROP" "1" "aux_camera_center_crop"
Set-OrbitEnvDefault "ORBIT_AUX_CAMERA_WARMUP_SECONDS" "1.2" "aux_camera_warmup_seconds"
$env:ORBIT_AUX_RECAPTURE_ON_SELECTION = if ($env:ORBIT_AUX_RECAPTURE_ON_SELECTION) { $env:ORBIT_AUX_RECAPTURE_ON_SELECTION } else { "0" }
Set-OrbitEnvDefault "ORBIT_PRINT_LABELS" "1" "print_pet_labels"
Set-OrbitEnvDefault "ORBIT_WRITE_RFID" "1" "write_rfid_tags"
Set-OrbitEnvDefault "RFID_PORT" "" "rfid_port"
Set-OrbitEnvDefault "SCALE_PORT" "COM9" "scale_port"
Set-OrbitEnvDefault "SCALE_BAUD" "9600" "scale_baud"

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
