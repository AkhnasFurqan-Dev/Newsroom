param(
    [string]$BuildPath = "dist\release-candidate",
    [string]$OutputPath = "dist\installer"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not [System.IO.Path]::IsPathRooted($BuildPath)) {
    $BuildPath = Join-Path $root $BuildPath
}
if (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $root $OutputPath
}

$sourceDir = Join-Path $BuildPath "Newsroom"
foreach ($name in @("Newsroom.exe", "NewsroomWorker.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceDir $name) -PathType Leaf)) {
        throw "The packaged application is incomplete. Missing '$name' in '$sourceDir'."
    }
    $brandIcon = Join-Path $root "assets\brand\newsroom\newsroom.ico"
    if (-not (Test-Path -LiteralPath $brandIcon -PathType Leaf)) {
        throw "The approved Newsroom installer icon is missing: $brandIcon"
    }
}

$compilerCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
$compilerCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
$compiler = if ($compilerCommand) { $compilerCommand.Source } else {
    $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $compiler) {
    throw "Inno Setup 6 compiler not found. Install the official JRSoftware Inno Setup package."
}

$versionLine = Select-String -Path (Join-Path $root "pyproject.toml") `
    -Pattern '^\s*version\s*=\s*"(?<version>[^"]+)"' |
    Select-Object -First 1
if (-not $versionLine) {
    throw "The application version was not found in pyproject.toml."
}
$appVersion = $versionLine.Matches[0].Groups["version"].Value

$bootstrapDir = Join-Path $root ".build\windows"
$bootstrapper = Join-Path $bootstrapDir "WebView2Bootstrapper.exe"
New-Item -ItemType Directory -Force -Path $bootstrapDir,$OutputPath | Out-Null
if (-not (Test-Path -LiteralPath $bootstrapper -PathType Leaf)) {
    Invoke-WebRequest `
        -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" `
        -OutFile $bootstrapper `
        -TimeoutSec 120
}
$signature = Get-AuthenticodeSignature -FilePath $bootstrapper
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
    throw "The WebView2 bootstrapper did not pass Microsoft Authenticode verification."
}

Write-Host "Compiling the per-user Newsroom installer."
& $compiler `
    "/DAppVersion=$appVersion" `
    "/DSourceDir=$sourceDir" `
    "/DWebView2Bootstrapper=$bootstrapper" `
    "/O$OutputPath" `
    "packaging\Newsroom.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed to compile the installer."
}

$installer = Join-Path $OutputPath "NewsroomSetup.exe"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "The installer compiler returned success without producing '$installer'."
}
$size = (Get-Item -LiteralPath $installer).Length
Write-Host "Installer ready: $installer ($size bytes). Model runtime and model weights are not bundled."
