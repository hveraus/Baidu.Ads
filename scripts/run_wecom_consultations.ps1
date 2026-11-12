# Run with Windows PowerShell under the Windows account assigned to the scheduled task.
[CmdletBinding(DefaultParameterSetName = 'Publish')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Initialize')]
    [switch]$Initialize,
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
    $config = @{
        WECOM_CORP_ID = Read-Host 'WeCom CorpID'
        WECOM_OPEN_KFID = Read-Host 'SSW website customer service account open_kfid'
        WECOM_APP_SECRET = Read-Secret 'Enter the SSW customer service app Secret in the password field'
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
