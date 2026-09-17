<#
.SYNOPSIS
    Everyday cost-saver: deallocates the dev VM and downgrades its OS disk to Standard HDD.
.DESCRIPTION
    Deallocated = $0 compute. Standard_LRS cuts the disk cost ~40% vs StandardSSD_LRS.
    Reverse with start-vm.ps1. For long idle periods use archive-to-cool.ps1 instead.
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-winonlinux-dev',
    [string]$VmName = 'vm-wol-dev'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Subscription

Write-Host "Deallocating '$VmName' (releases compute billing)..."
Invoke-Az @('vm', 'deallocate', '-g', $ResourceGroup, '-n', $VmName, '-o', 'none')

$diskName = Get-OsDiskName -ResourceGroup $ResourceGroup -VmName $VmName
Write-Host "Downgrading OS disk '$diskName' to Standard_LRS (HDD)..."
Invoke-Az @('disk', 'update', '-g', $ResourceGroup, '-n', $diskName, '--sku', 'Standard_LRS', '-o', 'none')

# az.cmd on Windows mangles JMESPath filters with parentheses; use the simple -d powerState field.
$state = az vm show -d -g $ResourceGroup -n $VmName --query 'powerState' -o tsv
$sku = az disk show -g $ResourceGroup -n $diskName --query 'sku.name' -o tsv
Write-Host ''
Write-Host "=== Stopped. VM: $state | OS disk: $sku ==="
Write-Host 'Ongoing cost: OS disk (~`$6/mo) + static public IP (~`$4/mo). Restart with ./start-vm.ps1'
