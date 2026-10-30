param([switch]$ChatPreview, [switch]$ExecutionPreview)
$ErrorActionPreference = 'Stop'
if ($ChatPreview) { Write-Output 'Старая пробная сборка заменена единым приложением.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Сначала создайте .venv и установите requirements-dev.txt. См. README.md.'
}
$stagingRoot = Join-Path $projectRoot 'build\portable-output'
$applicationName = 'TenderAssistantExecutionPreview'
$entryPoint = 'run_execution.py'
& $pythonExe scripts/build_app_icon.py
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить значок приложения.' }
$appIcon = Join-Path $projectRoot 'tender_app\assets\tender-assistant.ico'
$seedDatabase = Join-Path $projectRoot ('build\execution-seed-' + [Guid]::NewGuid().ToString('N') + '\tenders.db')
& $pythonExe -m scripts.create_execution_seed --output $seedDatabase
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить вымышленную базу.' }
# PyInstaller пересоздаёт только staging, а не папку с пользовательской data.
$portableBuildPath = $env:PATH
try {
    # External tools (for example Poppler) may ship an incompatible ICU DLL.
    # Qt must resolve Windows system DLLs rather than pick these up from PATH.
    $env:PATH = (Split-Path -Parent $pythonExe) + ';' + (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    & $pythonExe -m PyInstaller --noconfirm --onedir --windowed --icon $appIcon --collect-all playwright --collect-all py7zr --workpath (Join-Path $projectRoot 'build\portable-work') --distpath $stagingRoot --specpath (Join-Path $projectRoot 'build') --name $applicationName --add-data ($seedDatabase + ';seed') --add-data ((Split-Path -Parent $appIcon) + ';tender_app/assets') $entryPoint
} finally {
    $env:PATH = $portableBuildPath
}
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller завершился с ошибкой.' }
$portableRoot = Join-Path $projectRoot ('dist\' + $applicationName)
$stagedApp = Join-Path $stagingRoot $applicationName
$documentRoot = Join-Path $stagedApp 'Документы'
New-Item -ItemType Directory -Path $documentRoot -Force | Out-Null
foreach ($folder in @('1-ДЕМО-101', '2-ДЕМО-202', '3-ДЕМО-202', '4-ДЕМО-404')) {
    New-Item -ItemType Directory -Path (Join-Path $documentRoot ('Тендеры\' + $folder)) -Force | Out-Null
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\План_исполнения_тендеров.md') -Destination $stagedApp -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $stagedApp 'ПРОЧИТАЙТЕ.md') -Force
foreach ($documentName in @('Результаты_реализации.md', 'Журнал_пробной_работы.md')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ('docs\' + $documentName)) -Destination $stagedApp -Force
}
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut((Join-Path $stagedApp 'Тендеры.lnk'))
# Относительный .cmd обеспечивает запуск после перемещения portable-папки.
$launcher = '@echo off' + "`r`n" + ('start "" "%~dp0{0}.exe"' -f $applicationName) + "`r`n"
[IO.File]::WriteAllText((Join-Path $stagedApp 'Тендеры.cmd'), $launcher, [Text.Encoding]::ASCII)
$shortcut.TargetPath = Join-Path $portableRoot 'Тендеры.cmd'
$shortcut.WorkingDirectory = $portableRoot
$shortcut.Description = 'Локальный реестр тендеров'
$shortcut.IconLocation = (Join-Path $portableRoot ($applicationName + '.exe')) + ',0'
$shortcut.Save()
& $pythonExe scripts/package_portable.py
if ($LASTEXITCODE -ne 0) { throw 'Упаковка ZIP завершилась с ошибкой.' }
$runningApp = Get-Process -Name $applicationName -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $portableRoot ($applicationName + '.exe')) }
if ($runningApp) { throw 'Закройте приложение перед обновлением. Новая сборка сохранена в build\portable-output.' }
New-Item -ItemType Directory -Path $portableRoot -Force | Out-Null
$existingDatabase = Join-Path $portableRoot 'data\tenders.db'
$beforeHash = if (Test-Path -LiteralPath $existingDatabase) { (Get-FileHash -LiteralPath $existingDatabase -Algorithm SHA256).Hash } else { $null }
foreach ($entry in Get-ChildItem -LiteralPath $stagedApp) {
    $target = Join-Path $portableRoot $entry.Name
    if (($entry.Name -eq 'Документы' -or $entry.Name -eq 'Журнал_пробной_работы.md') -and (Test-Path -LiteralPath $target)) { continue }
    Copy-Item -LiteralPath $entry.FullName -Destination $portableRoot -Recurse -Force
}
if ($beforeHash -and (Get-FileHash -LiteralPath $existingDatabase -Algorithm SHA256).Hash -ne $beforeHash) {
    throw 'Рабочая база неожиданно изменилась при обновлении.'
}
if ($beforeHash) { Write-Output 'Рабочая база сохранена: SHA-256 до и после обновления совпадает.' }
Write-Output "Готово: $portableRoot"
