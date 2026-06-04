param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollama)) {
    throw "Ollama not found. Expected: $ollama"
}

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir "ollama-vulkan.out.log"
$stderrLog = Join-Path $logDir "ollama-vulkan.err.log"

function Invoke-OllamaPsWithTimeout {
    param([string]$OllamaExe)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $OllamaExe
    $psi.Arguments = "ps"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)

    if ($proc.WaitForExit(5000)) {
        $out = $proc.StandardOutput.ReadToEnd()
        $err = $proc.StandardError.ReadToEnd()
        if ($out) { Write-Host $out.TrimEnd() }
        if ($err) { Write-Host $err.TrimEnd() }
    } else {
        try { $proc.Kill() } catch {}
        Write-Host "ollama ps timed out after 5s; service may still be starting."
    }
}

$running = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if ($running -and -not $Restart) {
    Write-Host "Ollama is already running. Use -Restart to relaunch with Vulkan/proxy env."
    Invoke-OllamaPsWithTimeout $ollama
    exit 0
}

if ($running -and $Restart) {
    Write-Host "Stopping existing Ollama processes..."
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

$env:OLLAMA_VULKAN = "1"
$env:HTTP_PROXY = "http://127.0.0.1:10808"
$env:HTTPS_PROXY = "http://127.0.0.1:10808"
$env:NO_PROXY = "localhost,127.0.0.1,::1,192.168.31.3"

Write-Host "Starting Ollama with Vulkan and v2rayN proxy env..."
Write-Host "Logs:"
Write-Host "  $stdoutLog"
Write-Host "  $stderrLog"

Start-Process `
    -FilePath $ollama `
    -ArgumentList "serve" `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog

Start-Sleep -Seconds 3
Invoke-OllamaPsWithTimeout $ollama
