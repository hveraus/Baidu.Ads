param(
    [Parameter(Mandatory=$true)][string]$ReleaseCopy,
    [Parameter(Mandatory=$true)][string]$HttpConfig,
    [Parameter(Mandatory=$true)][string]$SecretConfig,
    [ValidateSet('web','worker')][string]$Mode = 'web'
)
$ErrorActionPreference = 'Stop'
$serviceRoot = $PSScriptRoot
$python = Join-Path $serviceRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install this service with uv sync --frozen first.' }
$releasePath = (Resolve-Path -LiteralPath $ReleaseCopy).Path
$httpPath = (Resolve-Path -LiteralPath $HttpConfig).Path
$secrets = Import-Clixml -LiteralPath $SecretConfig
if (-not ($secrets.MCP_BRIDGE_SERVICE_TOKEN -is [Security.SecureString])) {
    throw 'The bridge credential must be a user-scoped Windows DPAPI SecureString.'
}
$previous = @{}
try {
    foreach ($name in @('MCP_BRIDGE_SERVICE_TOKEN','MCP_BAIDU_WRITE_CREDENTIALS_JSON')) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        $value = $secrets[$name]
        if ($null -ne $value) {
            if (-not ($value -is [Security.SecureString])) { throw 'Plaintext service secrets are not accepted.' }
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
            try { [Environment]::SetEnvironmentVariable($name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process') }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        } else { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    }
    Push-Location $serviceRoot
    try {
        if ($Mode -eq 'web') {
            & $python -m baidu_ads_mcp.server --release-copy $releasePath --http-config $httpPath
        } else {
            & $python -m baidu_ads_mcp.worker --config $httpPath
        }
        $result = $LASTEXITCODE
    } finally { Pop-Location }
} finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    $secrets = $null
}
exit $result
