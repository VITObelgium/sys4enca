# Copyright (2022) VITO NV.
"""Functions to interact with the OSGeo4W installer."""

import importlib
import subprocess
import threading
import os
import time

from PyQt5.QtWidgets import QMessageBox
from qgis.core import Qgis, QgsMessageLog
from qgis.utils import iface


class ExitTask(threading.Thread):
    """Background task to exit QGIS.

    We can only run iface.actionExit().trigger() after QGIS initialization is complete => use a background task to
    wait for that, in case we are running before/during QGIS initialization."""
    def run(self, *args, **kwargs):
        while True:
            iface.actionExit().trigger()
            time.sleep(0.5)


def have_permission(osgeo4w_root):
    """Check if we have writing permission for the OSGeo4W root directory by opening a test file."""
    testfile = os.path.join(osgeo4w_root, 'inca_install_test.txt')
    try:
        with open(testfile, 'wt'):
            pass
    except BaseException:  # If we don't have permission this should raise 'PermissionError', but let's catch possible
        # other exceptions as well.
        return False
    # If no exception was raised, we have to remove the file again:
    os.remove(testfile)
    return True


def check_packages(packages):
    """Check if required packages are available, otherwise run osgeo4w-setup to install them, and exit QGIS.

    :param packages: dictionary of {python_package: osgeo4w_package}, e.g. {'rasterio': 'python3-rasterio'}
    :return: False if we are on OSGeo4W but all required packages are not installed (yet).
    """
    osgeo_root = os.getenv('OSGEO4W_ROOT')
    if osgeo_root is None:
        # We are on linux, mac, or (less likely) a non-OSGeo4W QGIS version for windows -> abort and try to install
        # using pip
        return True

    try:
        for python_package in packages:
            importlib.import_module(python_package)
        # If all imports succeed: return True
        return True
    except ModuleNotFoundError as e:
        QgsMessageLog.logMessage(f'Missing package {e}, need to run OSGeo4W setup.', level=Qgis.Info)
    except Exception as e:
        QgsMessageLog.logMessage(f'Exception when importing package: {e}, try to run OSGeo4W setup.', level=Qgis.Info)

    # We are on OSGeo4W -> ask to run the installer
    answer = QMessageBox.question(None, 'Plugin Installation',
                                  'We need to install a few extra packages.  Click OK to start the OSGeo4W '
                                  'installer and exit QGIS.  Click Cancel to abort the plugin installation.',
                                  QMessageBox.Ok | QMessageBox.Cancel)
    if answer == QMessageBox.Ok:
        # Run osgeo4w-setup and exit QGIS:
        osgeo4w_setup = f'{osgeo_root}\\bin\\osgeo4w-setup.exe'
        args = ['--advanced', '--autoaccept', '--root', osgeo_root]
        # add required packages to list of installer arguments:
        for osgeo4w_pkg in packages.values():
            args.append('--packages')
            args.append(osgeo4w_pkg)
        # Run command using PowerShell:
        argumentlist = '@(' + ', '.join(f'\"{arg}\"' for arg in args) + ')'
        # Location of powershell executable.  %SystemRoot% is likely C:\WINDOWS, but can never be too sure...
        powershell_exe = os.getenv('SystemRoot') + r'\System32\WindowsPowerShell\v1.0\powershell.exe'
        # try running with elevated privileges if we don't have write permissions:
        verb_opt = '-Verb RunAs ' if not have_permission(osgeo_root) else ''
        subprocess.Popen([
            powershell_exe, '-Command',
            f"& {{ Start-Process \"{osgeo4w_setup}\" -ArgumentList {argumentlist} {verb_opt}}}"
        ])
        # Exit QGIS in case setup needs to update files which are in use (e.g. the qgis executable, GDAL, ...)
        ExitTask(daemon=True).start()
    return False
