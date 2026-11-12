[CmdletBinding()]
param(
    [switch]$EnableHttps,
    [string]$NginxRoot = 'C:\SSW\Nginx',
    [string]$CertificateDirectory = 'C:\SSW\Certificate\BaiduAds'
)

$ErrorActionPreference = 'Stop'
$nginx = Join-Path $NginxRoot 'nginx.exe'
$main = Join-Path $NginxRoot 'conf\nginx.conf'
$siteFolder = Join-Path $NginxRoot 'conf\baidu-ads-login'
$site = Join-Path $siteFolder 'site.conf'
$fullchain = Join-Path $CertificateDirectory 'fullchain.pem'
$privateKey = Join-Path $CertificateDirectory 'private.pem'
$webroot = (Join-Path $NginxRoot 'acme').Replace('\', '/')

if (-not (Test-Path -LiteralPath $nginx -PathType Leaf) -or -not (Test-Path -LiteralPath $main -PathType Leaf)) {
    throw 'Could not locate nginx.exe and conf/nginx.conf.'
}
if ($EnableHttps -and (-not (Test-Path -LiteralPath $fullchain -PathType Leaf) -or
                       -not (Test-Path -LiteralPath $privateKey -PathType Leaf))) {
    throw 'The dashboard certificate and private key must be present before enabling HTTPS.'
}
$httpTarget = 'return 503;'
if ($EnableHttps) {
    $httpTarget = 'return 301 https://ads.ssw.com.cn$request_uri;'
}
$siteConfig = @'
server {
    listen 80;
    server_name ads.ssw.com.cn;
    location ^~ /.well-known/acme-challenge/ {
        root "WEBROOT";
        default_type text/plain;
        try_files $uri =404;
    }
    location / {
        HTTP_TARGET
    }
}
'@
$siteConfig = $siteConfig.Replace('WEBROOT', $webroot).Replace('HTTP_TARGET', $httpTarget)
if ($EnableHttps) {
    $httpsConfig = @'
server {
    listen 443 ssl;
    server_name ads.ssw.com.cn;
    ssl_certificate "FULLCHAIN";
    ssl_certificate_key "PRIVATE_KEY";
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    error_log logs/baidu-ads-login.error.log crit;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host ads.ssw.com.cn;
        proxy_set_header Connection "";
        error_page 502 504 =503 @starting;
    }
    location @starting {
        default_type text/plain;
        add_header Cache-Control "no-store" always;
        return 503 "Dashboard sign-in is being configured.";
    }
}
'@
    $siteConfig += "`r`n" + $httpsConfig.Replace('FULLCHAIN', $fullchain.Replace('\', '/')).Replace('PRIVATE_KEY', $privateKey.Replace('\', '/'))
}

$originalMain = [IO.File]::ReadAllBytes($main)
# Preserve the existing configuration bytes, including comments in other encodings.
$byteEncoding = [Text.Encoding]::GetEncoding(28591)
$updated = $byteEncoding.GetString($originalMain)
$include = 'include baidu-ads-login/site.conf;'
$hasInclude = $updated.Contains($include)
if (-not $hasInclude) {
    $http = [regex]::Match($updated, '(?m)^[ \t]*http[ \t]*\{[ \t]*(?=\r?$)')
    if (-not $http.Success -or $updated.Contains('ads.ssw.com.cn')) {
        throw 'Review the existing nginx.conf before adding a dashboard include.'
    }
    $updated = $updated.Insert($http.Index + $http.Length, "`r`n    $include")
}
$originalSite = $null
if (Test-Path -LiteralPath $site -PathType Leaf) {
    $originalSite = [IO.File]::ReadAllBytes($site)
}
$null = New-Item -ItemType Directory -Force -Path $siteFolder
Push-Location -LiteralPath $NginxRoot
try {
    [IO.File]::WriteAllText($site, $siteConfig + "`r`n", [Text.Encoding]::ASCII)
    if (-not $hasInclude) { [IO.File]::WriteAllBytes($main, $byteEncoding.GetBytes($updated)) }
    $check = Start-Process -FilePath $nginx -ArgumentList '-t' -NoNewWindow -Wait -PassThru
    if ($check.ExitCode -ne 0) { throw 'Nginx rejected the new configuration.' }
    $reload = Start-Process -FilePath $nginx -ArgumentList '-s', 'reload' -NoNewWindow -Wait -PassThru
    if ($reload.ExitCode -ne 0) { throw 'Nginx reload failed.' }
} catch {
    if (-not $hasInclude) { [IO.File]::WriteAllBytes($main, $originalMain) }
    if ($null -ne $originalSite) {
        [IO.File]::WriteAllBytes($site, $originalSite)
    } else {
        Remove-Item -LiteralPath $site -ErrorAction SilentlyContinue
    }
    throw
} finally {
    Pop-Location
}
if ($EnableHttps) { 'Dashboard HTTPS proxy configured.' } else { 'Dashboard ACME HTTP route configured.' }
