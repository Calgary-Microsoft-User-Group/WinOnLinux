<#
.SYNOPSIS
    Restores the OS disk to Standard SSD and starts the dev VM.
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-winonlinux-dev',
    [string]$VmName = 'vm-wol-dev',
    [string]$DiskSku = 'StandardSSD_LRS'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Subscription

$diskName = Get-OsDiskName -ResourceGroup $ResourceGroup -VmName $VmName
$currentSku = az disk show -g $ResourceGroup -n $diskName --query 'sku.name' -o tsv
if ($currentSku -ne $DiskSku) {
    Write-Host "Upgrading OS disk '$diskName' from $currentSku to $DiskSku..."
    Invoke-Az @('disk', 'update', '-g', $ResourceGroup, '-n', $diskName, '--sku', $DiskSku, '-o', 'none')
}

Write-Host "Starting '$VmName'..."
Invoke-Az @('vm', 'start', '-g', $ResourceGroup, '-n', $VmName, '-o', 'none')

$publicIp = az vm list-ip-addresses -g $ResourceGroup -n $VmName --query '[0].virtualMachine.network.publicIpAddresses[0].ipAddress' -o tsv
Write-Host ''
Write-Host "=== Running. RDP: mstsc /v:$publicIp | SSH: ssh <user>@$publicIp ==="
