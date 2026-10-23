param([string]$LlvmRoot = $env:LEVIPILOT_LLVM_ROOT)

$ErrorActionPreference = 'Stop'
if (-not $LlvmRoot) { throw 'Specify -LlvmRoot or LEVIPILOT_LLVM_ROOT (LLVM 22 required)' }
$compiler = Join-Path (Resolve-Path -LiteralPath $LlvmRoot).Path 'bin/clang-cl.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw "Compiler missing: $compiler" }
$compilerVersion = & $compiler --version
if ($LASTEXITCODE -ne 0 -or $compilerVersion[0] -notmatch 'clang version 22\.') {
    throw "LLVM 22 required: $compilerVersion"
}
$compilerVersion | Write-Output
$env:LEVIPILOT_LLVM_ROOT = (Resolve-Path -LiteralPath $LlvmRoot).Path
$env:PATH = (Join-Path $env:LEVIPILOT_LLVM_ROOT 'bin') + ';' + $env:PATH
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$logDirectory = Join-Path $repoRoot 'out'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$buildLog = Join-Path $logDirectory 'ci-mod-build.log'

Push-Location (Join-Path $repoRoot 'mod/probe')
try {
    # Explicit tools avoid the older clang bundled with Visual Studio. Neither
    # xmake's installation nor the SDK needs a machine-specific path patch.
    & xmake f -y -p windows -a x64 -m release --target_type=client `
        "--cc=$compiler" "--cxx=$compiler" 2>&1 | Tee-Object -FilePath $buildLog
    if ($LASTEXITCODE -ne 0) { throw "xmake configure failed; see $buildLog" }
    & xmake -j4 -y 2>&1 | Tee-Object -FilePath $buildLog -Append
    if ($LASTEXITCODE -ne 0) { throw "xmake build failed; see $buildLog" }
    foreach ($name in @('probe.dll', 'probe.pdb', 'manifest.json')) {
        $artifact = Join-Path 'bin/probe' $name
        if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Missing output: $artifact" }
    }
    Get-FileHash -LiteralPath 'bin/probe/probe.dll' -Algorithm SHA256
} finally {
    Pop-Location
}
