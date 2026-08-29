[CmdletBinding()]
param(
    [string]$Serial = '',
    [switch]$NoLaunch,
    [switch]$SkipResources,
    [switch]$ResourcesOnly,
    [int]$ConnectionWaitSeconds = 45
)

# NanaonPrivate Server one-click installer for LDPlayer 9.

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root 'runtime\python\python.exe'
$Adb = Join-Path $Root 'runtime\platform-tools\adb.exe'
$Package = 'com.aniplex.nananiji'
$Activity = 'com.tomatolib.LibMainActivity'
$ResourceRoot = Join-Path (Split-Path -Parent $Root) $Package
$SetupRoot = Join-Path $Root 'var\setup'
$LogDir = Join-Path $Root 'var\logs'
$Cert = Join-Path $Root 'var\certs\gateway_trust_cert.pem'
$Domains = @(
    '227.hand.co.jp',
    'prd-asset.227.hand.co.jp',
    'eomwzup9a3.execute-api.ap-northeast-1.amazonaws.com',
    '1d8r7iwbqc.execute-api.ap-northeast-1.amazonaws.com',
    'api.gaudiy.com'
)
# Android indexes system CAs by the OpenSSL subject hash in the filename.
# Keep the legacy release hashes and the hashes of the Docker generator's
# stable subject (CN=Nanaon Private Gateway) for old/new Android providers.
$CertNames = @(
    'c8750f0d.0',
    'd6dc44f9.0',
    '543b6ca4.0',
    '0485b453.0',
    '1a6db830.0'
)

function Assert-File([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description is missing: $Path"
    }
}

function Restore-AdbConnection([string]$Device) {
    # Emulator launchers may restart their own daemon on the default 5037
    # port.  Keep using the bundled client and reconnect after that brief race.
    Start-Sleep -Seconds 2
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Adb start-server 2>&1 | Out-Null
    if ($Device -match '^\d{1,3}(?:\.\d{1,3}){3}:\d+$') {
        & $Adb connect $Device 2>&1 | Out-Null
    } else {
        foreach ($port in @(5555, 7555)) {
            & $Adb connect "127.0.0.1:$port" 2>&1 | Out-Null
        }
    }
    $ErrorActionPreference = $savedPreference
}

function Invoke-Adb {
    param(
        [string]$Device,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    $all = @()
    if ($Device) { $all += @('-s', $Device) }
    $all += $Arguments
    $output = @()
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = @(& $Adb @all 2>&1)
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $savedPreference
        if ($exitCode -eq 0) { return $output }
        if ($attempt -lt 2) {
            Write-Warning "ADB connection changed while configuring $Device; reconnecting once..."
            Restore-AdbConnection $Device
        }
    }
    throw "adb failed for [$Device]: adb $($Arguments -join ' ')`n$($output -join "`n")"
}

function Invoke-Su([string]$Device, [string]$Command) {
    if ($Command.Contains("'")) {
        throw "Root command contains an unsupported single quote: $Command"
    }
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $remoteCommand = "su -c '$Command'"
    $output = & $Adb -s $Device shell $remoteCommand 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedPreference
    if ($exitCode -ne 0) {
        throw "su failed on ${Device}: $Command`n$($output -join "`n")"
    }
    return @($output)
}

function Get-SuFileHash([string]$Device, [string]$Path) {
    $line = ((Invoke-Su $Device "sha256sum $Path 2>/dev/null || echo MISSING") -join ' ').Trim()
    if ($line -match '^([0-9a-fA-F]{64})') {
        return $Matches[1].ToLowerInvariant()
    }
    return ''
}

