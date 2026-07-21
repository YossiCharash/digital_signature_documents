#Requires -Version 2.0
<#
.SYNOPSIS
    API Client - works on ALL Windows versions (7, 8, 10, 11) with PowerShell 2.0+.
    No curl required. Uses built-in .NET WebRequest automatically.

.NOTES
    If you get an "Execution Policy" error, run:
        powershell.exe -ExecutionPolicy Bypass -File api_client.ps1 [arguments]
#>

param(
    [string]$command        = "",
    [string]$host_url       = "http://localhost:8000",
    [string]$file           = "",
    [string]$email          = "",
    [string]$phone          = "",
    [string]$so             = "",
    [string]$subject        = "",
    [string]$body           = "",
    [string]$client_name    = "",
    [string]$business_name  = "",
    [string]$business_email = "",
    [string]$message        = "",
    [string]$url            = "",
    [string]$log_file       = ""
)

# ---------------------------------------------------------------
# Local log file.
# Records every attempt on THIS machine so you can always tell what happened,
# even when the console is gone. The key distinction it captures:
#   RESPONSE     - the server answered (with any HTTP code); the outcome is known.
#   NO_RESPONSE  - the script got a network error / timeout and never received an
#                  answer. The request MAY still have been processed by the server
#                  (e.g. the email was sent but the acknowledgement was lost).
# Default location: api_client.log next to this script. Override with -log_file.
# ---------------------------------------------------------------
if ($log_file -eq "") {
    try {
        $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
    } catch {
        $scriptDir = "."
    }
    if (-not $scriptDir) { $scriptDir = "." }
    $log_file = Join-Path $scriptDir "api_client.log"
}

function Write-Log($msg) {
    $ts   = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = $ts + " | " + $msg + [Environment]::NewLine
    # Logging must never break the actual send, so swallow any file error.
    try {
        [System.IO.File]::AppendAllText($log_file, $line, [System.Text.Encoding]::UTF8)
    } catch { }
}

# ---------------------------------------------------------------
# Infer command if not provided
# ---------------------------------------------------------------
if ($command -eq "") {
    if ($phone -ne "")     { $command = "sign-and-sms" }
    elseif ($email -ne "") { $command = "sign-and-email" }
}

