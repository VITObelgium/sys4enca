# sys4enca

## Check out marvin_qgis_tools submodule

Make sure the `qgis_tools` submodule is checked out as well. If it is
not, run :

    git submodule init
    git submodule update

Make sure the symbolic link to the `marvin_qgis_tool` directory in the
`enca_plugin` directory is working (if the link is not working on
Windows, you can try the following:

-   make sure Developer Mode is turned on in windows so symlinks are
    enabled.

-   make sure git `core.symlinks` setting is `True`for your repository
    (or globally) :

        git config --global core.symlinks True

-   On windows, symlinks only seem to work if using git from windows cmd
    prompt. If using git bash on windows, adding
    `MSYS=winsymlinks:nativestrict` to its bashrc file may help.

-   If you have a broken symlink, apply above settings, delete the
    broken `marvin_qgis_tool` link in `enca_plugin`, and use
    `git reset --hard HEAD` to restore it.

## Making a new release

To release a new plugin, we need to prepare a zip file to install the
plugin with, and upload a new sys4enca package to the artifactory
(unless the sys4enca package is unchanged, and only the QGIS plugin was
updated).

### Update sys4enca

1.  Update the attributes `_min_version` and `_version_next` in
    `enca_plugin/install_deps.p` so the `_min_version` is the new
    version the plugin to use, and `_version_next` is e.g. the next
    minor version.

2.  Commit this version of the code, and tag the git commit with the new
    version. (Remember to push the tag to other repositories, so tags
    remain in sync with all users of the code\...)

3.  Build a new wheel for sys4enca from a clean git checkout of the new
    release tag, e.g. by running :

        python -m pip wheel . --no-deps

    (`.` refers to the current working direectory, if you are not in the
    repository root, change that to the path to the repository root
    directory. This should produce a file
    `sys4enca-<VERSION>-py3-none-any.whl`.

4.  Add the wheel file to the
    [Artifactory](https://artifactory.vgt.vito.be). package repository
    directory `python-packages-public/sys4enca`.

### Update the plugin

Before you distribute the plugin, make sure the files resources.qrc and
pip_install_dialog_base.ui are compiled. This is done using `pyrcc5` and
`pyuic5` respectively. Youcan use the script `release_plugin.bat` or
`release_plugin.sh` to automatically build a plugin zip file for the
current version. Before you can run the scripts, you need to make sure
all required tools are installed on your system. Requirements are:

-   Plugin builder tool (`pb_tool`). You should install the fork from
    <https://github.com/tdanckaert/plugin_build_tool.git>, because the
    official version has a some issues. Install in your python
    environment with :

        python -m pip install git+https://github.com/tdanckaert/plugin_build_tool.git@qgis3_version

-   Sphinx to build documentation (you can install it using pip, or
    using the OSGeo4W installer).

-   Zip or [7zip](https://www.7-zip.org).

-   Make (windows version available from
    [GnuWin32](https://gnuwin32.sourceforge.net/downlinks/make.php).

-   `pyuic5` and `pyrcc5` PyQt command line tools. These are installed
    as part of QGIS, so the easiest solution to get everything working
    on windows may be to work from the OSGeo4W Shell.

If all these tools are installed and available from the command line
(you may need to add Make, git and 7-zip directories) to your `PATH`
environment variable for this), run :

    ./release_plugin.sh <VERSION_TAG>

where `<VERSION_TAG>` is the git tag we created in the previous step. A
file `enca_plugin_<COMMIT_HASH>.zip` should now appear in the current
directory.

## Building a plugin zip

Before you distribute the plugin, make sure the files resources.qrc and
pip_install_dialog_base.ui are compiled. This is done using `pyrcc5` and
`pyuic5` respectively (in Ubuntu, these are part of the pyqt5-dev-tools,
on Windows, these are included in your QGIS installation). If you use
the plugin builder tool, it will compile when your run the \'zip\' or
\'deploy\' commands.

To build a plugin zip for distribution, I recommend using the following
two steps:

1.  Use `git archive` to build an archive of the plugin source files.
    This will also embed the current git commit hash into the plugin
    metadata.txt file. Take extra care to include the symlinked files
    from marvin_qgis_tools :

        git archive -o enca_plugin.zip HEAD enca_plugin \
            --prefix enca_plugin/marvin_qgis_tools/ --add-file qgis_tools/src/marvin_qgis_tools/osgeo4w.py --prefix ""

    (Replace [HEAD]{.title-ref} by another tree or commit to build a
    plugin zip for that version)

2.  Extract the resulting archive and use
    [pb_tool](https://pypi.org/project/pb-tool) to build an installable
    plugin zip file. `pb_tool` will compile resources, .ui, translation
    and help files. Due to a limitation in the official `pb_tool`
    version, I recommend using the version from
    <https://github.com/tdanckaert/plugin_build_tool/tree/qgis3_version>
    for now, which you can install using pip :

        python -m pip install git+https://github.com/tdanckaert/plugin_build_tool.git@qgis3_version

    You will also need a `make` program to run `pb_tool`. On Linux, this
    is readily available, on Windows, you can install `make` from
    [GNUWin32](https://gnuwin32.sourceforge.net/downlinks/make.php) .

    From the `enca_plugin` directory of the unzipped git archive, run :

        pb_tool translate
        pb_tool zip

The scripts `release_plugin.bat` and `release_plugin.sh` automate this
process (if `pb_tool` and `make` are installed).
