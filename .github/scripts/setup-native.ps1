param([Parameter(Mandatory)][string]$ToolsDirectory)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $ToolsDirectory -Force | Out-Null
$toolsRoot = (Resolve-Path -LiteralPath $ToolsDirectory).Path

function Get-VerifiedDownload([string]$Url, [string]$Destination, [string]$Sha256) {
    Invoke-WebRequest -Uri $Url -OutFile $Destination
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ne $Sha256) {
        throw "Download checksum mismatch: $Destination"
    }
}

$llvmDir = Join-Path $toolsRoot 'llvm22'
$installer = Join-Path $toolsRoot 'LLVM-22.1.8-win64.exe'
Get-VerifiedDownload `
    'https://github.com/llvm/llvm-project/releases/download/llvmorg-22.1.8/LLVM-22.1.8-win64.exe' `
    $installer '16e5709785fef73c854646241c4a92c5cd574318d1b33c63330dd7721903e55c'
$installed = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$llvmDir") `
    -Wait -PassThru -WindowStyle Hidden
if ($installed.ExitCode -ne 0) { throw "LLVM installation failed: $($installed.ExitCode)" }

$xmakeZip = Join-Path $toolsRoot 'xmake-3.1.2.zip'
Get-VerifiedDownload `
    'https://github.com/xmake-io/xmake/releases/download/v3.1.2/xmake-v3.1.2.win64.zip' `
    $xmakeZip 'be1c5fd5abec08ac14004daca7bfb1ec6e1f2f2dea94877de8f44dc6e0aed1b1'
$xmakeDir = Join-Path $toolsRoot 'xmake'
Expand-Archive -LiteralPath $xmakeZip -DestinationPath $xmakeDir -Force
$xmakeBin = Join-Path $xmakeDir 'xmake'

@(
    "LEVIPILOT_LLVM_ROOT=$llvmDir"
    "XMAKE_GLOBALDIR=$(Join-Path $toolsRoot 'home')"
    "XMAKE_PKG_INSTALLDIR=$(Join-Path $toolsRoot 'packages')"
) | Out-File -LiteralPath $env:GITHUB_ENV -Encoding utf8 -Append
@((Join-Path $llvmDir 'bin'), $xmakeBin) |
    Out-File -LiteralPath $env:GITHUB_PATH -Encoding utf8 -Append
& (Join-Path $llvmDir 'bin/clang-cl.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'LLVM compiler did not start' }
& (Join-Path $xmakeBin 'xmake.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'xmake did not start' }
