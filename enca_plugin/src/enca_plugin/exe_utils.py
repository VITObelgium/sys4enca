import subprocess
import os
import glob
from pathlib import Path
import stat
#import sys

from qgis.core import Qgis, QgsMessageLog

from enca_plugin.cfg import MSG_LOG_TAG

_THIS_DIRECTORY = Path(__file__).parent
_WHEEL_GLOB = "sys4enca*.whl"
_PACKAGE = "enca"

# Regarding subprocess calls (run, Popen, etc):
# When command is a single str, then use shell=True to execute it.
# When command is a sequence (e.g. list), shell should be False.
# On Windows, shell=True automatically hides the command window. subprocess STARTUPINFO should be able to do the same.
def run_in_enca_env(command: str, plugin_dir: Path = _THIS_DIRECTORY, check: bool = True) -> None:
    """Run a CLI command inside the shipped environment and wait for its completion."""
    # Pip commands do not consider the CONDA-like virtual env.
    # To run them on the virtual env, the PYTHONHOME and PYTHONPATH env variables defined by QGIS need to be overruled.
    cmd_env = _get_cmd_env(plugin_dir)
    full_command = _get_cmd_to_run(command)
    QgsMessageLog.logMessage('Running cmd '+str(full_command), tag=MSG_LOG_TAG, level=Qgis.Info)
    #QgsMessageLog.logMessage(' in env '+str(cmd_env), tag=MSG_LOG_TAG, level=Qgis.Info)
    #QgsMessageLog.logMessage(' with sys.path '+str(sys.path), tag=MSG_LOG_TAG, level=Qgis.Info)    
    if os.name == "nt":
        subprocess.run(full_command, shell=True, cwd=str(plugin_dir), check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=cmd_env)
    else:
        subprocess.run(full_command, shell=True, executable="/bin/bash", cwd=str(plugin_dir), check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=cmd_env)

def run_in_enca_env_no_wait(command: str, plugin_dir: Path = _THIS_DIRECTORY) -> subprocess:
    """Run a CLI command inside the shipped environment, without waiting for its completion."""
    # Pip commands do not consider the CONDA-like virtual env.
    # To run them on the virtual env, the PYTHONHOME and PYTHONPATH env variables defined by QGIS need to be overruled.  
    cmd_env = _get_cmd_env(plugin_dir)
    full_command = _get_cmd_to_run(command)
    QgsMessageLog.logMessage('Running cmd '+str(full_command), tag=MSG_LOG_TAG, level=Qgis.Info)
    #QgsMessageLog.logMessage(' in env '+str(cmd_env), tag=MSG_LOG_TAG, level=Qgis.Info)
    #QgsMessageLog.logMessage(' with sys.path '+str(sys.path), tag=MSG_LOG_TAG, level=Qgis.Info)    
    if os.name == "nt":
        return subprocess.Popen(full_command, shell=True, text=True, stdout=subprocess.PIPE, 
                                stderr=subprocess.STDOUT, cwd=str(plugin_dir), env=cmd_env)
    else:
        return subprocess.Popen(full_command, executable="/bin/bash", shell=True, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(plugin_dir), env=cmd_env)

def _get_cmd_env(plugin_dir: Path = _THIS_DIRECTORY) -> dict:
    cmd_env = dict(os.environ)
    # overwrite PYTHONHOME
    cmd_env['PYTHONHOME'] = str(plugin_dir / 'env')
    # pre-pend PYTHONPATH
    if 'PYTHONPATH' in cmd_env:
        cmd_env['PYTHONPATH'] = str(plugin_dir / 'env' / 'Lib' / 'site-packages') + os.pathsep + cmd_env['PYTHONPATH']
    else:
        cmd_env['PYTHONPATH'] = str(plugin_dir / 'env' / 'Lib' / 'site-packages')

    return cmd_env

