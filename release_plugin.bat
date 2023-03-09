@echo off
REM Build a zip file from which the plugin can be installed.
REM requires 7-zip, git, pyqt command line tools, and QGIS plugin builder tool (pb_tool)
REM Run this script from the repository root.
set TEMP_ENCA=%Temp%\temp_enca_plugin
echo Removing existing %TEMP_ENCA% directory
rmdir /s /q %TEMP_ENCA%
git archive --format tar --worktree-attributes HEAD enca_plugin ^
    --prefix temp_enca_plugin/marvin_qgis_tools/ --add-file qgis_tools/src/marvin_qgis_tools/osgeo4w.py ^
    --prefix temp_ ^
    | 7z x -si -ttar -o%Temp% -y
pushd %TEMP_ENCA%
REM Genenerate .qm files from .ts files:
pb_tool translate
pb_tool zip
popd
move %TEMP_ENCA%\zip_build\enca_plugin.zip .
