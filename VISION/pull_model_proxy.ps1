$ErrorActionPreference = "Stop"

$model = if ($args.Count -gt 0) { $args[0] } else { "gemma3:4b" }
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"

$env:HTTP_PROXY = "http://127.0.0.1:10808"
$env:HTTPS_PROXY = "http://127.0.0.1:10808"
$env:NO_PROXY = "localhost,127.0.0.1,::1,192.168.31.3"

Write-Host "Pulling $model through v2rayN proxy http://127.0.0.1:10808"
& $ollama pull $model
