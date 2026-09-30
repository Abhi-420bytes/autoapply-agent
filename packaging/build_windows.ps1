# Build the AutoApply Windows app and installer (no Docker needed to run it).
# Usage (PowerShell, from the repo root):  packaging\build_windows.ps1 -Version 1.0.0
# Needs: Node.js 20 + npm, Python 3.11 with the backend installed (and pyinstaller),
#        Inno Setup 6 (iscc) for the installer (otherwise a .zip is produced).
# Created by Abhiram (Challa Abhiram). MIT license.
param([string]$Version = "1.0.0", [string]$Python = "python")
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
$Stage = Join-Path $Here "stage"
$NodeVersion = "20.19.2"
$TectonicVersion = "0.17.0"

Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$Stage\node", "$Stage\bin" | Out-Null

# 1) dashboard: self-contained Next.js server
Push-Location "$Root\frontend"
npm ci --no-audit --no-fund
$env:NEXT_TELEMETRY_DISABLED = "1"
npm run build
Pop-Location
Copy-Item -Recurse "$Root\frontend\.next\standalone" "$Stage\dashboard"
Copy-Item -Recurse "$Root\frontend\.next\static" "$Stage\dashboard\.next\static"
Copy-Item -Recurse "$Root\frontend\public" "$Stage\dashboard\public"

# 2) Node.js runtime (official Windows build)
$tmp = New-Item -ItemType Directory -Force (Join-Path $env:TEMP "autoapply-build")
Invoke-WebRequest "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip" -OutFile "$tmp\node.zip"
Expand-Archive "$tmp\node.zip" -DestinationPath $tmp -Force
Copy-Item "$tmp\node-v$NodeVersion-win-x64\node.exe" "$Stage\node\node.exe"

# 3) Tectonic (LaTeX engine)
$tt = "tectonic-$TectonicVersion-x86_64-pc-windows-msvc.zip"
Invoke-WebRequest "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40$TectonicVersion/$tt" -OutFile "$tmp\tectonic.zip"
Expand-Archive "$tmp\tectonic.zip" -DestinationPath "$Stage\bin" -Force

# 4) the app folder
$env:AUTOAPPLY_VERSION = $Version
& $Python -m PyInstaller --noconfirm --clean --distpath "$Here\dist" --workpath "$Here\build" "$Here\autoapply.spec"

# 5) installer (Inno Setup), else a zip
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc -and (Test-Path "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe")) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if ($iscc) {
  & $iscc "/DAppVersion=$Version" "/DSourceDir=$Here\dist\AutoApply" "/DOutDir=$Here\dist" "$Here\windows_installer.iss"
} else {
  Compress-Archive -Path "$Here\dist\AutoApply\*" -DestinationPath "$Here\dist\AutoApply-windows.zip" -Force
}
Write-Host "built into $Here\dist"
