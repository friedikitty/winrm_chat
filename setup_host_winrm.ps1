# setup_host_winrm.ps1
# Run as Administrator on remote Windows Server

$ErrorActionPreference = "Stop"

Write-Host "=== Enable WinRM ===" -ForegroundColor Cyan

# Enable PowerShell Remoting
# -SkipNetworkProfileCheck: required when the network profile
# is Public, otherwise Enable-PSRemoting aborts.
Enable-PSRemoting -Force -SkipNetworkProfileCheck


Write-Host "=== Configure WinRM Service ===" -ForegroundColor Cyan

# Allow Basic authentication
winrm set winrm/config/service/auth '@{Basic="true"}'

# Allow HTTP transport
# NOTE:
# This is acceptable only for trusted network / VPN.
# For public Internet use HTTPS instead.
winrm set winrm/config/service '@{AllowUnencrypted="true"}'


Write-Host "=== Enable Firewall Rules (HTTP 5985) ===" -ForegroundColor Cyan

# Rule name differs on localized Windows builds; try the en-US rule
# name first, fall back to the display group. Existence is checked
# first because a missing rule throws under $ErrorActionPreference
# = "Stop" in a way try/catch does not reliably intercept.
if (Get-NetFirewallRule -Name "WINRM-HTTP-In-TCP" `
        -ErrorAction SilentlyContinue)
{
    Enable-NetFirewallRule -Name "WINRM-HTTP-In-TCP"
}
elseif (Get-NetFirewallRule `
        -DisplayGroup "Windows Remote Management" `
        -ErrorAction SilentlyContinue)
{
    Enable-NetFirewallRule -DisplayGroup "Windows Remote Management"
}
else
{
    Write-Host "No built-in WinRM HTTP rule found, creating one..."
    New-NetFirewallRule `
        -DisplayName "Windows Remote Management (HTTP-In)" `
        -Direction Inbound `
        -Protocol TCP `
        -LocalPort 5985 `
        -Action Allow | Out-Null
}


Write-Host "=== HTTPS Listener (5986) ===" -ForegroundColor Cyan

# Wrapped in try/catch: HTTPS is optional, HTTP above already works.
try
{
    $httpsListener = Get-ChildItem WSMan:\LocalHost\Listener |
        Where-Object { $_.Keys -contains "Transport=HTTPS" }

    if ($httpsListener)
    {
        Write-Host "HTTPS listener already exists, skipping."
    }
    else
    {
        # Find a valid server-auth certificate with a private key;
        # create a self-signed one when none is available.
        $cert = Get-ChildItem Cert:\LocalMachine\My |
            Where-Object {
                $eku = @($_.EnhancedKeyUsageList)
                $_.HasPrivateKey -and
                $_.NotAfter -gt (Get-Date) -and
                ($eku.Count -eq 0 -or
                 $eku.FriendlyName -contains "Server Authentication")
            } |
            Sort-Object NotAfter -Descending |
            Select-Object -First 1

        if (-not $cert)
        {
            Write-Host "No suitable certificate found, creating self-signed..."
            $cert = New-SelfSignedCertificate `
                -DnsName $env:COMPUTERNAME `
                -CertStoreLocation "Cert:\LocalMachine\My" `
                -NotAfter (Get-Date).AddYears(5) `
                -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.1")
        }

        Write-Host "Using certificate: $($cert.Subject)"
        Write-Host "Thumbprint: $($cert.Thumbprint)"

        New-Item `
            -Path WSMan:\LocalHost\Listener `
            -Transport HTTPS `
            -Address * `
            -CertificateThumbPrint $cert.Thumbprint `
            -Force | Out-Null

        # The built-in HTTPS firewall rule does not exist on all
        # systems. Check before enabling: with $ErrorActionPreference
        # = "Stop" a missing rule throws a CIM "not found" error that
        # try/catch does not reliably intercept.
        if (Get-NetFirewallRule -Name "WINRM-HTTPS-In-TCP" `
                -ErrorAction SilentlyContinue)
        {
            Enable-NetFirewallRule -Name "WINRM-HTTPS-In-TCP"
        }
        elseif (-not (Get-NetFirewallRule `
                -DisplayName "Windows Remote Management (HTTPS-In)" `
                -ErrorAction SilentlyContinue))
        {
            New-NetFirewallRule `
                -DisplayName "Windows Remote Management (HTTPS-In)" `
                -Direction Inbound `
                -Protocol TCP `
                -LocalPort 5986 `
                -Action Allow | Out-Null
        }

        Write-Host "HTTPS listener created on port 5986." `
            -ForegroundColor Green
    }
}
catch
{
    Write-Host "HTTPS setup failed (HTTP still works):" `
        -ForegroundColor Yellow
    Write-Host $_.Exception.Message
}


Write-Host "=== Check Listener ===" -ForegroundColor Cyan

winrm enumerate winrm/config/listener


Write-Host "=== Verify Ports ===" -ForegroundColor Cyan

foreach ($p in 5985, 5986)
{
    $probe = Test-NetConnection `
        -ComputerName 127.0.0.1 `
        -Port $p `
        -WarningAction SilentlyContinue

    if ($probe.TcpTestSucceeded)
    {
        Write-Host "Port ${p}: LISTENING" -ForegroundColor Green
    }
    else
    {
        Write-Host "Port ${p}: not listening" -ForegroundColor Yellow
    }
}


Write-Host ""
Write-Host "=== WinRM Host Setup Finished ===" -ForegroundColor Green

Write-Host ""
Write-Host "Test from client:"
Write-Host 'HTTP : Enter-PSSession -ComputerName <SERVER_IP> -Credential (Get-Credential)'
Write-Host 'HTTPS: Enter-PSSession -ComputerName <SERVER_IP> -UseSSL -Credential (Get-Credential)'
