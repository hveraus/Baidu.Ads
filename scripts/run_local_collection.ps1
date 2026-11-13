[CmdletBinding()]
param(
    [switch]$Initialize,
    [switch]$Seed,
    [switch]$Check,
    [switch]$InstallTask,
    [string]$SiteConfig,
    [string]$OssUri,
    [string]$Ossutil = 'ossutil'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$private = Join-Path $env:LOCALAPPDATA 'Baidu.Ads'
$storage = Join-Path $private 'storage'
$configPath = Join-Path $private 'collection.xml'
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $env:USERPROFILE 'baidu-ads-login\.venv\Scripts\python.exe'
}
$collector = Join-Path $PSScriptRoot 'collect_local.py'

function Read-Secret([string]$Message) {
    $credential = Get-Credential -UserName 'Baidu.Ads' -Message $Message
    if (-not $credential -or -not $credential.Password.Length) { throw 'Setup cancelled.' }
    return $credential.Password
}

if ($Initialize) {
    if (-not $SiteConfig) { $SiteConfig = Read-Host 'Paste the BAIDU_TONGJI_SSW_SITE_JSON repository variable' }
    $null = ConvertFrom-Json -InputObject $SiteConfig
    if ($OssUri -and $OssUri -notmatch '^oss://[a-z0-9-]+/[^\s]+$') {
        throw 'Use an OSS bucket URI with a dedicated backup prefix.'
    }
    $config = @{
        DATA_BACKUP_KEY = Read-Secret 'Enter the existing 64-character Baidu.Ads backup key'
        BAIDU_TONGJI_CREDENTIALS_JSON = Read-Secret 'Enter the Baidu Tongji read credentials JSON'
        POSTHOG_CLI_QUERY_KEY = Read-Secret 'Enter the PostHog project query key'
        BAIDU_TONGJI_SSW_SITE_JSON = $SiteConfig
        OSS_URI = $OssUri
        OSSUTIL = $Ossutil
    }
    $staged = $configPath + '.' + [guid]::NewGuid().ToString('N')
    try {
        $config | Export-Clixml -LiteralPath $staged
        if (Test-Path -LiteralPath $configPath) {
            [IO.File]::Replace($staged, $configPath, [NullString]::Value)
        } else {
            [IO.File]::Move($staged, $configPath)
        }
    } finally {
        if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged }
    }
    'Collection settings saved for this Windows account.'
    exit 0
}

if ($InstallTask) {
    if ((Get-TimeZone).Id -ne 'China Standard Time') { throw 'Expected the Windows server to use Beijing time.' }
    if (-not (Test-Path -LiteralPath (Join-Path $storage 'current'))) { throw 'Seed and verify local storage first.' }
    if (-not (Test-Path -LiteralPath $configPath)) { throw 'Initialize collection settings first.' }
    $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $credential = Get-Credential -UserName $account -Message 'Enter the Windows account password for daily background collection'
    if (-not $credential -or -not $credential.Password.Length) { throw 'Task setup cancelled.' }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -File "' + $PSCommandPath + '"') -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Daily -At '09:00'
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    $null = Register-ScheduledTask -TaskName 'baidu-ads-collect-local' -Action $action -Trigger $trigger -Settings $settings -User $account -Password $credential.GetNetworkCredential().Password -Force
    'Collection task registered for 09:00 Beijing time.'
    exit 0
}

$login = Import-Clixml -LiteralPath (Join-Path $private 'dashboard-login.xml')
$wecom = Import-Clixml -LiteralPath (Join-Path $private 'wecom-consultations.xml')
$settings = @{
    POSTHOG_CLI_SNAPSHOT_KEY = $login.POSTHOG_CLI_SNAPSHOT_KEY
    WECOM_SNAPSHOT_KEY = $wecom.WECOM_SNAPSHOT_KEY
    WECOM_CORP_ID = $wecom.WECOM_CORP_ID
    WECOM_OPEN_KFID = $wecom.WECOM_OPEN_KFID
    WECOM_APP_SECRET = $wecom.WECOM_APP_SECRET
    PYTHONUTF8 = '1'
}
$mode = if ($Check) { 'check' } else { 'seed' }
$argsForPython = @('--storage', $storage)
if (-not ($Seed -or $Check)) {
    $mode = 'collect'
    $config = Import-Clixml -LiteralPath $configPath
    foreach ($name in @('DATA_BACKUP_KEY', 'BAIDU_TONGJI_CREDENTIALS_JSON', 'POSTHOG_CLI_QUERY_KEY', 'BAIDU_TONGJI_SSW_SITE_JSON')) {
        $settings[$name] = $config[$name]
    }
    if ($config.OSS_URI) {
        $argsForPython += @('--oss', $config.OSS_URI, '--ossutil', $config.OSSUTIL)
    }
}

try {
    foreach ($name in $settings.Keys) {
        $value = $settings[$name]
        if ($value -is [Security.SecureString]) {
            $value = [pscredential]::new('Baidu.Ads', $value).GetNetworkCredential().Password
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
    if ($Seed -or $Check) {
        & $python $collector $mode @argsForPython
    } else {
        $log = Join-Path $storage 'collection.log'
        if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 5MB) {
            Move-Item -LiteralPath $log -Destination (Join-Path $storage 'collection.previous.log') -Force
        }
        Out-File -LiteralPath $log -InputObject (Get-Date -Format o) -Append -Encoding utf8
        $ErrorActionPreference = 'Continue'
        & $python $collector $mode @argsForPython 2>&1 | Out-File -LiteralPath $log -Append -Encoding utf8
        $ErrorActionPreference = 'Stop'
    }
    $status = $LASTEXITCODE
} finally {
    foreach ($name in $settings.Keys) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
}
exit $status
