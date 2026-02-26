#Requires -Version 2.0
<#
.SYNOPSIS
    API Client - works on ALL Windows versions (7, 8, 10, 11) with PowerShell 2.0+.
    No curl required. Uses built-in .NET HTTP client automatically.

.NOTES
    If you get an "Execution Policy" error, run:
        powershell.exe -ExecutionPolicy Bypass -File api_client.ps1 [arguments]
#>

param(
    [string]$command,
    [string]$host_url,
    [string]$file,
    [string]$email,
    [string]$phone,
    [string]$so,
    [string]$subject,
    [string]$body,
    [string]$client_name,
    [string]$business_name,
    [string]$business_email,
    [string]$message,
    [string]$url
)

# ---------------------------------------------------------------
# Infer command if not provided
# ---------------------------------------------------------------
if (-not $command) {
    if ($phone)     { $command = "sign-and-sms" }
    elseif ($email) { $command = "sign-and-email" }
    else            { $command = $null }
}

# ---------------------------------------------------------------
# Show help if no command
# ---------------------------------------------------------------
if (-not $command) {
    Write-Host "Usage: api_client.ps1 -command COMMAND [options]"
    Write-Host ""
    Write-Host "Commands: send-email, send-sms, sign-and-email, sign-and-sms, verify"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  -host_url       Base URL (default: http://localhost:8000)"
    Write-Host "  -file           PDF file path (required)"
    Write-Host "  -email          Recipient email"
    Write-Host "  -phone          Recipient phone"
    Write-Host "  -so             Service order code"
    Write-Host "  -subject        Email subject"
    Write-Host "  -body           Email body"
    Write-Host "  -client_name    Client name"
    Write-Host "  -business_name  Business name"
    Write-Host "  -business_email Business email"
    Write-Host "  -message        SMS message"
    Write-Host ""
    Write-Host "Example:"
    Write-Host "  .\api_client.ps1 -command sign-and-email -host_url https://server.com -file doc.pdf -email a@b.com -so SO-1"
    Write-Host ""
    Write-Host "NOTE: If you get an execution policy error, run:"
    Write-Host "  powershell.exe -ExecutionPolicy Bypass -File api_client.ps1 [args]"
    exit 1
}

# ---------------------------------------------------------------
# Build URL
# ---------------------------------------------------------------
$host_url = $host_url.TrimEnd("/")
if (-not $url) {
    switch ($command) {
        "send-email"     { $url = "$host_url/api/v1/documents/send-email" }
        "send-sms"       { $url = "$host_url/api/v1/documents/send-sms" }
        "sign-and-email" { $url = "$host_url/api/v1/documents/sign-and-email" }
        "sign-and-sms"   { $url = "$host_url/api/v1/documents/sign-and-sms" }
        "verify"         { $url = "$host_url/api/v1/documents/verify-signature" }
        default          { Write-Host "Error: Unknown command '$command'"; exit 1 }
    }
}

# ---------------------------------------------------------------
# Validate file
# ---------------------------------------------------------------
if (-not $file)              { Write-Host "Error: Missing -file"; exit 1 }
if (-not (Test-Path $file))  { Write-Host "Error: File not found: $file"; exit 1 }
$file = (Resolve-Path $file).Path

# ---------------------------------------------------------------
# Validate required fields
# ---------------------------------------------------------------
if (@("send-email","sign-and-email") -contains $command -and -not $email) { Write-Host "Error: Missing -email"; exit 1 }
if (@("send-sms","sign-and-sms")     -contains $command -and -not $phone) { Write-Host "Error: Missing -phone"; exit 1 }

# ---------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------
Write-Host ""
Write-Host "Sending [$command] to $url ..."
Write-Host "  file           = $file"
if ($email)          { Write-Host "  email          = $email" }
if ($phone)          { Write-Host "  phone          = $phone" }
if ($so)             { Write-Host "  so             = $so" }
if ($client_name)    { Write-Host "  client_name    = $client_name" }
if ($business_name)  { Write-Host "  business_name  = $business_name" }
if ($business_email) { Write-Host "  business_email = $business_email" }
if ($message)        { Write-Host "  message        = $message" }
Write-Host ""

# ---------------------------------------------------------------
# ServicePointManager – must be configured BEFORE any HTTP request.
#
# Fix 1: Expect100Continue = $false
#   Prevents .NET from sending "Expect: 100-continue" before the body.
#   Many servers (nginx, gunicorn, etc.) drop the connection on that header,
#   causing "The underlying connection was closed: An unexpected error
#   occurred on a send."
#
# Fix 2: TLS fallback chain
#   Try TLS 1.3+1.2+1.1+1.0 down to TLS 1.0 alone so the script works on
#   Windows 7 (.NET 3.5, max TLS 1.0) through Windows 11 (TLS 1.3).
#   Numeric values avoid enum-resolution errors on old .NET versions.
#       TLS 1.3 = 12288, TLS 1.2 = 3072, TLS 1.1 = 768, TLS 1.0 = 192
#
# Fix 3: CheckCertificateRevocationList = $false
#   Revocation checks can time-out / fail in air-gapped or corp-proxy
#   environments and appear as "unexpected error on send".
# ---------------------------------------------------------------
try { [System.Net.ServicePointManager]::Expect100Continue = $false } catch { }

$tlsCandidates = @(
    (12288 -bor 3072 -bor 768 -bor 192),   # TLS 1.3 + 1.2 + 1.1 + 1.0
    (3072  -bor 768  -bor 192),             # TLS 1.2 + 1.1 + 1.0
    3072,                                    # TLS 1.2 only
    (768   -bor 192),                        # TLS 1.1 + 1.0
    192                                      # TLS 1.0 only  (Win 7 fallback)
)
foreach ($tlsVal in $tlsCandidates) {
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]$tlsVal
        break
    } catch { }
}