# ---------------------------------------------------------------
# Show help if no command
# ---------------------------------------------------------------
if ($command -eq "") {
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
# Build URL  (use string concatenation - avoids PS 2.0 interpolation quirks)
# ---------------------------------------------------------------
if ($host_url -eq "") { $host_url = "http://localhost:8000" }
# Trim whitespace BEFORE the slashes: a value that ends with a newline (easy to
# introduce by pasting a URL) would otherwise survive TrimEnd("/") and end up
# percent-encoded inside the path as /%0A/api/... , which the server 404s.
$host_url = $host_url.Trim().TrimEnd("/").Trim()
$url = $url.Trim()

if ($url -eq "") {
    if      ($command -eq "send-email")     { $url = $host_url + "/api/v1/documents/send-email" }
    elseif  ($command -eq "send-sms")       { $url = $host_url + "/api/v1/documents/send-sms" }
    elseif  ($command -eq "sign-and-email") { $url = $host_url + "/api/v1/documents/sign-and-email" }
    elseif  ($command -eq "sign-and-sms")   { $url = $host_url + "/api/v1/documents/sign-and-sms" }
    elseif  ($command -eq "verify")         { $url = $host_url + "/api/v1/documents/verify-signature" }
    else    { Write-Host "Error: Unknown command '$command'"; exit 1 }
}

# ---------------------------------------------------------------
# Validate file
# ---------------------------------------------------------------
if ($file -eq "") {
    Write-Host "Error: Missing -file"
    exit 1
}
if (-not (Test-Path $file)) {
    Write-Host "Error: File not found: $file"
    exit 1
}
$file = (Resolve-Path $file).Path

# ---------------------------------------------------------------
# Validate required fields
# ---------------------------------------------------------------
if (($command -eq "send-email" -or $command -eq "sign-and-email") -and $email -eq "") {
    Write-Host "Error: Missing -email"
    exit 1
}
if (($command -eq "send-sms" -or $command -eq "sign-and-sms") -and $phone -eq "") {
    Write-Host "Error: Missing -phone"
    exit 1
}

# ---------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------
Write-Host ""
Write-Host "Sending [$command] to $url ..."
Write-Host "  file           = $file"
if ($email -ne "")          { Write-Host "  email          = $email" }
if ($phone -ne "")          { Write-Host "  phone          = $phone" }
if ($so -ne "")             { Write-Host "  so             = $so" }
if ($client_name -ne "")    { Write-Host "  client_name    = $client_name" }
if ($business_name -ne "")  { Write-Host "  business_name  = $business_name" }
if ($business_email -ne "") { Write-Host "  business_email = $business_email" }
if ($message -ne "")        { Write-Host "  message        = $message" }
Write-Host ""

# Record the start of this request in the local log.
$logRecipient = ""
if ($email -ne "")     { $logRecipient = "email=" + $email }
elseif ($phone -ne "") { $logRecipient = "phone=" + $phone }
Write-Log ("---- REQUEST command=" + $command + " " + $logRecipient + " file=" + $file + " url=" + $url)

# ---------------------------------------------------------------
# Configure ServicePointManager BEFORE any HTTP call.
#
# Expect100Continue = $false
#   .NET sends "Expect: 100-continue" by default before the body.
#   nginx / gunicorn / many servers close the connection on that header,
#   which produces: "The underlying connection was closed: An unexpected
#   error occurred on a send."  Disabling it fixes this.
#
# TLS fallback chain (numeric values to avoid enum errors on old .NET)
#   TLS 1.3 = 12288, TLS 1.2 = 3072, TLS 1.1 = 768, TLS 1.0 = 192
#   Windows 7 + .NET 3.5 only supports TLS 1.0 (value 192).
#   Windows 7 + .NET 4.6 supports TLS 1.2.
#
# CheckCertificateRevocationList = $false
#   Revocation lookups time-out in air-gapped / proxy environments and
#   surface as connection-closed errors.
# ---------------------------------------------------------------
# SSL / TLS hardening — BEFORE any HTTP call.
#
# Step 1: ICertificatePolicy via Add-Type (C# compiled inline).
#   More reliable than assigning a PS script-block to
#   ServerCertificateValidationCallback, which silently fails on
#   PowerShell 2.0 / .NET 2-3.5 because the delegate conversion
#   is not supported.  ICertificatePolicy works on every .NET version.
#
# Step 2: ServerCertificateValidationCallback as belt-and-suspenders
#   for .NET 4.x where ICertificatePolicy is ignored.
#
# Step 3: TLS protocol fallback chain.
#   Numeric values to avoid enum-not-found errors on old .NET:
#     TLS 1.3 = 12288  TLS 1.2 = 3072  TLS 1.1 = 768  TLS 1.0 = 192
#
# Step 4: Expect100Continue / KeepAlive / RevocationList flags.
# ---------------------------------------------------------------

# --- Step 1: C# SSL bypass (works on .NET 2.0+, PS 2.0+) ---
$sslBypassCode = @"
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class _TrustAll : ICertificatePolicy {
    public bool CheckValidationResult(
        ServicePoint sp, X509Certificate cert,
        WebRequest req, int problem) { return true; }
}
"@
try {
    Add-Type -TypeDefinition $sslBypassCode -ErrorAction Stop
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object _TrustAll
} catch { }

# --- Step 2: callback for .NET 4.x ---
try {
    [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
} catch { }

# --- Step 3: TLS fallback ---
$tlsSet = $false
if (-not $tlsSet) {
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType](12288 -bor 3072 -bor 768 -bor 192)
        $tlsSet = $true
    } catch { }
}
if (-not $tlsSet) {
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType](3072 -bor 768 -bor 192)
        $tlsSet = $true
    } catch { }
}
if (-not $tlsSet) {
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]3072
        $tlsSet = $true
    } catch { }
}
if (-not $tlsSet) {
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType](768 -bor 192)
        $tlsSet = $true
    } catch { }
}
if (-not $tlsSet) {
    try {
        [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]192
    } catch { }
}

