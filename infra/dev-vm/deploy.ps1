<#
.SYNOPSIS
    Deploys the WinOnLinux Linux dev VM (Ubuntu 24.04 LTS + GNOME over xrdp) into AZ-PRI-01.
.DESCRIPTION
    Creates the resource group and deploys main.bicep. The NSG only admits SSH/RDP from
    -AllowedIp (auto-detected from your current public IP when omitted).
    If -AdminPassword is omitted, az prompts for it securely.
    Azure password rules: 12-72 chars, 3 of 4 character classes.
.EXAMPLE
    ./deploy.ps1
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-winonlinux-dev',
    [string]$Location = 'canadacentral',
    [string]$VmName = 'vm-wol-dev',
    [string]$AdminUsername = 'woldev',
    [string]$VmSize = 'Standard_D4s_v4',
    [string]$AllowedIp = '',
    [string]$AdminPassword = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Subscription

if (-not $AllowedIp) {
    Write-Host 'Detecting your public IP...'
    $AllowedIp = (Invoke-RestMethod 'https://api.ipify.org?format=json').ip
    Write-Host "  NSG will allow SSH/RDP from: $AllowedIp"
}

Write-Host "Creating resource group '$ResourceGroup' in $Location..."
Invoke-Az @('group', 'create', '-n', $ResourceGroup, '-l', $Location, '-o', 'none')

$params = @(
    "vmName=$VmName"
    "adminUsername=$AdminUsername"
    "vmSize=$VmSize"
    "allowedSourceIp=$AllowedIp"
)
if ($AdminPassword) { $params += "adminPassword=$AdminPassword" }

Write-Host 'Deploying main.bicep (VM creation ~5 min; desktop provisioning continues ~15 min after that)...'
$deployArgs = @(
    'deployment', 'group', 'create',
    '-g', $ResourceGroup,
    '-n', "dev-vm-$(Get-Date -Format yyyyMMddHHmmss)",
    '--template-file', (Join-Path $PSScriptRoot 'main.bicep'),
    '--query', 'properties.outputs',
    '-o', 'json'
)
foreach ($p in $params) { $deployArgs += @('--parameters', $p) }
# When -AdminPassword was omitted, az prompts for the @secure() param interactively.
$outputs = az @deployArgs
if ($LASTEXITCODE -ne 0) { throw "Deployment failed:`n$outputs" }
$outputs = $outputs | ConvertFrom-Json

$publicIp = $outputs.publicIpAddress.value
Write-Host ''
Write-Host '=== Deployment complete ==='
Write-Host "Public IP : $publicIp"
Write-Host "SSH       : ssh $AdminUsername@$publicIp"
Write-Host "RDP       : mstsc /v:$publicIp   (login as $AdminUsername once provisioning finishes)"
Write-Host ''
Write-Host 'Desktop provisioning runs in the background on the VM. Check completion with:'
Write-Host "  ssh $AdminUsername@$publicIp cloud-init status --wait"
