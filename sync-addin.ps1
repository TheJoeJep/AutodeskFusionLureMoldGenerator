# Copy the add-in into Fusion's AddIns folder.
#
# The add-in is installed as a real copy, not a junction, because Fusion's
# add-in scanner is not reliable about following reparse points. That means
# edits to the source here do NOT reach Fusion until this script is run.
#
# After running it, restart the add-in in Fusion:
#   Utilities > Add-Ins > Scripts and Add-Ins > Add-Ins tab
#   select "LureMoldGenerator" > Stop, then Run
# (or just restart Fusion). Fusion caches an add-in's Python modules once
# loaded, so a running add-in keeps executing the old code otherwise.

$source = Join-Path $PSScriptRoot 'LureMoldGenerator'
$target = Join-Path $env:APPDATA 'Autodesk\Autodesk Fusion 360\API\AddIns\LureMoldGenerator'

if (-not (Test-Path $source)) {
    Write-Error "Source not found: $source"
    exit 1
}

Get-ChildItem -Path $source -Filter '__pycache__' -Recurse -Directory |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

if (Test-Path $target) {
    Remove-Item -Recurse -Force $target
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force

$count = (Get-ChildItem -Path $target -Recurse -File).Count
Write-Output "Copied $count files to:"
Write-Output "  $target"
Write-Output ''
Write-Output 'Now restart the add-in in Fusion (Utilities > Add-Ins > Scripts and Add-Ins).'
