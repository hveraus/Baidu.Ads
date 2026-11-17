param([Parameter(Mandatory=$true)][string]$SecretConfig)
$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install this service with uv sync --frozen first.' }
$config = Import-Clixml -LiteralPath $SecretConfig
$secretNames = @('MCP_READER_OWNER_HASH', 'MCP_READER_VERSION_KEY', 'MCP_READER_SERVICE_TOKEN', 'MCP_READER_CLI_KEY', 'MCP_READER_WECOM_KEY')
$pathNames = @('MCP_READER_STORAGE', 'MCP_READER_USERS_DB')
foreach ($name in $pathNames) {
    if (-not $config[$name] -or -not [IO.Path]::IsPathRooted($config[$name])) { throw 'Reader paths must be explicitly configured absolute paths.' }
}
foreach ($name in $secretNames[0..2]) {
    if (-not ($config[$name] -is [Security.SecureString])) { throw 'Required reader credentials must be user-scoped Windows DPAPI SecureStrings.' }
}
$previous = @{}
try {
    foreach ($name in ($pathNames + $secretNames)) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        $value = $config[$name]
        if ($name -in $secretNames -and $null -ne $value) {
            if (-not ($value -is [Security.SecureString])) { throw 'Plaintext reader secrets are not accepted.' }
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
            try { [Environment]::SetEnvironmentVariable($name, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), 'Process') }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        } else { [Environment]::SetEnvironmentVariable($name, $value, 'Process') }
    }
    & $python (Join-Path $PSScriptRoot 'reader.py')
    $result = $LASTEXITCODE
} finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    $config = $null
}
exit $result