function Test-MagiskDevice([string]$Device) {
    try {
        Invoke-Su $Device 'command -v magisk >/dev/null 2>&1 && magisk -V >/dev/null 2>&1' | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-DeviceRows {
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $rows = & $Adb devices 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedPreference
    if ($exitCode -ne 0) {
        throw "Cannot list Android devices.`n$($rows -join "`n")"
    }
    return @($rows | Select-Object -Skip 1 | ForEach-Object {
        if ($_ -match '^([^\s]+)\s+device(?:\s|$)') {
            [PSCustomObject]@{ Serial = $Matches[1] }
        }
    } | Where-Object { $_ })
}

function Get-DeviceIdentity([string]$Device) {
    $value = ((& $Adb -s $Device shell getprop ro.boot.serialno 2>&1) -join '').Trim()
    if (-not $value -or $value -eq 'unknown') {
        $value = ((& $Adb -s $Device shell getprop ro.serialno 2>&1) -join '').Trim()
    }
    if (-not $value -or $value -eq 'unknown') {
        return "serial:$Device"
    }
    return $value
}

function Connect-LDPlayerPorts {
    # LDPlayer instances commonly listen on 5555 (LDPlayer 9) or 7555.
    foreach ($port in @(5555, 7555)) {
        $client = New-Object Net.Sockets.TcpClient
        try {
            $pending = $client.BeginConnect('127.0.0.1', $port, $null, $null)
            if ($pending.AsyncWaitHandle.WaitOne(120)) {
                $client.EndConnect($pending)
                & $Adb connect "127.0.0.1:$port" 2>&1 | Out-Null
            }
        } catch {
            # No LDPlayer instance is listening on this port.
        } finally {
            $client.Dispose()
        }
    }
}

function Resolve-Devices {
    & $Adb start-server | Out-Null
    Connect-LDPlayerPorts
    $rows = Get-DeviceRows

    if ($Serial) {
        if (@($rows.Serial) -notcontains $Serial) {
            & $Adb connect $Serial 2>&1 | Out-Null
            Start-Sleep -Seconds 2
            $rows = Get-DeviceRows
        }
        if (@($rows.Serial) -notcontains $Serial) {
            throw "Requested device is not connected: $Serial"
        }
        return @([PSCustomObject]@{ Serial = $Serial; Identity = (Get-DeviceIdentity $Serial) })
    }

    if ($rows.Count -eq 0) {
        throw 'No LDPlayer instance is connected. Start LDPlayer, enable ADB/root in its settings, then run again.'
    }

    # One LDPlayer VM is frequently exposed twice by ADB:
    # `emulator-5554` (native transport) and `127.0.0.1:5555` (TCP alias).
    # Configure each physical VM exactly once.
    $preferred = @($rows | Where-Object { $_.Serial -like 'emulator-*' }) +
                 @($rows | Where-Object { $_.Serial -notlike 'emulator-*' })
    $seen = @{}
    $result = @()
    foreach ($row in $preferred) {
        $identity = Get-DeviceIdentity $row.Serial
        if ($seen.ContainsKey($identity)) {
            Write-Host "Skipping duplicate ADB entry $($row.Serial) (same device as $($seen[$identity]))." -ForegroundColor DarkGray
            continue
        }
        $seen[$identity] = $row.Serial
        $result += [PSCustomObject]@{ Serial = $row.Serial; Identity = $identity }
    }
    return @($result)
}

function Get-PortOwners([int]$Port) {
    $connections = @()
    try {
        $connections = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop)
    } catch {
        # Fallback for hosts where the NetTCPIP CIM provider is unavailable.
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $netstat = & netstat -ano -p tcp 2>&1
        $ErrorActionPreference = $savedPreference
        foreach ($line in $netstat) {
            if ($line -is [string] -and $line -match "^\s*TCP\s+(\S+:${Port})\s+\S+\s+LISTENING\s+(\d+)") {
                $connections += [PSCustomObject]@{
                    LocalAddress = $Matches[1]
                    OwningProcess = [int]$Matches[2]
                }
            }
        }
    }

    $owners = @()
    foreach ($connection in $connections) {
        $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
        $owners += [PSCustomObject]@{
            Port = $Port
            Address = $connection.LocalAddress
            ProcessId = $connection.OwningProcess
            ProcessName = if ($process) { $process.ProcessName } else { 'unknown' }
        }
    }
    return @($owners)
}

function Get-PortProxyRules {
    $rules = @()
    foreach ($protocol in @('v4tov4', 'v4tov6', 'v6tov4', 'v6tov6')) {
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = & netsh interface portproxy show $protocol 2>&1
        $ErrorActionPreference = $savedPreference
        foreach ($line in $output) {
            if ($line -is [string] -and $line -match '^(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
                $rules += [PSCustomObject]@{
                    Protocol = $protocol
                    ListenAddress = $Matches[1]
                    ListenPort = [int]$Matches[2]
                    ConnectAddress = $Matches[3]
                    ConnectPort = [int]$Matches[4]
                }
            }
        }
    }
    return @($rules)
}

function Assert-RequiredPortsFree([int[]]$Ports) {
    # PIDs recorded by a previous launch are left for run.py to stop itself.
    $recordedPids = @()
    $statePath = Join-Path $Root 'var\run\processes.json'
    if (Test-Path -LiteralPath $statePath) {
        try {
            $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            $recordedPids = @(
                @($state.children) + @($state.services.PSObject.Properties.Value) + @($state.launcher_pid) |
                Where-Object { $_ } | ForEach-Object { [int]$_ }
            )
        } catch { $recordedPids = @() }
    }

    foreach ($port in $Ports) {
        $owners = @(Get-PortOwners $port)
        if ($owners.Count -eq 0) { continue }
        $unrecorded = @($owners | Where-Object { $recordedPids -notcontains $_.ProcessId })
        if ($unrecorded.Count -eq 0) { continue }

        $ownerText = ($owners | ForEach-Object { "$($_.ProcessName) (PID $($_.ProcessId))" }) -join ', '
        $proxyRules = @(Get-PortProxyRules | Where-Object { $_.ListenPort -eq $port })
        $proxyText = if ($proxyRules.Count -gt 0) {
            ' A Windows portproxy rule also uses this port; inspect it with: netsh interface portproxy show all.'
        } else { '' }
        throw "Port $port is already in use by: $ownerText.$proxyText Close that program (or run Stop-Server.cmd for an old project instance), then run again."
    }
}

function Get-LocalIPv4 {
    return @([Net.Dns]::GetHostAddresses([Net.Dns]::GetHostName()) |
        Where-Object {
            $_.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
            -not $_.IsIPv6LinkLocal -and
            $_.IPAddressToString -notlike '127.*' -and
            $_.IPAddressToString -notlike '169.254.*'
        } | ForEach-Object { $_.IPAddressToString } | Select-Object -Unique)
}

function Test-DeviceTcp443([string]$Device, [string]$Address) {
    $command = "echo | nc -w 3 $Address 443 >/dev/null 2>&1 || echo | toybox nc -w 3 $Address 443 >/dev/null 2>&1; echo `$?"
    $result = & $Adb -s $Device shell $command 2>&1
    return (($result | Select-Object -Last 1).ToString().Trim() -eq '0')
}

function Select-HostAddress([string]$Device, [string[]]$LocalAddresses) {
    $route = (Invoke-Adb $Device shell ip route) -join "`n"
    $guest = ''
    if ($route -match '\bsrc\s+(\d+\.\d+\.\d+\.\d+)') { $guest = $Matches[1] }

    $candidates = New-Object Collections.Generic.List[string]
    if ($guest -like '10.0.2.*') { $candidates.Add('10.0.2.2') }
    if ($guest) {
        $prefix = ($guest -split '\.')[0..2] -join '.'
        foreach ($alias in @('2', '1')) { $candidates.Add("$prefix.$alias") }
        if ($route -match 'default via (\d+\.\d+\.\d+\.\d+)') { $candidates.Add($Matches[1]) }
        foreach ($address in $LocalAddresses) {
            if ($address.StartsWith($prefix + '.')) { $candidates.Add($address) }
        }
    }
    foreach ($address in $LocalAddresses) { $candidates.Add($address) }
    if (-not $candidates.Contains('10.0.2.2')) { $candidates.Add('10.0.2.2') }

    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if (Test-DeviceTcp443 $Device $candidate) {
            return [PSCustomObject]@{ HostAddress = $candidate; GuestAddress = $guest; Route = $route }
        }
    }
    throw "Device $Device cannot reach TCP 443 on any host address: $($candidates -join ', ')"
}

function Write-DeviceScript([string]$Path, [string[]]$Lines) {
    ([string]::Join("`n", $Lines) + "`n") | Set-Content -LiteralPath $Path -Encoding Ascii -NoNewline
}

function Invoke-DeviceScript([string]$Device, [string]$ScriptName) {
    Invoke-Su $Device "if type timeout >/dev/null 2>&1; then timeout 120 sh /data/local/tmp/$ScriptName; else sh /data/local/tmp/$ScriptName; fi"
}

function Restart-AndWaitBoot([string]$Device) {
    $identity = Get-DeviceIdentity $Device
    Write-Host "Rebooting LDPlayer instance (device id $identity)..." -ForegroundColor Yellow

    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $job = Start-Job -ScriptBlock { param($a, $s) & $a -s $s reboot 2>&1 } -ArgumentList $Adb, $Device
    if (-not (Wait-Job $job -Timeout 30)) { Stop-Job $job }
    Receive-Job $job | Out-Null
    Remove-Job $job -Force
    $ErrorActionPreference = $savedPreference

    # LDPlayer may republish its ADB endpoint under a different serial after
    # the reboot.  Follow the physical device id instead of the old serial.
    $deadline = (Get-Date).AddSeconds(180)
    do {
        Connect-LDPlayerPorts
        $rows = Get-DeviceRows
        $preferred = @($rows | Where-Object { $_.Serial -eq $Device }) +
                     @($rows | Where-Object { $_.Serial -ne $Device })
        foreach ($row in $preferred) {
            $bootCompleted = ((& $Adb -s $row.Serial shell getprop sys.boot_completed 2>&1) -join '').Trim()
            if ($bootCompleted -ne '1') { continue }
            if ((Get-DeviceIdentity $row.Serial) -eq $identity) {
                Write-Host "LDPlayer is back online as $($row.Serial)."
                Start-Sleep -Seconds 5
                return $row.Serial
            }
        }
        Start-Sleep -Seconds 3
    } until ((Get-Date) -gt $deadline)
    throw "LDPlayer instance $identity did not finish rebooting within 180 seconds."
}

function Start-PrivateServer {
    $launcherOutLog = Join-Path $LogDir 'launcher.out.log'
    $launcherErrLog = Join-Path $LogDir 'launcher.err.log'
    $server = Start-Process -FilePath $Python -ArgumentList @('-B', 'run.py') -WorkingDirectory $Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $launcherOutLog -RedirectStandardError $launcherErrLog
    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 500
        $response = $null
        try {
            $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:8888/health')
            $request.Proxy = $null
            $request.Timeout = 2000
            $request.ReadWriteTimeout = 2000
            $response = $request.GetResponse()
            $healthy = ([int]$response.StatusCode -eq 200)
        } catch {
            $healthy = $false
        } finally {
            if ($response) { $response.Close() }
        }
    } until ($healthy -or (Get-Date) -gt $deadline)
    if (-not $healthy) {
        $diagnostics = ''
        foreach ($launcherLog in @($launcherErrLog, $launcherOutLog)) {
            if (Test-Path -LiteralPath $launcherLog) {
                $tail = ((Get-Content -LiteralPath $launcherLog -Tail 20 -ErrorAction SilentlyContinue) -join "`n").Trim()
                if ($tail) { $diagnostics += "`n--- $(Split-Path -Leaf $launcherLog) ---`n$tail" }
            }
        }
        throw "Server did not become healthy. See $LogDir. Launcher PID: $($server.Id)$diagnostics"
    }
    Write-Host "Server: OK (launcher PID $($server.Id), HTTPS 0.0.0.0:443)"
}

function Get-HostResourceStats {
    if (-not (Test-Path -LiteralPath $ResourceRoot -PathType Container)) {
        throw "Game resource directory is missing: $ResourceRoot"
    }
    $files = @(Get-ChildItem -LiteralPath $ResourceRoot -Recurse -File)
    $bytes = [long](($files | Measure-Object -Property Length -Sum).Sum)
    return [PSCustomObject]@{
        FileCount = $files.Count
        Bytes = $bytes
    }
}

function Get-DeviceTreeKilobytes([string]$Device, [string]$RemotePath) {
    $value = ((Invoke-Adb $Device shell "du -sk '$RemotePath' 2>/dev/null || echo 0") -join "`n")
    if ($value -match '(?m)^\s*(\d+)') { return [long]$Matches[1] }
    return 0L
}

function Get-ResourceEntryStats([IO.FileSystemInfo]$Entry) {
    if (-not $Entry.PSIsContainer) {
        return [PSCustomObject]@{ FileCount = 1; Bytes = [long]$Entry.Length }
    }
    $files = @(Get-ChildItem -LiteralPath $Entry.FullName -Recurse -File)
    return [PSCustomObject]@{
        FileCount = $files.Count
        Bytes = [long](($files | Measure-Object -Property Length -Sum).Sum)
    }
}

function Push-ResourceGroup([string]$Device, [object[]]$Entries, [string]$RemoteDirectory) {
    if ($Entries.Count -eq 0) { return }
    Invoke-Adb $Device shell "mkdir -p '$RemoteDirectory'" | Out-Null
    $names = ($Entries | ForEach-Object { $_.Name }) -join ', '
    $label = if ($Entries.Count -eq 1) { $Entries[0].Name } else { "$($Entries.Count) entries" }
    Write-Host "  syncing $label -> $(Split-Path -Leaf $RemoteDirectory)..."
    $arguments = @('-s', $Device, 'push', '--sync')
    $arguments += @($Entries | ForEach-Object { $_.FullName })
    $arguments += "$RemoteDirectory/"
    $output = @()
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $savedPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = @(& $Adb @arguments 2>&1)
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $savedPreference
        if ($exitCode -eq 0) { break }
        if ($attempt -lt 2) {
            Write-Warning 'ADB daemon restarted during resource transfer; reconnecting and retrying this batch...'
            Restore-AdbConnection $Device
        }
    }
    if ($exitCode -ne 0) {
        throw "Resource push failed on ${Device}: $names`n$($output -join "`n")"
    }
    $summary = @($output | Where-Object { $_ -match 'files? pushed|skipped' } | Select-Object -Last 1)
    if ($summary.Count -gt 0) {
        Write-Host "    $($summary[0])"
    }
}

function Sync-ResourceDirectory([string]$Device, [string]$LocalDirectory, [string]$RemoteDirectory) {
    # LDPlayer's ADB can stall indefinitely when one push contains thousands of
    # nested directories.  Keep each transaction below 100 files / 512 MiB and
    # recurse only into oversized branches.
    $maxFiles = 100
    $maxBytes = 512MB
    $batch = New-Object Collections.Generic.List[object]
    $batchFiles = 0
    $batchBytes = 0L

    foreach ($entry in @(Get-ChildItem -LiteralPath $LocalDirectory -Force)) {
        $stats = Get-ResourceEntryStats $entry
        $oversized = $stats.FileCount -gt $maxFiles -or $stats.Bytes -gt $maxBytes
        if ($oversized) {
            if ($batch.Count -gt 0) {
                Push-ResourceGroup $Device $batch.ToArray() $RemoteDirectory
                $batch.Clear(); $batchFiles = 0; $batchBytes = 0L
            }
            if ($entry.PSIsContainer) {
                Sync-ResourceDirectory $Device $entry.FullName "$RemoteDirectory/$($entry.Name)"
            } else {
                Push-ResourceGroup $Device @($entry) $RemoteDirectory
            }
            continue
        }

        if ($batch.Count -gt 0 -and
            ($batchFiles + $stats.FileCount -gt $maxFiles -or
             $batchBytes + $stats.Bytes -gt $maxBytes)) {
            Push-ResourceGroup $Device $batch.ToArray() $RemoteDirectory
            $batch.Clear(); $batchFiles = 0; $batchBytes = 0L
        }
        $batch.Add($entry)
        $batchFiles += $stats.FileCount
        $batchBytes += $stats.Bytes
    }
    if ($batch.Count -gt 0) {
        Push-ResourceGroup $Device $batch.ToArray() $RemoteDirectory
    }
}

function Sync-GameResources([string]$Device) {
    $packagePath = (& $Adb -s $Device shell pm path $Package 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $packagePath -notmatch '^package:') {
        throw "Game package is not installed on ${Device}: $Package"
    }
    Invoke-Adb $Device shell am force-stop $Package | Out-Null

    $stats = Get-HostResourceStats
    $remoteRoot = "/sdcard/Android/data/$Package"
    Invoke-Adb $Device shell "mkdir -p '$remoteRoot'" | Out-Null

    $currentKb = Get-DeviceTreeKilobytes $Device $remoteRoot
    $dfLines = @(Invoke-Adb $Device shell df -k /sdcard)
    $dfLine = @($dfLines | Where-Object { $_ -match '^\S+\s+\d+' } | Select-Object -Last 1)
    if ($dfLine.Count -gt 0) {
        $columns = @($dfLine[0].Trim() -split '\s+')
        if ($columns.Count -ge 4 -and $columns[3] -match '^\d+$') {
            $availableBytes = [long]$columns[3] * 1KB
            $remainingBytes = [Math]::Max(0L, $stats.Bytes - ($currentKb * 1KB))
            $safetyMargin = 256MB
            if ($availableBytes -lt ($remainingBytes + $safetyMargin)) {
                throw "Not enough device storage for the resource pack. Available=$([Math]::Round($availableBytes / 1GB, 2)) GiB, estimated required=$([Math]::Round(($remainingBytes + $safetyMargin) / 1GB, 2)) GiB."
            }
        }
    }

    Write-Host "Resource pack: $($stats.FileCount) files, $([Math]::Round($stats.Bytes / 1GB, 2)) GiB" -ForegroundColor Yellow
    Write-Host "Device currently has $([Math]::Round(($currentKb * 1KB) / 1GB, 2)) GiB; starting incremental ADB sync..."
    Sync-ResourceDirectory $Device $ResourceRoot $remoteRoot

    $remoteCountText = ((Invoke-Adb $Device shell "find '$remoteRoot' -type f 2>/dev/null | wc -l") -join '').Trim()
    $finalKb = Get-DeviceTreeKilobytes $Device $remoteRoot
    if ($remoteCountText -notmatch '^\d+$' -or [long]$remoteCountText -lt $stats.FileCount) {
        throw "Resource verification failed on ${Device}: host files=$($stats.FileCount), device files=$remoteCountText"
    }
    if (($finalKb * 1KB) -lt $stats.Bytes) {
        throw "Resource verification failed on ${Device}: device tree is smaller than the host pack."
    }
    Write-Host "Game resources: OK ($remoteCountText files, $([Math]::Round(($finalKb * 1KB) / 1GB, 2)) GiB)" -ForegroundColor Green
}

function Sync-GameObb([string]$Device) {
    $obbName = 'main.5465.com.aniplex.nananiji.obb'
    $obbHostCandidates = @(
        (Join-Path (Split-Path -Parent $Root) $obbName),
        (Join-Path $Root $obbName)
    )
    $obbHostPath = $null
    foreach ($candidate in $obbHostCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $obbHostPath = $candidate
            break
        }
    }
    $deviceObbDirectory = "/sdcard/Android/obb/$Package"
    $deviceObbPath = "$deviceObbDirectory/$obbName"
    $deviceObbSize = ((& $Adb -s $Device shell "stat -c %s '$deviceObbPath' 2>/dev/null") -join '').Trim()
    if (-not $obbHostPath) {
        if ($deviceObbSize -notmatch '^\d+$') {
            Write-Warning "No host OBB found (expected $obbName next to the private_server folder) and the device OBB is missing. The game may hang at the loading screen."
        }
        return
    }

    $hostObbSize = (Get-Item -LiteralPath $obbHostPath).Length
    if ($deviceObbSize -match '^\d+$' -and [long]$deviceObbSize -eq $hostObbSize) {
        Write-Host 'Game OBB: already present'
        return
    }

    Write-Host "Game OBB missing or changed; pushing $([Math]::Round($hostObbSize / 1MB)) MB..." -ForegroundColor Yellow
    Invoke-Adb $Device shell "mkdir -p '$deviceObbDirectory'" | Out-Null
    Invoke-Adb $Device push $obbHostPath $deviceObbPath | Out-Null
    $verifiedSize = ((& $Adb -s $Device shell "stat -c %s '$deviceObbPath' 2>/dev/null") -join '').Trim()
    if ($verifiedSize -notmatch '^\d+$' -or [long]$verifiedSize -ne $hostObbSize) {
        throw "Game OBB verification failed on ${Device}: host=$hostObbSize device=$verifiedSize"
    }
    Write-Host 'Game OBB: OK' -ForegroundColor Green
}

function Install-Device([string]$Device, [string[]]$LocalAddresses) {
    Write-Host ""
    Write-Host "--- Configuring $Device ---" -ForegroundColor Cyan
    $model = ((Invoke-Adb $Device shell getprop ro.product.model) -join '').Trim()
    Write-Host "Model: $model"

    $packagePath = (& $Adb -s $Device shell pm path $Package 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $packagePath -notmatch '^package:') {
        Write-Warning "Skipping $Device because $Package is not installed."
        return $false
    }

    $identity = ((Invoke-Su $Device 'id') -join '').Trim()
    if ($identity -notmatch 'uid=0\(root\)') {
        throw "Device $Device has no working root shell. Enable LDPlayer root and run again."
    }
    Write-Host "Root: OK"

    if (-not $SkipResources) {
        Sync-GameResources $Device
        Sync-GameObb $Device
    } else {
        Write-Host 'Game resources and OBB: skipped by -SkipResources' -ForegroundColor Yellow
    }

    $network = Select-HostAddress $Device $LocalAddresses
    Write-Host "Guest IPv4: $($network.GuestAddress)"
    Write-Host "Server address visible to this device: $($network.HostAddress)"

    $safeSerial = $Device -replace '[^A-Za-z0-9_.-]', '_'
    $deviceDir = Join-Path $SetupRoot $safeSerial
    if (-not (Test-Path -LiteralPath $deviceDir)) {
        New-Item -ItemType Directory -Path $deviceDir -Force | Out-Null
    }
    $hostsPath = Join-Path $deviceDir 'hosts'
    $installScriptPath = Join-Path $deviceDir 'install-system.sh'
    $hostLines = @(
        '127.0.0.1 localhost',
        '::1 ip6-localhost',
        '127.0.0.1 coinhive.com'
    ) + @($Domains | ForEach-Object { "$($network.HostAddress) $_" })
    # Android hosts parsing requires LF line endings.
    [IO.File]::WriteAllText($hostsPath, (($hostLines -join "`n") + "`n"), [Text.Encoding]::ASCII)
    $localHostsHash = (Get-FileHash -LiteralPath $hostsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $localCertHash = (Get-FileHash -LiteralPath $Cert -Algorithm SHA256).Hash.ToLowerInvariant()

    Invoke-Adb $Device push $hostsPath /data/local/tmp/nanaon-hosts | Out-Null
    Invoke-Adb $Device push $Cert /data/local/tmp/nanaon-ca.pem | Out-Null

    $hasMagisk = Test-MagiskDevice $Device
    if ($hasMagisk) {
        Write-Host 'Magisk detected; using systemless hosts/CA modules...'
        $mountedHostsHash = Get-SuFileHash $Device '/system/etc/hosts'
        $mountedCertHash = Get-SuFileHash $Device '/system/etc/security/cacerts/c8750f0d.0'
        $moduleChanged = ($mountedHostsHash -ne $localHostsHash -or
                          $mountedCertHash -ne $localCertHash)

        if ($moduleChanged) {
            $moduleRoot = Join-Path $deviceDir 'magisk-module'
            $moduleSystem = Join-Path $moduleRoot 'system\etc'
            $moduleCerts = Join-Path $moduleSystem 'security\cacerts'
            $moduleZip = Join-Path $deviceDir 'nanaon-magisk-module.zip'
            if (Test-Path -LiteralPath $moduleRoot) {
                Remove-Item -LiteralPath $moduleRoot -Recurse -Force
            }
            if (Test-Path -LiteralPath $moduleZip) {
                Remove-Item -LiteralPath $moduleZip -Force
            }
            New-Item -ItemType Directory -Path $moduleCerts -Force | Out-Null
            Copy-Item -LiteralPath $hostsPath -Destination (Join-Path $moduleSystem 'hosts') -Force
            foreach ($name in $CertNames) {
                Copy-Item -LiteralPath $Cert -Destination (Join-Path $moduleCerts $name) -Force
            }
            $moduleProperty = @(
                'id=nanaon-ca',
                'name=Nanaon Private Server Network',
                'version=1.0',
                'versionCode=1',
                'author=nanaon',
                'description=Systemless hosts and local CA'
            ) -join "`n"
            [IO.File]::WriteAllText(
                (Join-Path $moduleRoot 'module.prop'),
                ($moduleProperty + "`n"),
                [Text.Encoding]::ASCII
            )
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::CreateFromDirectory(
                $moduleRoot,
                $moduleZip,
                [IO.Compression.CompressionLevel]::Optimal,
                $false
            )
            Invoke-Adb $Device push $moduleZip /data/local/tmp/nanaon-magisk-module.zip | Out-Null
            Invoke-Su $Device 'magisk --install-module /data/local/tmp/nanaon-magisk-module.zip' | Out-Null
            $Device = Restart-AndWaitBoot $Device
        } else {
            Write-Host 'Magisk modules are already current; reboot skipped.'
        }
    } else {
        Write-Host 'Magisk not found; trying direct installation on writable /system...' -ForegroundColor Yellow
        $directChanged = ((Get-SuFileHash $Device '/system/etc/hosts') -ne $localHostsHash -or
                          (Get-SuFileHash $Device '/system/etc/security/cacerts/c8750f0d.0') -ne $localCertHash)
        if ($directChanged) {
            $directLines = @(
                '#!/system/bin/sh',
                'set -e',
                'mount -o rw,remount /system 2>/dev/null || mount -o remount,rw /system 2>/dev/null || mount -o rw,remount / 2>/dev/null || mount -o remount,rw / 2>/dev/null || true',
                'test -d /system/etc/security/cacerts',
                'cp /data/local/tmp/nanaon-hosts /system/etc/hosts',
                'chown 0:0 /system/etc/hosts',
                'chmod 0644 /system/etc/hosts'
            )
            foreach ($name in $CertNames) {
                $directLines += "cp /data/local/tmp/nanaon-ca.pem /system/etc/security/cacerts/$name"
                $directLines += "chown 0:0 /system/etc/security/cacerts/$name"
                $directLines += "chmod 0644 /system/etc/security/cacerts/$name"
            }
            $directLines += 'sync'
            Write-DeviceScript $installScriptPath $directLines
            Invoke-Adb $Device push $installScriptPath /data/local/tmp/nanaon-install-system.sh | Out-Null
            try {
                Invoke-DeviceScript $Device 'nanaon-install-system.sh' | Out-Null
            } catch {
                throw "Magisk is absent and /system is not writable on ${Device}. Enable emulator root with writable system (or Magisk), then retry. $($_.Exception.Message)"
            }
            if ((Get-SuFileHash $Device '/system/etc/hosts') -ne $localHostsHash -or
                (Get-SuFileHash $Device '/system/etc/security/cacerts/c8750f0d.0') -ne $localCertHash) {
                throw "Direct /system installation did not persist on ${Device}. Use an emulator with writable system or Magisk."
            }
            $Device = Restart-AndWaitBoot $Device
        } else {
            Write-Host 'Direct system hosts/CA are already current; reboot skipped.'
        }
    }

    $installedHosts = (Invoke-Su $Device 'cat /system/etc/hosts') -join "`n"
    foreach ($domain in $Domains) {
        if ($installedHosts -notmatch [regex]::Escape($domain)) {
            throw "hosts verification failed on ${Device}: missing $domain"
        }
    }
    foreach ($name in $CertNames) {
        Invoke-Su $Device "test -s /system/etc/security/cacerts/$name" | Out-Null
        $deviceHash = Get-SuFileHash $Device "/system/etc/security/cacerts/$name"
        if ($deviceHash -ne $localCertHash) {
            throw "Certificate hash verification failed on ${Device}: $name"
        }
    }
    Write-Host "Hosts: OK ($($Domains.Count) domains)"
    Write-Host "Android system CA: OK ($($CertNames -join ', '))"

    Invoke-Adb $Device logcat -c | Out-Null
    Invoke-Adb $Device shell am force-stop $Package | Out-Null
    if (-not $NoLaunch) {
        Invoke-Adb $Device shell am start -n "$Package/$Activity" | Out-Null
        Write-Host "App launched. Waiting up to $ConnectionWaitSeconds seconds for its first API request..."
    }
    return $true
}