def _get_cmd_to_run(command: str) -> str:
    if os.name == "nt":
        #activation_script = "run_in_enca_env.bat"
        activation_script = "activate.bat"
        full_command = f'{activation_script} & {command}'
    else:
        activation_script = "activate.sh"
        full_command = f"source {activation_script} && {command}"
    return full_command

def configure_environment(plugin_dir: Path = _THIS_DIRECTORY) -> None:
    if not is_env_available():
        if isinstance(plugin_dir, str):
            plugin_dir = Path(plugin_dir)
        plugin_dir.mkdir(exist_ok=True)
        # initial installation: first unpack env folder, then install wheel
        setup_environment(plugin_dir)
    else:
        # Install or update the package from the wheel file,
        # without the need to unpack the venv again.
        # This can be convenient for sending enca code updates to users, 
        # that do not require changes to the python venv.
        setup_wheel(plugin_dir, uninstall_first=True)

        # if it's not the first time we don't have to do anything at this moment
        # if constant re-importing of enca is a performance problem
        # one can think of opening a long running shell here

def is_env_available(plugin_dir: Path = _THIS_DIRECTORY) -> bool:
    """Check whether environment folder is available.

    When it is not available, either this is the initial installation (pixi unpack needs to be run) 
    or something went wrong with the installation.
    """
    return os.path.isdir(str(plugin_dir / "env"))

def setup_environment(plugin_dir: Path = _THIS_DIRECTORY) -> None:
    exe_extension = ""
    if os.name == "nt":
        exe_extension = ".exe"
    pixi_pack = str(_THIS_DIRECTORY / f"pixi-pack{exe_extension}")
    environment_archive = str(_THIS_DIRECTORY / "environment.tar")

    if os.path.isfile(pixi_pack) and os.path.isfile(environment_archive):
        QgsMessageLog.logMessage(f'Unpacking pixi environment in {plugin_dir}', tag='Plugins', level=Qgis.Info)
        # Make sure pixi-pack is executable on Linux/Mac
        if os.name != "nt":
            os.chmod(pixi_pack, stat.S_IXUSR)
        # Run pixi to unpack environment.tar file
        subprocess.run(
            [pixi_pack, "unpack", environment_archive],
            check=True, cwd=str(plugin_dir)
        )

        # If Python virtual env is available
        if is_env_available(plugin_dir):
            # then remove pixi executable and environment tar file
            os.remove(pixi_pack)
            os.remove(environment_archive)

            # and install the enca wheel
            setup_wheel(plugin_dir)

def setup_wheel(plugin_dir: Path = _THIS_DIRECTORY, uninstall_first=False) -> None:
    os.chdir(str(plugin_dir))
    wheel_files = glob.glob(_WHEEL_GLOB)
    if len(wheel_files)>0:
        plugin_wheel = wheel_files[0]
        if os.path.isfile(plugin_wheel):
            if uninstall_first and is_wheel_installed(plugin_dir):
                QgsMessageLog.logMessage('Existing ENCA package is first uninstalled.', tag='Plugins', level=Qgis.Info)
                run_in_enca_env('python -m pip uninstall -y ' + _PACKAGE, plugin_dir = plugin_dir)

            QgsMessageLog.logMessage(f'Installing updated ENCA package from wheel file {plugin_wheel}', tag='Plugins', level=Qgis.Info)
            run_in_enca_env('python -m pip install ' + plugin_wheel, plugin_dir = plugin_dir)
        
            if is_wheel_installed(plugin_dir):
                # wheel was installed, so .whl file can be removed
                QgsMessageLog.logMessage('Installation completed. Cleaning up wheel file.', tag='Plugins', level=Qgis.Info)
                os.remove(plugin_wheel)

def is_wheel_installed(plugin_dir: Path = _THIS_DIRECTORY) -> bool:
    """Check whether enca wheel file was installed in enca env."""
    return os.path.isdir(str(plugin_dir / "env" / "Lib" / "site-packages" / _PACKAGE))

if __name__ == "__main__":
    configure_environment()
