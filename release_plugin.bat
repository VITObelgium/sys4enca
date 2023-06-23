@echo off
REM Build a zip file from which the plugin can be installed.
if "%~1" == "" (
    echo Please specify a git revision ^(e.g. HEAD, 0.1.0, ...^) to build a plugin .zip file from.
    echo Make sure 7-zip, pyqt command line tools and QGIS plugin builder ^(pb_tool^) are available.
    echo Run this script from the repository root.
    echo:
    echo Usage:
    echo:
    echo     %0 ^<git-rev^>
    exit /b 1
)
set GIT_REV=%1
for /f %%i in ('git rev-parse --short %GIT_REV%') do set COMMIT_HASH=%%i
set TEMP_ENCA=%Temp%\enca_plugin_%COMMIT_HASH%
mkdir %TEMP_ENCA%
git archive --format tar --worktree-attributes %GIT_REV% enca_plugin ^
    --prefix enca_plugin/marvin_qgis_tools/ --add-file qgis_tools/src/marvin_qgis_tools/osgeo4w.py ^
    --prefix "" ^
    | 7z x -si -ttar -o%TEMP_ENCA% -y
pushd %TEMP_ENCA%\enca_plugin
REM Genenerate .qm files from .ts files:
pb_tool translate
pb_tool zip
popd
move %TEMP_ENCA%\enca_plugin\zip_build\enca_plugin.zip .\enca_plugin_%COMMIT_HASH%.zip
echo Removing %TEMP_ENCA%
rmdir /s /q %TEMP_ENCA%
