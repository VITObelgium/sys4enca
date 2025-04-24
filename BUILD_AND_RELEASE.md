# SYS4ENCA tool

## Components

SYS4ENCA tool and codebase consists of two components:

* The Python package _enca_ which calculates natural capital accounts according to the ENCA methodology (Weber et al). This package can be integrated into other Python programs, or it can be used directly with a command line interface. It resides in the src/enca folder.
* A QGIS plugin, which provides a graphical interface to configure and run calculations with the Python package. The plugin code resides in the enca_plugin/ folder.

## Python dependencies and virtual environment

For both components, the Python dependencies are managed using [Pixi](https://pixi.sh/latest/).

Pixi is an improved alternative to conda which allows us to manage dependencies in a modern way. 

Install Pixi as instructed on the web site, or manually download the binary from the [github releases page](https://github.com/prefix-dev/pixi/releases) and put it in a location that is on your PATH.

The QGIS plugin code is to run in the QGIS environment and depends on QGIS components, such as the Qt GUI ones.

The Python package for calculating the ENCA accounts is designed to run in its own, virtual environment.

To add and install new dependencies, run `pixi add <conda-forge package>` or add it manually in the _pyproject.toml_ file and activate the environment.
Both the lock and _pyproject.toml_ files must be tracked in git to ensure all developers are working in the same environment. 
Have a look [here](https://pixi.sh/latest/advanced/pyproject_toml/) on the syntax.

## Python package enca

### Activating the virtual environment

To activate the Python virtual environment, run `pixi shell` in the root directory.

Optional arguments include 
* _-e <environment>_, which will choose a different environment with additional dependencies, and 
* _--no-lockfile-update_ which will create the environment without updating any dependencies.

### Building the wheel file

In the root directory run `pixi run build`. This will produce a Python wheel file in your current directory.

### Running tests

Additionally, Pixi can be used for automations like `make`, so you can run the unit tests with `pixi run test`.

### Command line interface

Installing the _enca_ package will also install the _enca_ command line tool defined in enca/__main__.py.

In order to run _enca_, make sured the location of the installed executable is on your _PATH_ environment variable.
You can look up the installation directory of the ``enca`` package as follows ::

`python -m pip show enca`

The _Location:_ key contains the location of the _enca_ package.  The _enca_ executable is typically located
in the _bin_ (Unix) or _Scripts_ (Windows) sibling directory of the package location.  Run `enca -h` for an
overview of the different account modules, or `enca <account_name> -h` for an overview of the command line options for
a specific account.

## QGIS plugin

### Development

The plugin uses its own _pyproject.toml_ to define environments which are managed by _pixi_.
Inside the _enca_plugin_ folder, you can run `pixi shell` to enter a development environment specific to the plugin.

### Building the plugin zip file

You can build the plugin zip by from the _enca_plugin_ folder running `pixi run build-all` (to build a zip for all platforms). `build-windows` and `build-linux` are also available. 

This will call the _enca_plugin/src/make/make_plugin_release.py_ script, which produces zip files for each platform in _enca_plugin/build_.

### About make_plugin_release.py

The script _enca_plugin/src/make/make_plugin_release.py_ is used to create a zip file which can be installed in QGIS.

The strategy for shipping the plugin is to ship _enca_ package together with all its dependencies, including GDAL and a Python interpreter.

Instead of installing these in the QGIS Python environment, everything except the UI components live in their own environment, and QGIS communicates with _enca_ through its CLI. 

At the cost of some additional disk space useage, the reasons for doing this are:
* it guarantees fully reproducible environments. Everything is shipped together like a poor man's docker container. No network requests are necessary to install the plugin and its dependencies.
* the _enca_ development environment is not constrained by the environment provided by QGIS over which we have no control.
* we can tap into the conda ecosystem for installing packages/dependencies using _pixi_.

The script takes care of the following:

* Compile the resources.qrc file using pyrcc5.
* Substitute the current commit id in the plugin metadata.txt.  The commit hash of the current HEAD is used, so run from a clean checkout to have the hash match the contents...
* Zip the contents of the plugin, skipping files that should not be distributed.
* Downloads and packages [pixi-pack](https://github.com/quantco/pixi-pack), a tool for bundling and shipping a pixi environment.
* Creates a python wheel out of _enca_ package and includes it in the zip.
* Packages the entire _prod_ environment defined in enca's _pyproject.toml_ as _environment.tar_ using _pixi-pack_ and includes it in the zip.

**Note** The GUI translation is left out of this release script on purpose, to avoid dependency of the build environment on Qt linguist and pb_tools.

### Plugin interface translation

Previously, the scripts _release_plugin.bat_ and _release_plugin.sh_ automated the code and Zip build process,
including resource compilation and translation, if the plugin builder tool _pb_tool_ and other tools (make, etc) are installed.
For _make_ on Windows, [GNUWin32](https://gnuwin32.sourceforge.net/downlinks/make.php) can be used.

_pb_tool_ can be installed from Thomas' fork through `python -m pip install git+https://github.com/tdanckaert/plugin_build_tool.git@https://github.com/tdanckaert/plugin_build_tool.git@qgis3_version`
and has a configuration (.cfg) file in the _enca_plugin/src_ directory.

While most of the functionality is now done by the make_plugin_release.py script, the pb_tool code (method _translate_) 
shows how to compile the i18n .ts files into .qm files via the lrelease tool (from Qt4). It does not extract the translatable strings (lupdate) from the code files first.

For the translation via Qt Linquist on Windows, you can install the pyside6 software, and execute following commands:

To extract the translatable strings from the given .ui and .py files:
`pyside6-lupdate -tr-function-alias tr+=self.tr -extensions ui,py enca_plugin_dockwidget_base.ui enca_plugin.py enca_plugin_dockwidget.py region_picker.py -ts i18n\fr.ts`

To make this extraction work correctly, it is recommended to
* Use double quotes
* Avoid f-strings and use str.format() syntax so that substitution values are {0}, {1} ...
* Avoid u'' (all Python 3 strings are unicode by default)
* File dialogs are customized by code for the filter patterns and dialog title. Some labels of the dialog, e.g. 'selected file name', are inherited from the OS' locale when static dialog class methods are used.

Then update the .ts file, adding in the translated strings.

To compile the .ts file into the .qm file:
`pyside6-lrelease i18n\fr.ts`

For reference, see [Qt linguist manual](https://doc.qt.io/qt-6/qtlinguist-index.html).

### Running the plugin

When the plugin is first installed or loaded, the functions in _enca_plugin/src/exe_utils.py_ become relevant.

These will:
* Unpack the _environment.tar_ using the shipped _pixi-pack_ binary, to a pixi environment that lives in the _env_ folder, if the .tar file is found in the plugin installation directory.
* Install the wheel into this environment, if the .whl file is found in the plugin directory. This can be used to update the enca package code without having to re-install (un-pack) the whole plugin (i.e. for testing and patching).

The code that interacts with the UI interacts with the _enca_ CLI that is installed in this environment.

You must test installing the resulting zip file and running the plugin before publishing it.

## Release checklist

### Update the ENCA core package and/or plugin code

Commit the code. 

### Update the translation file, compile it and push it into Git.

### Tag the code

Tag the git commit with the new version ('1.0.0' in our example).

Remember to push the tag to other repositories, so tags remain in sync with all users of the code.

### Update the plugin

Create plugin zip files using `pixi run build-all` and publish them.

Check if the plugin's metadata.txt file reflects the proper version and Git commit (build), and update it if needed.

### Update the documentation

To generate HTML documentation in the __build_ directory run `pixi run docs`.

To generate pdf documentation, you will also need a LaTeX installation.  Then, navigate to the _docs_ subdirectory and
run ::

`make latexpdf`

**Note**

This project has been set up using PyScaffold 4.0.1. For details and usage
information see [PyScaffold](https://pyscaffold.org/).
