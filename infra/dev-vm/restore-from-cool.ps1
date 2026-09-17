<#
.SYNOPSIS
    Rebuilds the dev VM from the cool-tier archive blob created by archive-to-cool.ps1.
.DESCRIPTION
    Flow: copy the archived BLOCK blob back to a temporary PAGE blob (managed-disk import
    needs a page-blob VHD) -> az disk create from the blob -> az vm create --attach-os-disk
    onto the preserved NIC -> re-enable auto-shutdown -> delete the temporary page blob.
    The original cool archive blob is kept until you delete it manually (safety net).
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-winonlinux-dev',
    [string]$VmName = 'vm-wol-dev',
    [string]$Container = 'vm-archive',
    [string]$StorageAccount = ''
)
# VM size comes from the restore metadata blob written at archive time.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Assert-Subscription

if (-not $StorageAccount) { $StorageAccount = Get-ArchiveStorageAccountName }
$blobName = "$VmName-osdisk.vhd"
$restoreBlobName = "$VmName-osdisk-restore.vhd"
$metaBlobName = "$VmName-restore-info.json"
$key = az storage account keys list -g $ResourceGroup -n $StorageAccount --query '[0].value' -o tsv
if ($LASTEXITCODE -ne 0 -or -not $key) { throw "Storage account '$StorageAccount' not found in '$ResourceGroup'." }

Write-Host 'Reading restore metadata...'
$metaPath = Join-Path ([System.IO.Path]::GetTempPath()) $metaBlobName
Invoke-Az @('storage', 'blob', 'download', '--account-name', $StorageAccount, '--account-key', $key,
    '-c', $Container, '-n', $metaBlobName, '-f', $metaPath, '--overwrite', '-o', 'none')
$meta = Get-Content $metaPath -Raw | ConvertFrom-Json
Remove-Item $metaPath -Force
Write-Host "  VM $($meta.vmName) | size $($meta.vmSize) | disk $($meta.diskSizeGb) GB $($meta.diskSku) | gen $($meta.hyperVGeneration)"

Write-Host 'Copying archive block blob back to a temporary page blob (managed-disk import format)...'
Invoke-Az @('storage', 'blob', 'copy', 'start', '--account-name', $StorageAccount, '--account-key', $key,
    '--source-container', $Container, '--source-blob', $blobName,
    '--destination-container', $Container, '--destination-blob', $restoreBlobName,
    '--destination-blob-type', 'PageBlob', '-o', 'none')
Wait-BlobCopy -AccountName $StorageAccount -AccountKey $key -Container $Container -BlobName $restoreBlobName

Write-Host "Creating managed disk '$($meta.diskName)' from the page blob..."
$saId = az storage account show -g $ResourceGroup -n $StorageAccount --query 'id' -o tsv
$blobUrl = "https://$StorageAccount.blob.core.windows.net/$Container/$restoreBlobName"
Invoke-Az @('disk', 'create', '-g', $ResourceGroup, '-n', $meta.diskName,
    '--source', $blobUrl, '--source-storage-account-id', $saId,
    '--os-type', 'Linux', '--hyper-v-generation', $meta.hyperVGeneration,
    '--sku', $meta.diskSku, '-o', 'none')

Write-Host "Recreating VM '$VmName' on the preserved NIC..."
Invoke-Az @('vm', 'create', '-g', $ResourceGroup, '-n', $VmName,
    '--attach-os-disk', $meta.diskName, '--os-type', 'linux',
    '--size', $meta.vmSize, '--nics', $meta.nicId, '-o', 'none')

Write-Host 'Re-enabling daily auto-shutdown (19:00 Mountain = 01:00 UTC)...'
Invoke-Az @('vm', 'auto-shutdown', '-g', $ResourceGroup, '-n', $VmName, '--time', '0100', '-o', 'none')

Write-Host 'Deleting the temporary page blob...'
Invoke-Az @('storage', 'blob', 'delete', '--account-name', $StorageAccount, '--account-key', $key,
    '-c', $Container, '-n', $restoreBlobName, '-o', 'none')

$publicIp = az vm list-ip-addresses -g $ResourceGroup -n $VmName --query '[0].virtualMachine.network.publicIpAddresses[0].ipAddress' -o tsv
Write-Host ''
Write-Host "=== Restored. RDP: mstsc /v:$publicIp ==="
Write-Host "The cool archive blob '$blobName' was kept as a safety net. Once the VM checks out, delete it with:"
Write-Host "  az storage blob delete --account-name $StorageAccount -c $Container -n $blobName --account-key <key>"
