# Run with Windows PowerShell under the Windows account assigned to the scheduled task.
[CmdletBinding(DefaultParameterSetName = 'Publish')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Initialize')]
    [switch]$Initialize,
    [Parameter(ParameterSetName = 'Initialize')]
    [string]$CorpId,
    [Parameter(Mandatory, ParameterSetName = 'Check')]
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$configPath = Join-Path $env:LOCALAPPDATA 'Baidu.Ads\wecom-consultations.xml'
$collector = Join-Path $PSScriptRoot 'sync_wecom_consultations.py'
$python = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'

function Read-Secret([string]$Message) {
    $credential = Get-Credential -UserName 'Baidu.Ads' -Message $Message
    if (-not $credential) { throw 'Setup cancelled.' }
    return $credential.Password
}

if ($Initialize) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if (-not $CorpId) { $CorpId = (Read-Host 'WeCom CorpID').Trim() }
    $appSecret = Read-Secret 'Enter the SSW customer service app Secret in the password field'
    $base = 'https://qyapi.weixin.qq.com/cgi-bin'
    $secret = [Uri]::EscapeDataString(([pscredential]::new('WeCom', $appSecret)).GetNetworkCredential().Password.Trim())
    $encodedCorp = [Uri]::EscapeDataString($CorpId)
    try {
        $auth = Invoke-RestMethod -Uri "$base/gettoken?corpid=$encodedCorp&corpsecret=$secret" -TimeoutSec 20
        if ($auth.errcode -ne 0) { throw "WeCom gettoken errcode=$($auth.errcode)" }
        $accounts = Invoke-RestMethod -Uri "$base/kf/account/list?access_token=$($auth.access_token)" -TimeoutSec 20
        if ($accounts.errcode -ne 0) { throw "WeCom account/list errcode=$($accounts.errcode)" }
    } catch {
        if ($_.Exception.Message -match '^WeCom (gettoken|account/list) errcode=\d+$') { throw }
        throw 'Could not contact the WeCom API from this server.'
    } finally {
        Remove-Variable secret,auth -ErrorAction SilentlyContinue
    }
    $selectedAccounts = @($accounts.account_list | Where-Object { $_.open_kfid -and ($_.name -replace '[^a-z0-9]', '') -eq 'sswchina' })
    if ($selectedAccounts.Count -ne 1) { throw 'Expected exactly one SSW China customer service account.' }
    'Found the SSW China customer service account.'
    $config = @{
        WECOM_CORP_ID = $CorpId
        WECOM_OPEN_KFID = $selectedAccounts[0].open_kfid
        WECOM_APP_SECRET = $appSecret
        WECOM_SNAPSHOT_KEY = Read-Secret 'Enter the private viewing key (64 hexadecimal characters) in the password field'
        WECOM_GITHUB_TOKEN = Read-Secret 'Enter the GitHub token for hveraus/Baidu.Ads in the password field'
    }
    if (-not $config.WECOM_CORP_ID.Trim() -or -not $config.WECOM_OPEN_KFID.Trim() -or
        -not $config.WECOM_APP_SECRET.Length -or $config.WECOM_SNAPSHOT_KEY.Length -ne 64 -or
        -not $config.WECOM_GITHUB_TOKEN.Length) {
        throw 'Enter all settings and a 64-character viewing key.'
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $configPath) | Out-Null
    $config | Export-Clixml -Path $configPath
    'WeCom settings saved for this Windows account.'
    exit 0
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Create the repository virtual environment and install requirements.txt before running the collector.'
}

$config = Import-Clixml -LiteralPath $configPath
$previous = @{}
$mode = if ($Check) { '--check' } else { '--publish' }
try {
    foreach ($name in $config.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        $value = $config[$name]
        if ($value -is [Security.SecureString]) {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
            try {
                [Environment]::SetEnvironmentVariable($name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer), 'Process')
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            }
        } else {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
    & $python $collector $mode
    $status = $LASTEXITCODE
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
exit $status
