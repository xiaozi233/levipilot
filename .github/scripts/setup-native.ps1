param(
    [Parameter(Mandatory)][string]$ToolsDirectory,
    [switch]$SkipXmake
)

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
if (-not (Test-Path -LiteralPath (Join-Path $llvmDir 'bin/clang-cl.exe') -PathType Leaf)) {
    $installer = Join-Path $toolsRoot 'LLVM-22.1.8-win64.exe'
    Get-VerifiedDownload `
        'https://github.com/llvm/llvm-project/releases/download/llvmorg-22.1.8/LLVM-22.1.8-win64.exe' `
        $installer '16e5709785fef73c854646241c4a92c5cd574318d1b33c63330dd7721903e55c'
    $installed = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$llvmDir") `
        -Wait -PassThru -WindowStyle Hidden
    if ($installed.ExitCode -ne 0) { throw "LLVM installation failed: $($installed.ExitCode)" }
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
}

$envLines = @("LEVIPILOT_LLVM_ROOT=$llvmDir")
$pathLines = @((Join-Path $llvmDir 'bin'))

if (-not $SkipXmake) {
    $xmakeDir = Join-Path $toolsRoot 'xmake'
    $xmakeBin = Join-Path $xmakeDir 'xmake'
    if (-not (Test-Path -LiteralPath (Join-Path $xmakeBin 'xmake.exe') -PathType Leaf)) {
        $xmakeZip = Join-Path $toolsRoot 'xmake-3.1.2.zip'
        Get-VerifiedDownload `
            'https://github.com/xmake-io/xmake/releases/download/v3.1.2/xmake-v3.1.2.win64.zip' `
            $xmakeZip 'be1c5fd5abec08ac14004daca7bfb1ec6e1f2f2dea94877de8f44dc6e0aed1b1'
        Expand-Archive -LiteralPath $xmakeZip -DestinationPath $xmakeDir -Force
        Remove-Item -LiteralPath $xmakeZip -Force -ErrorAction SilentlyContinue
    }
    $envLines += @(
        "XMAKE_GLOBALDIR=$(Join-Path $toolsRoot 'home')"
        "XMAKE_PKG_INSTALLDIR=$(Join-Path $toolsRoot 'packages')"
    )
    $pathLines += $xmakeBin
}

$envLines | Out-File -LiteralPath $env:GITHUB_ENV -Encoding utf8 -Append
$pathLines | Out-File -LiteralPath $env:GITHUB_PATH -Encoding utf8 -Append

& (Join-Path $llvmDir 'bin/clang-cl.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'LLVM clang-cl did not start' }
& (Join-Path $llvmDir 'bin/clang++.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'LLVM clang++ did not start' }
if (-not $SkipXmake) {
    & (Join-Path $xmakeBin 'xmake.exe') --version
    if ($LASTEXITCODE -ne 0) { throw 'xmake did not start' }
}