function Save-FailureEvidence([string]$Message) {
    try {
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $path = Join-Path $LogDir "install-and-run.failure.$stamp.log"
        $lines = New-Object 'System.Collections.Generic.List[string]'
        $lines.Add("time: $(Get-Date -Format o)")
        $lines.Add("error: $Message")
        $lines.Add('')
        try {
            $lines.Add('--- adb devices ---')
            foreach ($row in @(& $Adb devices 2>&1)) { $lines.Add([string]$row) }
            $lines.Add('')
            $lines.Add('--- logcat (last 300 lines) ---')
            foreach ($row in @(& $Adb logcat -d -t 300 2>&1)) { $lines.Add([string]$row) }
        } catch {
            $lines.Add("logcat capture failed: $($_.Exception.Message)")
        }
        foreach ($name in @('gateway.log', 'server.log', 'api.out.log', 'cdn.out.log', 'gateway.out.log', 'launcher.out.log', 'launcher.err.log')) {
            $logPath = Join-Path $LogDir $name
            if (-not (Test-Path -LiteralPath $logPath)) { continue }
            try {
                $lines.Add('')
                $lines.Add("--- $name (last 40 lines) ---")
                foreach ($line in @(Get-Content -LiteralPath $logPath -Tail 40 -ErrorAction SilentlyContinue)) {
                    $lines.Add([string]$line)
                }
            } catch { }
        }
        [IO.File]::WriteAllLines($path, $lines, [Text.Encoding]::UTF8)
        Write-Host "Failure evidence saved to $path" -ForegroundColor Yellow
    } catch {
        Write-Warning "Could not save failure evidence: $($_.Exception.Message)"
    }
}

