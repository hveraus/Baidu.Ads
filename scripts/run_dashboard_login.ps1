[CmdletBinding()]
param([switch]$Initialize, [switch]$InstallTask, [switch]$UpdatePostHogKey)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$private = Join-Path $env:LOCALAPPDATA 'Baidu.Ads'
$loginPath = Join-Path $private 'dashboard-login.xml'
$wecomPath = Join-Path $private 'wecom-consultations.xml'
$python = Join-Path $root '.venv\Scripts\python.exe'
$appPath = Join-Path $root 'login_dashboard.py'

function Read-PasswordField([string]$Message) {
    $credential = Get-Credential -UserName 'Baidu.Ads' -Message $Message
    if (-not $credential -or -not $credential.Password.Length) { throw 'Setup cancelled.' }
    return $credential
}

function Save-LoginConfig($config) {
    $staged = Join-Path $private ('dashboard-login-' + [guid]::NewGuid().ToString('N') + '.xml')
    try {
        $config | Export-Clixml -Path $staged
        if (Test-Path -LiteralPath $loginPath) {
            [IO.File]::Replace($staged, $loginPath, [NullString]::Value)
        } else {
            [IO.File]::Move($staged, $loginPath)
        }
    } finally {
        if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged }
    }
}

if ($UpdatePostHogKey) {
    if ($Initialize -or $InstallTask) { throw 'Run -UpdatePostHogKey on its own.' }
    $config = Import-Clixml -LiteralPath $loginPath
    $credential = Read-PasswordField 'Enter the 64-character Tina CLI viewing key from Keeper'
    try {
        $key = $credential.GetNetworkCredential().Password.Trim()
        if ($key -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'Enter a 64-character hexadecimal viewing key.' }
        $key | & $python $appPath --check-posthog-key
        if ($LASTEXITCODE -ne 0) { throw 'Tina CLI key validation failed. The previous settings are still in place.' }
        $config.POSTHOG_CLI_SNAPSHOT_KEY = ConvertTo-SecureString $key -AsPlainText -Force
        Save-LoginConfig $config
    } finally {
        $key = $credential = $null
    }
    $taskName = 'baidu-ads-dashboard-login'
    if ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') {
        Stop-ScheduledTask -TaskName $taskName
    }
    Start-ScheduledTask -TaskName $taskName
    'Tina CLI key saved and dashboard task restarted.'
    exit 0
}

if ($Initialize) {
    if (-not (Test-Path -LiteralPath $wecomPath)) {
        throw 'Run setup under the Windows account that runs the SSW WeCom collector.'
    }
    $username = (Read-Host 'Dashboard username [ken]').Trim()
    if (-not $username) { $username = 'ken' }
    if ($username -cnotmatch '^[A-Za-z0-9_.@-]{1,64}$') {
        throw 'Use up to 64 letters, numbers, dots, underscores, at signs or hyphens.'
    }
    $previous = $null
    if (Test-Path -LiteralPath $loginPath) {
        $previous = Import-Clixml -LiteralPath $loginPath
    }
    $posthogKey = $previous.POSTHOG_CLI_SNAPSHOT_KEY
    if ($null -eq $posthogKey) {
        $posthogKey = (Read-PasswordField 'Enter the existing Tina CLI viewing key in the password field').Password
    }
    if ($posthogKey.Length -ne 64) { throw 'The Tina CLI viewing key must have 64 hexadecimal characters.' }
    $first = Read-PasswordField 'Enter your new dashboard password (at least 15 characters)'
    $second = Read-PasswordField 'Confirm your new dashboard password'
    $encoding = $OutputEncoding
    try {
        $password = $first.GetNetworkCredential().Password
        if (-not [string]::Equals($password, $second.GetNetworkCredential().Password, [StringComparison]::Ordinal)) {
            throw 'The passwords did not match.'
        }
        $OutputEncoding = New-Object Text.UTF8Encoding($false)
        $passwordHash = $password | & $python $appPath --hash-password
        if ($LASTEXITCODE -ne 0 -or $passwordHash -cnotmatch '^[0-9a-f]{32}:[0-9a-f]{64}$') {
            throw 'Could not prepare the dashboard password.'
        }
    } finally {
        $OutputEncoding = $encoding
        $password = $first = $second = $null
    }
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 32
    try {
        $random.GetBytes($bytes)
        $hex = [BitConverter]::ToString($bytes).Replace('-', '')
        $sessionKey = ConvertTo-SecureString $hex -AsPlainText -Force
    } finally {
        [Array]::Clear($bytes, 0, $bytes.Length)
        $random.Dispose()
    }
    Save-LoginConfig @{
        BAIDU_ADS_LOGIN_USERNAME = $username
        BAIDU_ADS_PASSWORD_HASH = $passwordHash
        POSTHOG_CLI_SNAPSHOT_KEY = $posthogKey
        BAIDU_ADS_SESSION_KEY = $sessionKey
    }
    "Dashboard login settings saved. Username: $username"
}

if ($InstallTask) {
    $config = Import-Clixml -LiteralPath $loginPath
    if (-not $config.BAIDU_ADS_PASSWORD_HASH) { throw 'Initialize the dashboard password first.' }
    $taskName = 'baidu-ads-dashboard-login'
    $account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $credential = Get-Credential -UserName $account -Message 'Enter the Windows account password for automatic dashboard startup'
    if (-not $credential -or -not $credential.Password.Length) { throw 'Task setup cancelled.' }
    $arguments = '-NoProfile -NonInteractive -File "' + $PSCommandPath + '"'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $windowsPassword = $credential.GetNetworkCredential().Password
    try {
        $null = Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User $account -Password $windowsPassword -Force
    } finally {
        $windowsPassword = $credential = $null
    }
    if ((Get-ScheduledTask -TaskName $taskName).State -eq 'Running') {
        Stop-ScheduledTask -TaskName $taskName
    }
    Start-ScheduledTask -TaskName $taskName
    'Dashboard startup task registered and started.'
    exit 0
}

if ($Initialize) { exit 0 }

$config = Import-Clixml -LiteralPath $loginPath
$config.WECOM_SNAPSHOT_KEY = (Import-Clixml -LiteralPath $wecomPath).WECOM_SNAPSHOT_KEY
try {
    foreach ($name in $config.Keys) {
        $value = $config[$name]
        if ($value -is [Security.SecureString]) {
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
            try {
                [Environment]::SetEnvironmentVariable($name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process')
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
            }
        } else {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
    & $python $appPath
    $exitCode = $LASTEXITCODE
} finally {
    foreach ($name in $config.Keys) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
}
exit $exitCode
