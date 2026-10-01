<#
.SYNOPSIS
    Long-idle cost-saver: moves the dev VM's OS disk into COOL-tier blob storage, then
    deletes the VM and managed disk. Network (VNet/NSG/NIC/public IP) is kept so the
    address stays stable. Reverse with restore-from-cool.ps1.
.DESCRIPTION
    Flow: deallocate -> snapshot OS disk -> export snapshot (SAS) -> server-side copy into a
    Cool-tier BLOCK blob (page blobs cannot be tiered, so the copy converts the type) ->
    delete snapshot, VM and disk. Cool tier is online, so restore needs no rehydration wait.
    DESTRUCTIVE: the VM object and managed disk are deleted after the copy verifies. Requires
    -Force or an interactive confirmation.
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-winonlinux-dev',
    [string]$VmName = 'vm-wol-dev',
    [string]$Container = 'vm-archive',
    [string]$StorageAccount = '',
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Subscription

if (-not $StorageAccount) { $StorageAccount = Get-ArchiveStorageAccountName }
$blobName = "$VmName-osdisk.vhd"
$metaBlobName = "$VmName-restore-info.json"

$diskName = Get-OsDiskName -ResourceGroup $ResourceGroup -VmName $VmName
$vmSize = az vm show -g $ResourceGroup -n $VmName --query 'hardwareProfile.vmSize' -o tsv
$nicId = az vm show -g $ResourceGroup -n $VmName --query 'networkProfile.networkInterfaces[0].id' -o tsv
$diskJson = az disk show -g $ResourceGroup -n $diskName -o json | ConvertFrom-Json

Write-Host "This will archive '$VmName' to cool blob storage and then DELETE:"
Write-Host "  - VM:          $VmName"
Write-Host "  - OS disk:     $diskName ($($diskJson.diskSizeGB) GB, $($diskJson.sku.name))"
Write-Host "Kept: VNet, NSG, NIC, public IP. Archive blob: $StorageAccount/$Container/$blobName"
if (-not $Force) {
    $answer = Read-Host "Type 'archive' to continue"
    if ($answer -ne 'archive') { Write-Host 'Aborted.'; exit 1 }
}

Write-Host "Deallocating '$VmName'..."
Invoke-Az @('vm', 'deallocate', '-g', $ResourceGroup, '-n', $VmName, '-o', 'none')

$snapName = "$VmName-archive-snap"
Write-Host "Snapshotting OS disk to '$snapName'..."
Invoke-Az @('snapshot', 'create', '-g', $ResourceGroup, '-n', $snapName, '--source', $diskJson.id, '-o', 'none')

Write-Host "Ensuring cool-tier storage account '$StorageAccount'..."
$saExists = az storage account show -g $ResourceGroup -n $StorageAccount -o none 2>$null; $null = $saExists
if ($LASTEXITCODE -ne 0) {
    Invoke-Az @('storage', 'account', 'create', '-g', $ResourceGroup, '-n', $StorageAccount,
        '--sku', 'Standard_LRS', '--kind', 'StorageV2', '--access-tier', 'Cool',
        '--min-tls-version', 'TLS1_2', '--allow-blob-public-access', 'false', '-o', 'none')
}
$key = az storage account keys list -g $ResourceGroup -n $StorageAccount --query '[0].value' -o tsv
Invoke-Az @('storage', 'container', 'create', '--account-name', $StorageAccount, '--account-key', $key,
    '-n', $Container, '-o', 'none')

Write-Host 'Granting snapshot read access (24h SAS) and starting server-side copy to a Cool block blob...'
$sas = az snapshot grant-access -g $ResourceGroup -n $snapName --duration-in-seconds 86400 --access-level Read --query 'accessSAS' -o tsv
if ($LASTEXITCODE -ne 0 -or -not $sas) { throw 'snapshot grant-access failed.' }
Invoke-Az @('storage', 'blob', 'copy', 'start', '--account-name', $StorageAccount, '--account-key', $key,
    '--destination-container', $Container, '--destination-blob', $blobName,
    '--source-uri', $sas, '--destination-blob-type', 'BlockBlob', '--tier', 'Cool', '-o', 'none')

Write-Host 'Waiting for the copy to complete (a 128 GB disk typically takes 10-30 min)...'
Wait-BlobCopy -AccountName $StorageAccount -AccountKey $key -Container $Container -BlobName $blobName

$tier = az storage blob show --account-name $StorageAccount --account-key $key -c $Container -n $blobName --query 'properties.blobTier' -o tsv
Write-Host "Copy complete. Blob tier: $tier"
if ($tier -ne 'Cool') {
    Write-Host 'Setting blob tier to Cool...'
    Invoke-Az @('storage', 'blob', 'set-tier', '--account-name', $StorageAccount, '--account-key', $key,
        '-c', $Container, '-n', $blobName, '--tier', 'Cool', '-o', 'none')
}

# Record everything restore-from-cool.ps1 needs to rebuild the VM.
$meta = [ordered]@{
    vmName            = $VmName
    vmSize            = $vmSize
    nicId             = $nicId
    diskName          = $diskName
    diskSku           = $diskJson.sku.name
    diskSizeGb        = $diskJson.diskSizeGB
    hyperVGeneration  = $diskJson.hyperVGeneration
    archivedUtc       = (Get-Date).ToUniversalTime().ToString('o')
} | ConvertTo-Json
$metaPath = Join-Path ([System.IO.Path]::GetTempPath()) $metaBlobName
Set-Content -Path $metaPath -Value $meta -Encoding utf8NoBOM
Invoke-Az @('storage', 'blob', 'upload', '--account-name', $StorageAccount, '--account-key', $key,
    '-c', $Container, '-n', $metaBlobName, '-f', $metaPath, '--tier', 'Cool', '--overwrite', '-o', 'none')
Remove-Item $metaPath -Force

Write-Host 'Cleaning up snapshot and deleting VM + disk...'
Invoke-Az @('snapshot', 'revoke-access', '-g', $ResourceGroup, '-n', $snapName, '-o', 'none')
Invoke-Az @('snapshot', 'delete', '-g', $ResourceGroup, '-n', $snapName, '-o', 'none')
Invoke-Az @('vm', 'delete', '-g', $ResourceGroup, '-n', $VmName, '--yes', '-o', 'none')
Invoke-Az @('disk', 'delete', '-g', $ResourceGroup, '-n', $diskName, '--yes', '-o', 'none')

Write-Host ''
Write-Host '=== Archived to cool storage ==='
Write-Host "Blob: https://$StorageAccount.blob.core.windows.net/$Container/$blobName (tier: Cool)"
Write-Host 'Remaining monthly cost: cool blob (~`$1.50 for 128 GB) + public IP (~`$4).'
Write-Host "Restore with: ./restore-from-cool.ps1 -ResourceGroup $ResourceGroup -VmName $VmName"
