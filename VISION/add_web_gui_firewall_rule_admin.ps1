$ErrorActionPreference = "Stop"

$ruleName = "O.R.B.I.T. Web GUI 8765"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Set-NetFirewallRule -DisplayName $ruleName -Enabled True -Action Allow
} else {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8765 `
        -Profile Private,Domain
}

Get-NetFirewallRule -DisplayName $ruleName | Format-List DisplayName,Enabled,Direction,Action,Profile
