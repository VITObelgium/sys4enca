"""Check if all dependencies are installed, and try to install them automatically if possible.

On a Windows installation based on OSGeo4W, we run the OSGeo4W installer to install rasterio, geopandas, scipy, etc.
On other systems, we present a warning message and expect the user to take care of this installation.
"""
import importlib
import os
import subprocess
import tempfile
from importlib.metadata import PackageNotFoundError, version, distributions  # pragma: no cover

from PyQt5.QtWidgets import QMessageBox, QDialog
from pkg_resources import parse_version
from qgis.core import Qgis, QgsMessageLog

from .pip_install_dialog import PipInstallDialog
from .marvin_qgis_tools import osgeo4w

_package_dist_name = 'sys4enca'  # Python package with core functionality
_min_version = '0.5.0'  # Minimum required package version for the plugin.
_version_next = '0.6.0'  # Next package version which may no longer be compatible with this version of the plugin.
_repo_url = 'https://artifactory.vgt.vito.be/api/pypi/python-packages/simple'


def get_python_interpreter():
    if os.name == 'nt':
        # On Windows, use the interpreter from OSGEO4W_ROOT\bin, *not* the one in PYTHONHOME, because that one fails to
        # import the ssl module!
        osgeo4w_root = os.getenv('OSGEO4W_ROOT')
        if osgeo4w_root is not None:
            return os.path.join(osgeo4w_root, 'bin', 'python3.exe')
        return 'python3'  # On windows, not having OSGEO4W_ROOT is unlikely, but just in case...
    elif os.name == 'posix':  # Linux, Mac, ...
        pythonhome = os.getenv('PYTHONHOME')
        if pythonhome is not None:  # Normally on Mac, we have PYTHONHOME=/Applications/QGIS[-LTR].app/Contents/MacOS
            return os.path.join(pythonhome, 'bin', 'python3')
        return 'python3'  # Should be ok on Linux
    else:
        QgsMessageLog.logMessage(f'os.name "{os.name}" not recognized', level=Qgis.Warning)
        return 'python3'


def install_pip_deps():
    """Install remaining dependencies using pip."""
    install_dialog = PipInstallDialog()
    install_dialog.message.setText('ENCA plugin must install the sys4enca core package (and possible dependencies) '
                                   'using pip.  OK to download and install?')
    answer = install_dialog.exec()
    if answer != QDialog.Accepted:  # Installation cancelled.
        return False

    proxy_option = []
    if install_dialog.proxyGroup.isChecked():
        proxy_user = install_dialog.proxyUser.text()
        proxy_pass = install_dialog.proxyPass.text()
        proxy_host = install_dialog.proxyHost.text()
        proxy_port = install_dialog.proxyPort.text()
        if len(proxy_user) and len(proxy_pass):
            auth = f'{proxy_user}:{proxy_pass}@'
        else:
            auth = ''
        proxy_option = ['--proxy', f'http://{auth}{proxy_host}:{proxy_port}']

    python = get_python_interpreter()

    # Generate a constraints file for pip, to prevent pip from *downgrading* existing packages
    # (except existing sys4enca installations):
    with tempfile.NamedTemporaryFile(mode='w+t', delete=False, prefix='sys4enca_constraints') as cf:
        for dist in distributions():
            dist_name = dist.metadata['name']
            if dist_name == _package_dist_name:
                # sys4enca may be downgraded if the user wants to install an older plugin version
                continue
            cf.write(f'{dist_name}>={dist.version}\n')

    try:
        # now install our package
        subprocess.run([f'{python}', '-m', 'pip', 'install', '-U',
                        '-c', cf.name,  # use constraints file to prevent downgrades
                        f'{_package_dist_name}>={_min_version},<{_version_next}',
                        '--index-url', f'{_repo_url}',
                        '--extra-index-url', 'https://pypi.org/simple'] + proxy_option,
                       capture_output=True, check=True, text=True)
    except subprocess.CalledProcessError as e:
        QMessageBox.warning(None, 'ENCA Installation', f'Installation of package {_package_dist_name} using pip failed.  '
                                                       'ENCA may not work correctly\n'
                                                       f'Exit status: {e.returncode}, see message log for output.')
        QgsMessageLog.logMessage(f'pip install failed, stdout: {e.stdout}, stderr: {e.stderr}.', level=Qgis.Critical)
        return False
    finally:
        # Clean up the constraints file
        os.remove(cf.name)
    return True


def check_dependencies():
    """Try to import required extra packages, and try to install them if it fails."""
    # First try to install packages available from OSGeo4W
    if not osgeo4w.check_packages({'geopandas': 'python3-geopandas',
                                   'matplotlib': 'python3-matplotlib',
                                   'netCDF4': 'python3-netcdf4',
                                   'rasterio': 'python3-rasterio',
                                   'rtree': 'python3-rtree',
                                   'sklearn': 'python3-scikit-learn',
                                   'scipy': 'python3-scipy',
                                   'pip': 'python3-pip',
                                   'setuptools': 'python3-setuptools'}):
        return False

    # Now check installed package version; if package is not available or not the right version, run pip.
    run_pip = True
    try:
        installed_version = version(_package_dist_name)
        QgsMessageLog.logMessage((f'Current {_package_dist_name} version: {installed_version}.'), level=Qgis.Info)
        if parse_version(installed_version) >= parse_version(_min_version):
            run_pip = False  # We have everything we need, in the right version.
    except PackageNotFoundError:
        QgsMessageLog.logMessage(f'No version of {_package_dist_name} core package is currently installed.',
                                 level=Qgis.Info)

    if run_pip:
        QgsMessageLog.logMessage(f'{_package_dist_name} version {_min_version} will be installed using pip.',
                                 level=Qgis.Info)
        if not install_pip_deps():
            QMessageBox.warning(None, 'ENCA Installation', 'The installation is not complete.  The ENCA plugin may not '
                                                           'work correctly.')
            return False

    # Check if we can import the right version:
    # If a previous version of enca was already loaded before installation we have to restart QGIS in order to
    # import the new version.
    #
    # If enca wasn't installed previously, 'import enca' might still fail due to the importlib cache.  Invalidate the
    # caches so we are sure that we can import the newly installed enca:
    importlib.invalidate_caches()
    import enca
    if parse_version(enca.__version__) < parse_version(_min_version):
        QMessageBox.warning(None, 'Restart QGIS', f'The {_package_dist_name} package was updated.  Please restart '
                                                  'QGIS before using the ENCA plugin.')
        return False

    return True
