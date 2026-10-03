$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$taskRuntime = Join-Path $taskRoot 'runtime'
if (Test-Path -LiteralPath (Join-Path $taskRuntime 'READY')) { exit 0 }
if (Test-Path -LiteralPath $taskRuntime) { throw 'runtime already exists but is incomplete. Preserve it and extract a fresh release in another folder.' }
$taskArchive = Join-Path $taskRoot 'runtime-windows-x64.zip'
if (-not (Test-Path -LiteralPath $taskArchive)) {
    throw 'This is the core source package. Follow docs/PORTABLE_RUNTIME.md to prepare runtime, or use the complete portable release. No system settings were changed.'
}
$taskExpected = (Get-Content -LiteralPath (Join-Path $taskRoot 'runtime-windows-x64.sha256') -Raw).Trim()
if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskExpected) { throw 'Runtime archive checksum mismatch.' }
$taskStaging = Join-Path $taskRoot ('var\runtime-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskStaging -Force | Out-Null
Expand-Archive -LiteralPath $taskArchive -DestinationPath $taskStaging
if (-not (Test-Path -LiteralPath (Join-Path $taskStaging 'python\python.exe'))) { throw 'Python is missing from runtime archive.' }
Set-Content -LiteralPath (Join-Path $taskStaging 'READY') -Value '1' -Encoding Ascii
if (-not ([IO.Path]::GetFullPath($taskStaging).StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase))) { throw 'Staging directory escaped the project.' }
if ([IO.Path]::GetFullPath($taskRuntime) -ne (Join-Path $taskRoot 'runtime')) { throw 'Invalid runtime destination.' }
Move-Item -LiteralPath $taskStaging -Destination $taskRuntime