try { [System.Net.ServicePointManager]::CheckCertificateRevocationList = $false } catch { }

# ---------------------------------------------------------------
# Build form fields for the given command
# ---------------------------------------------------------------
$formFields = @{}
switch ($command) {
    "send-email" {
        $formFields = @{
            email          = $email
            so             = $so
            subject        = $subject
            body           = $body
            business_name  = $business_name
            business_email = $business_email
        }
    }
    "send-sms" {
        $formFields = @{ phone = $phone; message = $message }
    }
    "sign-and-email" {
        $formFields = @{
            email          = $email
            so             = $so
            subject        = $subject
            body           = $body
            client_name    = $client_name
            business_name  = $business_name
            business_email = $business_email
        }
    }
    "sign-and-sms" {
        $formFields = @{ phone = $phone; message = $message }
    }
    "verify" {
        $formFields = @{}
    }
}

# ---------------------------------------------------------------
# Send multipart/form-data POST using pure .NET (no curl needed)
# Compatible with PowerShell 2.0 / .NET 2.0 / Windows 7 and above
# ---------------------------------------------------------------
$boundary = "----FormBoundary" + [System.Guid]::NewGuid().ToString("N")
$CRLF     = "`r`n"
$encoding  = [System.Text.Encoding]::UTF8
$ms        = New-Object System.IO.MemoryStream

