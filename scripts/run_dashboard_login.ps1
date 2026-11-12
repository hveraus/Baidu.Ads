[CmdletBinding()]
param([switch]$Initialize)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$private = Join-Path $env:LOCALAPPDATA 'Baidu.Ads'
$loginPath = Join-Path $private 'dashboard-login.xml'
$wecomPath = Join-Path $private 'wecom-consultations.xml'
$python = Join-Path $root '.venv\Scripts\python.exe'

function Read-PasswordField([string]$Message) {
    $credential = Get-Credential -UserName 'Baidu.Ads' -Message $Message
    if (-not $credential -or -not $credential.Password.Length) { throw 'Setup cancelled.' }
    return $credential.Password
}

if ($Initialize) {
    if (-not (Test-Path -LiteralPath $wecomPath)) {
        throw 'Run setup under the Windows account that runs the SSW WeCom collector.'
    }
    $clientId = (Read-Host 'GitHub OAuth Client ID').Trim()
    if (-not $clientId) { throw 'The GitHub OAuth Client ID is required.' }
    $clientSecret = Read-PasswordField 'Enter the GitHub OAuth Client Secret in the password field'
    $posthogKey = Read-PasswordField 'Enter the existing Tina CLI viewing key in the password field'
    if ($posthogKey.Length -ne 64) { throw 'The Tina CLI viewing key must have 64 hexadecimal characters.' }
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
    @{
        BAIDU_ADS_GITHUB_CLIENT_ID = $clientId
        BAIDU_ADS_GITHUB_CLIENT_SECRET = $clientSecret
        POSTHOG_CLI_SNAPSHOT_KEY = $posthogKey
        BAIDU_ADS_SESSION_KEY = $sessionKey
    } | Export-Clixml -Path $loginPath
    'Dashboard login settings saved for this Windows account.'
    exit 0
}

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
    & $python (Join-Path $root 'login_dashboard.py')
    $exitCode = $LASTEXITCODE
} finally {
    foreach ($name in $config.Keys) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
}
exit $exitCode
