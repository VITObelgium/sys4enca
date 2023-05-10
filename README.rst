========
sys4enca
========

Checkout marvin_qgis_tools submodule
====================================
Make sure the ``qgis_tools`` submodule is checked out as well.  If it is not, run ::

  git submodule init
  git submodule update


Building a plugin zip
=====================

Before you distribute the plugin, make sure the files resources.qrc and pip_install_dialog_base.ui are compiled.  This
is done using ``pyrcc5`` and ``pyuic5`` respectively (in Ubuntu, these are part of the pyqt5-dev-tools, on Windows,
these are included in your QGIS installation).  If you use the plugin builder tool, it will compile when your run the
'zip' or 'deploy' commands. 

To build a plugin zip for distribution, I recommend using the following two steps:

1. Use ``git archive`` to build an archive of the plugin source files.  This will also embed the current git commit hash
   into the plugin metadata.txt file.  Take extra care to include the symlinked files from marvin_qgis_tools ::

     git archive -o enca_plugin.zip HEAD enca_plugin \
         --prefix enca_plugin/marvin_qgis_tools/ --add-file qgis_tools/src/marvin_qgis_tools/osgeo4w.py --prefix ""

   (Replace `HEAD` by another tree or commit to build a plugin zip for that version)

2. Extract the resulting archive and use `pb_tool <https://pypi.org/project/pb-tool>`_ to build an installable plugin
   zip file.  ``pb_tool`` will compile resources, .ui, translation and help files.  Due to a limitation in the official
   ``pb_tool`` version, I recommend using the verison from
   https://github.com/tdanckaert/plugin_build_tool/tree/qgis3_version for now,  which you can install using pip ::

     python -m pip install git+https://github.com/tdanckaert/plugin_build_tool.git@qgis3_version

   From the ``enca_plugin`` directory of the unzipped git archive, run ::

     pb_tool translate
     pb_tool zip

See the scripts ``release_plugin.bat`` and ``release_plugin.sh`` for an example.
