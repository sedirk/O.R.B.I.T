$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$inf = Join-Path $root "drivers\cp210x\extracted\silabser.inf"
$logDir = Join-Path $root "logs"
$log = Join-Path $logDir "cp210x-driver-install.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

"[$(Get-Date -Format s)] CP210x driver install started" | Out-File -FilePath $log -Encoding utf8
"INF: $inf" | Tee-Object -FilePath $log -Append

if (-not (Test-Path $inf)) {
    "INF not found" | Tee-Object -FilePath $log -Append
    exit 2
}

pnputil /add-driver "$inf" /install 2>&1 | Tee-Object -FilePath $log -Append
$exit = $LASTEXITCODE
"pnputil exit code: $exit" | Tee-Object -FilePath $log -Append

pnputil /scan-devices 2>&1 | Tee-Object -FilePath $log -Append
"[$(Get-Date -Format s)] CP210x driver install finished" | Tee-Object -FilePath $log -Append
exit $exit
