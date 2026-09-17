# Shared helpers for the WinOnLinux dev VM scripts. Dot-source, do not run directly.

$script:ExpectedSubscriptionId = '7a713a84-2144-4ea1-8e85-9c63111bd995'  # AZ-PRI-01

function Assert-Subscription {
    $sub = az account show --query id -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $sub) {
        throw "az CLI is not logged in. Run 'az login' first."
    }
    if ($sub -ne $script:ExpectedSubscriptionId) {
        throw "Wrong subscription '$sub'. Expected AZ-PRI-01 ($script:ExpectedSubscriptionId). Run: az account set -s $script:ExpectedSubscriptionId"
    }
}

function Invoke-Az {
    # Runs az, throws on failure, returns stdout.
    param([Parameter(Mandatory)][string[]]$Arguments)
    $out = az @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "az $($Arguments -join ' ') failed:`n$out"
    }
    return $out
}

function Get-OsDiskName {
    param([string]$ResourceGroup, [string]$VmName)
    $name = az vm show -g $ResourceGroup -n $VmName --query 'storageProfile.osDisk.name' -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $name) {
        throw "Could not find VM '$VmName' in resource group '$ResourceGroup'."
    }
    return $name
}

function Get-ArchiveStorageAccountName {
    # Globally-unique-but-deterministic name derived from the subscription id.
    $suffix = ($script:ExpectedSubscriptionId -replace '-', '').Substring(0, 12)
    return "stwolarc$suffix"
}

function Wait-BlobCopy {
    param(
        [string]$AccountName, [string]$AccountKey,
        [string]$Container, [string]$BlobName,
        [int]$TimeoutMinutes = 90
    )
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ($true) {
        $status = az storage blob show --account-name $AccountName --account-key $AccountKey `
            -c $Container -n $BlobName --query 'properties.copy.status' -o tsv
        if ($status -eq 'success') { return }
        if ($status -in @('failed', 'aborted')) { throw "Blob copy of '$BlobName' ended with status: $status" }
        if ((Get-Date) -gt $deadline) { throw "Blob copy of '$BlobName' timed out after $TimeoutMinutes minutes (status: $status)." }
        Write-Host "  copy status: $status ... waiting 30s"
        Start-Sleep -Seconds 30
    }
}
