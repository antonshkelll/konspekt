param([Parameter(Mandatory=$true)][string]$Destination, [Parameter(Mandatory=$true)][string]$NodeExe)
$ErrorActionPreference = 'Stop'
$packageRoot = Split-Path -Parent $PSScriptRoot
$runtimeSource = Join-Path $packageRoot 'siwc'
if (-not (Test-Path -LiteralPath $NodeExe)) { throw 'Node executable missing' }
if (-not (Test-Path -LiteralPath (Join-Path $runtimeSource 'node_modules'))) { throw 'Run npm ci in siwc first' }
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
foreach ($entry in @('bridge.mjs','package.json','package-lock.json','LICENSE','THIRD_PARTY_NOTICES.md','NOTICE.md','local','node_modules')) {
    Copy-Item -LiteralPath (Join-Path $runtimeSource $entry) -Destination $Destination -Recurse -Force
}
$nodeDestination = Join-Path $Destination 'node-bin'
New-Item -ItemType Directory -Path $nodeDestination -Force | Out-Null
Copy-Item -LiteralPath $NodeExe -Destination (Join-Path $nodeDestination 'node.exe')
Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $NodeExe) 'LICENSE') -Destination (Join-Path $nodeDestination 'LICENSE')
Write-Output "Packaged authentication runtime: $Destination"
