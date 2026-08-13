# setup_host_winrm.ps1
# Run as Administrator on remote Windows Server

$ErrorActionPreference = "Stop"

Write-Host "=== Enable WinRM ===" -ForegroundColor Cyan

# Enable PowerShell Remoting
# -SkipNetworkProfileCheck: required when the network profile
# is Public, otherwise Enable-PSRemoting aborts.
Enable-PSRemoting -Force -SkipNetworkProfileCheck


Write-Host "=== Configure WinRM Service ===" -ForegroundColor Cyan

# Ensure the service survives reboots.
Set-Service WinRM -StartupType Automatic

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

# WinRM accepts an HTTPS binding only when the listener's HostName matches
# the certificate's CN, and HostName defaults to the machine's FQDN.
# A certificate issued for $env:COMPUTERNAME alone therefore fails on any
# machine with a DNS suffix, with the misleading message
# "this machine does not have an appropriate certificate".
# Derive the real name once and use it for BOTH the cert and the listener.
$cs = Get-CimInstance Win32_ComputerSystem
if ($cs.PartOfDomain -and $cs.Domain)
{
    $dnsHost = "$($cs.DNSHostName).$($cs.Domain)"
}
elseif ($cs.DNSHostName)
{
    $dnsHost = $cs.DNSHostName
}
else
{
    $dnsHost = $env:COMPUTERNAME
}

Write-Host "Certificate CN / listener HostName: $dnsHost"

# Wrapped in try/catch: HTTPS is optional, HTTP above already works.
try
{
    # Reuse a certificate only if it would actually satisfy WinRM: right CN,
    # private key present, unexpired, usable for server auth.
    # The EKU is matched by OID, not FriendlyName -- the friendly name is
    # localized on non-English Windows and never equals the literal string
    # "Server Authentication" there.
    $cert = Get-ChildItem Cert:\LocalMachine\My |
        Where-Object {
            $_.HasPrivateKey -and
            $_.NotAfter -gt (Get-Date) -and
            $_.Subject -match "^CN=$([regex]::Escape($dnsHost))(,|$)" -and
            (-not $_.EnhancedKeyUsageList -or
             $_.EnhancedKeyUsageList.ObjectId -contains "1.3.6.1.5.5.7.3.1")
        } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1

    if (-not $cert)
    {
        Write-Host "No matching certificate found, creating self-signed..."
        $names = @($dnsHost, $env:COMPUTERNAME) | Select-Object -Unique
        try
        {
            $cert = New-SelfSignedCertificate `
                -DnsName $names `
                -CertStoreLocation "Cert:\LocalMachine\My" `
                -NotAfter (Get-Date).AddYears(5)
        }
        catch
        {
            # PowerShell 4 (Server 2012 R2) only accepts -DnsName and
            # -CertStoreLocation; that build defaults to a 1-year cert.
            $cert = New-SelfSignedCertificate `
                -DnsName $names `
                -CertStoreLocation "Cert:\LocalMachine\My"
        }
    }

    Write-Host "Using certificate: $($cert.Subject)"
    Write-Host "Thumbprint: $($cert.Thumbprint)  Expires: $($cert.NotAfter)"

    # Always drop an existing HTTPS listener instead of skipping it: a
    # previous run may have left one bound to a certificate that is now
    # deleted or expired, in which case port 5986 never comes up and
    # "already exists" hides the problem forever. WinRM also refuses a
    # second listener on the same transport, -Force or not.
    Get-ChildItem WSMan:\LocalHost\Listener |
        Where-Object { $_.Keys -contains "Transport=HTTPS" } |
        ForEach-Object {
            Write-Host "Removing existing HTTPS listener."
            Remove-Item -Path $_.PSPath -Recurse -Force
        }

    New-Item `
        -Path WSMan:\LocalHost\Listener `
        -Transport HTTPS `
        -Address * `
        -HostName $dnsHost `
        -CertificateThumbPrint $cert.Thumbprint `
        -Force | Out-Null

    # The built-in HTTPS firewall rule is named differently (or missing)
    # depending on the build. Enable whichever one exists -- an existing
    # but DISABLED rule must be enabled, not skipped -- and create one
    # only when neither is present. Existence is checked with
    # -ErrorAction SilentlyContinue because with $ErrorActionPreference
    # = "Stop" a missing rule throws a CIM "not found" error that
    # try/catch does not reliably intercept.
    $rule = Get-NetFirewallRule -Name "WINRM-HTTPS-In-TCP" `
        -ErrorAction SilentlyContinue
    if (-not $rule)
    {
        $rule = Get-NetFirewallRule `
            -DisplayName "Windows Remote Management (HTTPS-In)" `
            -ErrorAction SilentlyContinue
    }

    if ($rule)
    {
        $rule | Enable-NetFirewallRule
    }
    else
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

    # Restart so the new HTTPS binding is picked up immediately.
    # NOTE: this drops the connection if you are running this script
    # THROUGH WinRM. Run it locally / via console or RDP.
    Restart-Service WinRM
}
catch
{
    # Print the whole error record, not just the message: without the
    # failing command and line this failure cannot be diagnosed at all.
    Write-Host "HTTPS setup FAILED (HTTP on 5985 still works)." `
        -ForegroundColor Red
    Write-Host ($_ | Out-String) -ForegroundColor Red
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


Write-Host "=== External Reachability ===" -ForegroundColor Cyan

# WinRM may listen locally while clients still cannot connect: the
# final hop is decided OUTSIDE this machine (cloud security group,
# edge firewall). List the addresses clients would target.
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -ne "127.0.0.1" -and
                   $_.PrefixOrigin -ne "WellKnown" } |
    ForEach-Object {
        Write-Host "Local IPv4: $($_.IPAddress) ($($_.InterfaceAlias))"
    }

Write-Host ("If a port shows LISTENING above but clients still cannot " +
    "connect, allow inbound TCP on that port in the cloud security " +
    "group / network ACL of this VM -- the Windows firewall alone is " +
    "not enough on cloud VMs.") -ForegroundColor Yellow


Write-Host ""
Write-Host "=== WinRM Host Setup Finished ===" -ForegroundColor Green

Write-Host ""
Write-Host "Test from client:"
Write-Host 'HTTP : Enter-PSSession -ComputerName <SERVER_IP> -Credential (Get-Credential)'
Write-Host 'HTTPS: Enter-PSSession -ComputerName <SERVER_IP> -UseSSL -Credential (Get-Credential)'