# --- Step 4: connection flags ---
try { [System.Net.ServicePointManager]::Expect100Continue              = $false } catch { }
try { [System.Net.ServicePointManager]::CheckCertificateRevocationList = $false } catch { }

# ---------------------------------------------------------------
# Build form fields hashtable for the given command
# ---------------------------------------------------------------
$formKeys   = @()
$formValues = @()

if ($command -eq "send-email") {
    $formKeys   = @("email","so","subject","body","business_name","business_email")
    $formValues = @($email, $so, $subject, $body, $business_name, $business_email)
}
elseif ($command -eq "send-sms") {
    $formKeys   = @("phone","message","business_name")
    $formValues = @($phone, $message, $business_name)
}
elseif ($command -eq "sign-and-email") {
    $formKeys   = @("email","so","subject","body","client_name","business_name","business_email")
    $formValues = @($email, $so, $subject, $body, $client_name, $business_name, $business_email)
}
elseif ($command -eq "sign-and-sms") {
    $formKeys   = @("phone","message","business_name")
    $formValues = @($phone, $message, $business_name)
}
# "verify" has no extra fields

# ---------------------------------------------------------------
# Build multipart/form-data body in a MemoryStream
# Compatible with PowerShell 2.0 / .NET 2.0 / Windows 7+
# ---------------------------------------------------------------
$boundary = "----FormBoundary" + [System.Guid]::NewGuid().ToString("N")
$CRLF     = "`r`n"
$enc      = [System.Text.Encoding]::UTF8
$ms       = New-Object System.IO.MemoryStream