# Write text fields
foreach ($key in $formFields.Keys) {
    $val = $formFields[$key]
    if ($val -ne $null -and $val -ne "") {
        $part  = "--$boundary$CRLF"
        $part += "Content-Disposition: form-data; name=`"$key`"$CRLF$CRLF"
        $part += "$val$CRLF"
        $bytes = $encoding.GetBytes($part)
        $ms.Write($bytes, 0, $bytes.Length)
    }
}

# Write file field (binary)
$fileName    = [System.IO.Path]::GetFileName($file)
$fileHeader  = "--$boundary$CRLF"
$fileHeader += "Content-Disposition: form-data; name=`"file`"; filename=`"$fileName`"$CRLF"
$fileHeader += "Content-Type: application/octet-stream$CRLF$CRLF"
$headerBytes = $encoding.GetBytes($fileHeader)
$ms.Write($headerBytes, 0, $headerBytes.Length)

$fileBytes = [System.IO.File]::ReadAllBytes($file)
$ms.Write($fileBytes, 0, $fileBytes.Length)

$crlfBytes = $encoding.GetBytes($CRLF)
$ms.Write($crlfBytes, 0, $crlfBytes.Length)

$closingBytes = $encoding.GetBytes("--$boundary--$CRLF")
$ms.Write($closingBytes, 0, $closingBytes.Length)

$requestBody = $ms.ToArray()
$ms.Close()

# ---------------------------------------------------------------
# Send request – with automatic retry on transient connection errors.
# Retries help with flaky TLS handshakes and proxy resets.
# ---------------------------------------------------------------
$maxAttempts = 3
$attempt     = 0
$statusCode  = 0
$respBody    = ""

while ($attempt -lt $maxAttempts) {
    $attempt++

    # Recreate request object on every attempt (WebRequest is not reusable)
    $req                   = [System.Net.WebRequest]::Create($url)
    $req.Method            = "POST"
    $req.ContentType       = "multipart/form-data; boundary=$boundary"
    $req.ContentLength     = $requestBody.Length
    $req.Timeout           = 120000   # 120 s connect + response
    $req.ReadWriteTimeout  = 120000   # 120 s for body transfer
    $req.KeepAlive         = $false   # don't reuse the TCP socket

    try {
        $reqStream = $req.GetRequestStream()
        $reqStream.Write($requestBody, 0, $requestBody.Length)
        $reqStream.Close()
    } catch {
        $innerMsg = if ($_.Exception.InnerException) { $_.Exception.InnerException.Message } else { $_.Exception.Message }
        if ($attempt -lt $maxAttempts) {
            Write-Host "[WARN] Attempt $attempt failed (send): $innerMsg – retrying..."
            Start-Sleep -Seconds 2
            continue
        }
        Write-Host "[ERROR] Failed to send request after $maxAttempts attempts: $innerMsg"
        exit 1
    }

    try {
        $resp       = [System.Net.HttpWebResponse]$req.GetResponse()
        $statusCode = [int]$resp.StatusCode
        $reader     = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $respBody   = $reader.ReadToEnd()
        $reader.Close()
        $resp.Close()
        break   # success – exit retry loop
    } catch [System.Net.WebException] {
        $ex = $_.Exception
        if ($ex.Response -ne $null) {
            $errResp    = [System.Net.HttpWebResponse]$ex.Response
            $statusCode = [int]$errResp.StatusCode
            $reader     = New-Object System.IO.StreamReader($errResp.GetResponseStream())
            $respBody   = $reader.ReadToEnd()
            $reader.Close()
            $errResp.Close()
            break   # server replied with an error code – no point retrying
        }
        $innerMsg = if ($ex.InnerException) { $ex.InnerException.Message } else { $ex.Message }
        if ($attempt -lt $maxAttempts) {
            Write-Host "[WARN] Attempt $attempt failed (receive): $innerMsg – retrying..."
            Start-Sleep -Seconds 2
        } else {
            Write-Host "[ERROR] Network error after $maxAttempts attempts: $($ex.Message)"
            if ($ex.InnerException) { Write-Host "       Inner: $($ex.InnerException.Message)" }
            Write-Host ""
            Write-Host "Troubleshooting hints:"
            Write-Host "  1. Server requires TLS 1.2 but this machine may only support TLS 1.0."
            Write-Host "     Install .NET 4.6+ or enable TLS 1.2 via Windows update."
            Write-Host "  2. For HTTPS targets, check that the server certificate is valid."
            Write-Host "  3. Try HTTP instead of HTTPS to isolate the issue."
            exit 1
        }
    }
}

