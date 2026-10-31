param(
    [string]$PythonPath = ".venv\Scripts\python.exe",
    [string]$OutputPath = "dist\release-candidate"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not [System.IO.Path]::IsPathRooted($PythonPath)) {
    $PythonPath = Join-Path $root $PythonPath
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python environment not found at '$PythonPath'. Install the pinned build dependencies first."
}
if (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $root $OutputPath
}

$playwrightPin = Select-String -Path (Join-Path $root "pyproject.toml") `
    -Pattern '^\s*"playwright==(?<version>[^"]+)"' |
    Select-Object -First 1
if (-not $playwrightPin) {
    throw "The pinned Playwright dependency was not found in pyproject.toml."
}
$expectedVersion = $playwrightPin.Matches[0].Groups["version"].Value
$installedVersion = (& $PythonPath -c "from importlib.metadata import version; print(version('playwright'))").Trim()
if ($LASTEXITCODE -ne 0 -or $installedVersion -ne $expectedVersion) {
    throw "Playwright $expectedVersion is required for this build; found '$installedVersion'."
}

$buildRoot = Join-Path $root ".build\windows"
$browserRoot = Join-Path $buildRoot "playwright-browsers-$expectedVersion"
$workRoot = Join-Path $buildRoot "pyinstaller"
New-Item -ItemType Directory -Force -Path $browserRoot,$workRoot,(Split-Path -Parent $OutputPath) | Out-Null
$env:PLAYWRIGHT_BROWSERS_PATH = $browserRoot
$env:CHANNEL_PRESS_PLAYWRIGHT_BROWSERS = $browserRoot

Write-Host "Installing the Chromium revision pinned by Playwright $expectedVersion."
& $PythonPath -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "Playwright Chromium installation failed."
}

Write-Host "Building Newsroom.exe and NewsroomWorker.exe with the local renderer."
& $PythonPath -m PyInstaller --noconfirm --clean `
    --distpath $OutputPath `
    --workpath $workRoot `
    packaging\Newsroom.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the Windows application."
}

$appPath = Join-Path $OutputPath "Newsroom\Newsroom.exe"
$workerPath = Join-Path $OutputPath "Newsroom\NewsroomWorker.exe"
foreach ($executable in @($appPath,$workerPath)) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Expected packaged executable was not produced: $executable"
    }
}
$browserDirs = @(Get-ChildItem -LiteralPath (Join-Path $OutputPath "Newsroom\_internal\pw-browsers") `
    -Directory -Filter "chromium-*")
if ($browserDirs.Count -ne 1) {
    throw "Expected exactly one pinned Chromium revision in the packaged browser directory."
}
$requiredFiles = @(
    (Join-Path $browserDirs[0].FullName "chrome-win\chrome.exe"),
    (Join-Path $OutputPath "Newsroom\_internal\channel_press\layout\templates\postprocess.js"),
    (Join-Path $OutputPath "Newsroom\_internal\channel_press\web\static\vendor\paged.polyfill.min.js"),
    (Join-Path $OutputPath "Newsroom\_internal\channel_press\publishing\templates\epaper.html.j2"),
    (Join-Path $OutputPath "Newsroom\_internal\assets\brand\newsroom\newsroom.ico")
)
foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "The packaged renderer is incomplete; required asset is missing: $requiredFile"
    }
}
Write-Host "Build ready at $OutputPath. This is not an installer or clean-machine certification."