# Write text fields
$i = 0
while ($i -lt $formKeys.Length) {
    $key = $formKeys[$i]
    $val = $formValues[$i]
    $i++
    if ($val -ne $null -and $val -ne "") {
        $part  = "--" + $boundary + $CRLF
        $part += "Content-Disposition: form-data; name=""" + $key + """" + $CRLF + $CRLF
        $part += $val + $CRLF
        $bytes = $enc.GetBytes($part)
        $ms.Write($bytes, 0, $bytes.Length)
    }
}

# Write file field (binary safe)
$fileName    = [System.IO.Path]::GetFileName($file)
$fileHeader  = "--" + $boundary + $CRLF
$fileHeader += "Content-Disposition: form-data; name=""file""; filename=""" + $fileName + """" + $CRLF
$fileHeader += "Content-Type: application/octet-stream" + $CRLF + $CRLF
$headerBytes = $enc.GetBytes($fileHeader)
$ms.Write($headerBytes, 0, $headerBytes.Length)

$fileBytes = [System.IO.File]::ReadAllBytes($file)
$ms.Write($fileBytes, 0, $fileBytes.Length)

$crlfBytes = $enc.GetBytes($CRLF)
$ms.Write($crlfBytes, 0, $crlfBytes.Length)

$closingBytes = $enc.GetBytes("--" + $boundary + "--" + $CRLF)
$ms.Write($closingBytes, 0, $closingBytes.Length)

$requestBody = $ms.ToArray()
$ms.Close()

# ---------------------------------------------------------------
# Send request — retry on transient failures.
# A failed attempt (network error, or a 5xx server error such as an SMTP
# failure) is retried after $retryDelaySec seconds. HTTP 4xx responses are
# client errors (e.g. bad email address) and are NOT retried — they would
# just fail again.
# ---------------------------------------------------------------
$maxAttempts   = 3
$retryDelaySec = 30   # wait half a minute between attempts
$attempt       = 0
$statusCode    = 0
$respBody      = ""

while ($attempt -lt $maxAttempts) {
    $attempt++

    # Cast to HttpWebRequest (not just WebRequest) so that KeepAlive,
    # ReadWriteTimeout, ServicePoint and other HTTP-specific properties
    # are actually accessible and applied.
    $req = [System.Net.HttpWebRequest][System.Net.WebRequest]::Create($url)
    $req.Method           = "POST"
    $req.ContentType      = "multipart/form-data; boundary=" + $boundary
    $req.ContentLength    = $requestBody.Length
    $req.Timeout          = 120000   # ms — connect + response
    $req.ReadWriteTimeout = 120000   # ms — body transfer
    $req.KeepAlive        = $false   # new TCP socket each request
    try { $req.ServicePoint.Expect100Continue = $false } catch { }

    # --- Phase 1: send body ---
    $sendOk = $false
    try {
        $reqStream = $req.GetRequestStream()
        $reqStream.Write($requestBody, 0, $requestBody.Length)
        $reqStream.Close()
        $sendOk = $true
    } catch {
        $ex     = $_.Exception
        $errMsg = $ex.Message
        $inner  = $ex.InnerException
        if ($inner -ne $null) { $errMsg = $inner.Message }

        # Show full diagnostic chain
        $webEx = $ex -as [System.Net.WebException]
        if ($webEx -ne $null) {
            Write-Host ("[DIAG] WebExceptionStatus = " + $webEx.Status)
        }
        $cur = $ex
        $depth = 0
        while ($cur -ne $null -and $depth -lt 5) {
            Write-Host ("[DIAG] Exception[" + $depth + "]: " + $cur.GetType().Name + " - " + $cur.Message)
            $cur = $cur.InnerException
            $depth++
        }

        Write-Log ("ATTEMPT " + $attempt + ": NO_RESPONSE (send failed): " + $errMsg)
        if ($attempt -lt $maxAttempts) {
            Write-Host ("[WARN] Attempt " + $attempt + " failed (send): " + $errMsg + " - retrying in " + $retryDelaySec + "s...")
            Start-Sleep -Seconds $retryDelaySec
        } else {
            Write-Host ("[ERROR] Failed to send after " + $maxAttempts + " attempts: " + $errMsg)
            Write-Log ("RESULT: FAILED after " + $maxAttempts + " attempts - request never reached the server (no response). " + $logRecipient)
            Write-Host ""
            Write-Host "Troubleshooting:"
            Write-Host "  1. Disable antivirus / firewall temporarily to rule out blocking."
            Write-Host "  2. Server may require TLS 1.2 - install .NET 4.6+ on this machine."
            Write-Host "  3. Try HTTP instead of HTTPS to isolate the TLS issue."
            exit 1
        }
    }

    if (-not $sendOk) { continue }

    # --- Phase 2: read response ---
    $recvOk = $false
    try {
        $resp       = [System.Net.HttpWebResponse]$req.GetResponse()
        $statusCode = [int]$resp.StatusCode
        $reader     = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $respBody   = $reader.ReadToEnd()
        $reader.Close()
        $resp.Close()
        $recvOk = $true
        Write-Log ("ATTEMPT " + $attempt + ": RESPONSE HTTP " + $statusCode + " (server processed the request)")
    } catch [System.Net.WebException] {
        $ex = $_.Exception
        if ($ex.Response -ne $null) {
            # Server replied with an HTTP error — capture the body either way.
            $errResp    = [System.Net.HttpWebResponse]$ex.Response
            $statusCode = [int]$errResp.StatusCode
            $reader     = New-Object System.IO.StreamReader($errResp.GetResponseStream())
            $respBody   = $reader.ReadToEnd()
            $reader.Close()
            $errResp.Close()
            Write-Log ("ATTEMPT " + $attempt + ": RESPONSE HTTP " + $statusCode + " (server processed the request)")

            # 5xx = server-side / transient failure (e.g. the email send failed)
            # -> retry after the delay. 4xx = client error -> accept and stop.
            if ($statusCode -ge 500 -and $attempt -lt $maxAttempts) {
                Write-Host ("[WARN] Attempt " + $attempt + " failed (HTTP " + $statusCode + ") - retrying in " + $retryDelaySec + "s...")
                Start-Sleep -Seconds $retryDelaySec
            } else {
                $recvOk = $true
            }
        } else {
            # Network-level failure — retry if attempts remain
            $errMsg = $ex.Message
            $inner  = $ex.InnerException
            if ($inner -ne $null) { $errMsg = $inner.Message }
            Write-Log ("ATTEMPT " + $attempt + ": NO_RESPONSE (receive failed): " + $errMsg + " [request MAY already have been processed by the server]")
            if ($attempt -lt $maxAttempts) {
                Write-Host ("[WARN] Attempt " + $attempt + " failed (receive): " + $errMsg + " - retrying in " + $retryDelaySec + "s...")
                Start-Sleep -Seconds $retryDelaySec
            } else {
                Write-Host ("[ERROR] Network error after " + $maxAttempts + " attempts: " + $ex.Message)
                Write-Log ("RESULT: UNKNOWN after " + $maxAttempts + " attempts - no response received; the request MAY have been processed (possible duplicate on retry). " + $logRecipient)
                if ($ex.InnerException -ne $null) {
                    Write-Host ("       Inner: " + $ex.InnerException.Message)
                }
                Write-Host ""
                Write-Host "Troubleshooting:"
                Write-Host "  1. Server may require TLS 1.2 - install .NET 4.6+ on this machine."
                Write-Host "  2. Check that the server SSL certificate is valid."
                Write-Host "  3. Try HTTP instead of HTTPS to isolate the TLS issue."
                exit 1
            }
        }
    }

    if ($recvOk) { break }
}

# ---------------------------------------------------------------
# Output result
#
# The script always waits for the server's HTTP response before printing
# a verdict, so the exit code reflects whether the send actually succeeded:
#   exit 0  -> delivery confirmed by the server
#   exit 1  -> delivery failed (network error already handled above, or the
#              server rejected/failed the request)
# On success we also surface a per-recipient summary parsed from the JSON,
# including a WARNING when the business copy could not be delivered.
# ---------------------------------------------------------------

# Extract a single JSON string field by name. Returns "" if not present.
# Regex-based so it works on PowerShell 2.0 (no ConvertFrom-Json there).
function Get-JsonField($json, $name) {
    if ($json -match ('"' + $name + '"\s*:\s*"([^"]*)"')) { return $matches[1] }
    return ""
}

Write-Host ""
Write-Host "===== RESULT ====="
if ($respBody -ne "") {
    Write-Host "Server response:"
    Write-Host $respBody
    Write-Host ""
}

if ($statusCode -ge 200 -and $statusCode -lt 300) {
    $delivery  = Get-JsonField $respBody "delivery"
    $recipient = Get-JsonField $respBody "recipient"
    if ($delivery -ne "" -and $recipient -ne "") {
        Write-Host ("[OK] " + $delivery + " delivered to " + $recipient + " (HTTP " + $statusCode + ")")
    } else {
        Write-Host ("[OK] Delivery succeeded (HTTP " + $statusCode + ")")
    }
    Write-Log ("RESULT: SUCCESS (HTTP " + $statusCode + ") " + $logRecipient)

    # Partial success: the client was served (HTTP 2xx) but the business copy failed.
    $bizStatus = Get-JsonField $respBody "business_email_status"
    if ($bizStatus -eq "failed") {
        $bizRecipient = Get-JsonField $respBody "business_recipient"
        Write-Host ("[WARN] Business copy to " + $bizRecipient + " was NOT delivered. Check the server delivery log.")
        Write-Log ("RESULT: PARTIAL - business copy to " + $bizRecipient + " was NOT delivered")
    }
    exit 0
} else {
    # Prefer the precise server-provided reason ("detail") over a bare status.
    $detail = Get-JsonField $respBody "detail"
    if ($detail -ne "") {
        Write-Host ("[ERROR] Delivery FAILED (HTTP " + $statusCode + "): " + $detail)
        Write-Log ("RESULT: FAILED (HTTP " + $statusCode + "): " + $detail + " " + $logRecipient)
    } else {
        Write-Host ("[ERROR] Delivery FAILED (HTTP " + $statusCode + ")")
        Write-Log ("RESULT: FAILED (HTTP " + $statusCode + ") " + $logRecipient)
    }
    exit 1
}
