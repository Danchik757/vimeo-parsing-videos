param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$RunRoot = "",
    [string]$ConfigPath = "",
    [string]$StorageDownloadedRoot = "\\sciencestorage\cc\vimeo_Datasets\downloaded",
    [switch]$SkipOffload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $RunRoot) {
    $RunRoot = Join-Path $RepoRoot "output\runs\pc10\windows-2workers-500-email"
}

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $RepoRoot "configs\windows\config.windows.pc10.2workers.email.local.json"
}

$PythonExe = Join-Path $RepoRoot "venv\Scripts\python.exe"
$OffloadScript = Join-Path $RepoRoot "scripts\offload_downloads.py"
$RegistryPath = Join-Path $RunRoot "offload_registry.json"
$OffloadLogPath = Join-Path $RunRoot "offload.log"

function Show-DriveSummary {
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    [pscustomobject]@{
        DeviceID = $disk.DeviceID
        SizeGB   = [math]::Round($disk.Size / 1GB, 2)
        FreeGB   = [math]::Round($disk.FreeSpace / 1GB, 2)
        UsedGB   = [math]::Round(($disk.Size - $disk.FreeSpace) / 1GB, 2)
    } | Format-Table -AutoSize
}

function Get-DirectorySizeGB([string]$Path) {
    if (-not (Test-Path $Path)) {
        return 0
    }
    $sum = (Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    if ($null -eq $sum) {
        return 0
    }
    return [math]::Round($sum / 1GB, 2)
}

function Show-RunDirectorySizes {
    param([string]$Root)
    $rows = foreach ($name in @("videos", "shards", "workers", "jsons")) {
        $path = Join-Path $Root $name
        [pscustomobject]@{
            Dir    = $path
            SizeGB = Get-DirectorySizeGB $path
        }
    }
    $rows | Format-Table -AutoSize
}

function Show-RegistrySummary {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        Write-Host "Registry not found: $Path"
        return
    }

    $raw = Get-Content $Path -Raw
    if (-not $raw.Trim()) {
        Write-Host "Registry is empty: $Path"
        return
    }

    $registryObject = $raw | ConvertFrom-Json
    $items = @($registryObject.PSObject.Properties | ForEach-Object { $_.Value })
    $uploaded = @($items | Where-Object { $_.status -eq "uploaded" })
    $uploadedBytes = ($uploaded | Measure-Object file_size_bytes -Sum).Sum
    if ($null -eq $uploadedBytes) {
        $uploadedBytes = 0
    }

    [pscustomobject]@{
        RegistryPath      = $Path
        TotalItems        = $items.Count
        UploadedItems     = $uploaded.Count
        UploadedSizeGB    = [math]::Round($uploadedBytes / 1GB, 2)
        LocalDeletedItems = (@($uploaded | Where-Object { $_.local_video_deleted }).Count)
    } | Format-Table -AutoSize
}

function Stop-BrowserProcesses {
    $processes = Get-Process chrome, chromedriver -ErrorAction SilentlyContinue
    if ($null -eq $processes -or $processes.Count -eq 0) {
        Write-Host "No chrome/chromedriver processes found."
        return
    }
    $processes | Format-Table Id, ProcessName, StartTime -AutoSize
    $processes | Stop-Process -Force
    Write-Host "Stopped chrome/chromedriver processes."
}

Write-Host "== Before cleanup =="
Show-DriveSummary
Show-RunDirectorySizes -Root $RunRoot
Write-Host "Storage available:" (Test-Path $StorageDownloadedRoot)
Show-RegistrySummary -Path $RegistryPath

if (Test-Path $OffloadLogPath) {
    Write-Host "== Last offload log lines =="
    Get-Content $OffloadLogPath -Tail 20
}

Write-Host "== Browser cleanup =="
Stop-BrowserProcesses

if (-not $SkipOffload) {
    if (-not (Test-Path $PythonExe)) {
        throw "Python not found: $PythonExe"
    }
    if (-not (Test-Path $OffloadScript)) {
        throw "Offload script not found: $OffloadScript"
    }
    if (-not (Test-Path $ConfigPath)) {
        throw "Config not found: $ConfigPath"
    }

    Write-Host "== Running one-shot offloader with local delete =="
    Push-Location $RepoRoot
    try {
        & $PythonExe $OffloadScript --config $ConfigPath --delete-local-video
        if ($LASTEXITCODE -ne 0) {
            throw "Offloader exited with code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "== After cleanup =="
Show-DriveSummary
Show-RunDirectorySizes -Root $RunRoot
Show-RegistrySummary -Path $RegistryPath