try {
    Assert-File $Adb 'Bundled ADB'
    New-Item -ItemType Directory -Path $SetupRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

    if ($ResourcesOnly) {
        Write-Host '[1/2] Discovering connected Android devices...' -ForegroundColor Green
        $resourceDevices = @(Resolve-Devices)
        if ($resourceDevices.Count -eq 0) { throw 'No Android device is connected.' }
        Write-Host '[2/2] Synchronizing the complete game resource pack...' -ForegroundColor Green
        foreach ($resourceDevice in $resourceDevices) {
            Sync-GameResources $resourceDevice.Serial
            Sync-GameObb $resourceDevice.Serial
        }
        Write-Host ''
        Write-Host 'All game resources were pushed and verified.' -ForegroundColor Green
        exit 0
    }

    Assert-File $Python 'Bundled Python'

    Assert-RequiredPortsFree -Ports @(443, 8000, 8888)

    Write-Host '[1/5] Starting NanaonPrivate Server...' -ForegroundColor Green
    Start-PrivateServer
    Assert-File $Cert 'Generated TLS certificate'

    Write-Host '[2/5] Discovering LDPlayer instances...' -ForegroundColor Green
    $devices = @(Resolve-Devices)
    if ($devices.Count -eq 0) {
        throw 'No LDPlayer instance is connected. Start LDPlayer, enable ADB/root in its settings, then run again.'
    }
    Write-Host "LDPlayer instances: $(($devices | ForEach-Object { "$($_.Serial) [id $($_.Identity)]" }) -join ', ')"

    Write-Host '[3/5] Reading host IPv4 addresses...' -ForegroundColor Green
    $localAddresses = @(Get-LocalIPv4)
    if ($localAddresses.Count -eq 0) { throw 'No usable local IPv4 address was found.' }
    Write-Host "Host IPv4 candidates: $($localAddresses -join ', ')"

    $gatewayLog = Join-Path $LogDir 'gateway.log'
    $script:GatewayLogOffset = if (Test-Path -LiteralPath $gatewayLog) {
        (Get-Item -LiteralPath $gatewayLog).Length
    } else { 0 }

    Write-Host '[4/5] Installing hosts and TLS CA...' -ForegroundColor Green
    $configured = 0
    foreach ($device in $devices) {
        if (Install-Device $device.Serial $localAddresses) { $configured++ }
    }
    if ($configured -eq 0) { throw "No LDPlayer instance has $Package installed." }

    Write-Host '[5/5] Verifying listeners and client connection...' -ForegroundColor Green
    foreach ($port in @(443, 8000, 8888)) {
        $probe = New-Object Net.Sockets.TcpClient
        try {
            $pending = $probe.BeginConnect('127.0.0.1', $port, $null, $null)
            if (-not $pending.AsyncWaitHandle.WaitOne(3000)) {
                throw "TCP connection to server port $port timed out."
            }
            $probe.EndConnect($pending)
            Write-Host "Port ${port}: LISTENING"
        } catch {
            throw "Server port $port is unreachable: $($_.Exception.Message)"
        } finally {
            $probe.Dispose()
        }
    }

    if (-not $NoLaunch) {
        $request = $null
        $trustError = $null
        $deadline = (Get-Date).AddSeconds([Math]::Max(5, $ConnectionWaitSeconds))
        do {
            Start-Sleep -Seconds 2
            if (Test-Path -LiteralPath $gatewayLog) {
                $stream = [IO.File]::Open($gatewayLog, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                try {
                    if ($script:GatewayLogOffset -lt $stream.Length) {
                        $stream.Seek($script:GatewayLogOffset, [IO.SeekOrigin]::Begin) | Out-Null
                    }
                    $reader = New-Object IO.StreamReader($stream)
                    $currentRunLog = $reader.ReadToEnd()
                    $reader.Dispose()
                } finally {
                    $stream.Dispose()
                }
                $request = $currentRunLog | Select-String 'REQ .*Host=227\.hand\.co\.jp.*api/environment/server' | Select-Object -Last 1
                $trustError = $currentRunLog | Select-String 'CERTIFICATE_UNKNOWN|certificate unknown' | Select-Object -Last 1
            }
        } until ($request -or $trustError -or (Get-Date) -gt $deadline)

        if ($request) {
            Write-Host 'Client API connection: OK' -ForegroundColor Green
        } elseif ($trustError) {
            Write-Warning 'The client still rejected the CA. Reboot that LDPlayer instance once, then run this installer again.'
        } else {
            Write-Warning 'No first API request was observed yet. Keep the server running and inspect var\logs\gateway.log.'
        }
    }

    Write-Host ''
    Write-Host 'Installation complete. The server remains running in the background.' -ForegroundColor Green
    Write-Host 'Use Stop-Server.cmd to stop it.'
} catch {
    Write-Host ''
    Write-Host "INSTALL-AND-RUN FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Save-FailureEvidence $_.Exception.Message
    exit 1
}
