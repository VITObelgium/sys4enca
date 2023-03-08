========
sys4enca
========

Building a plugin zip
====================

To build a plugin zip for distribution, I recommend using the following two steps:

1. Use ``git archive`` to build an archive of the plugin source files.  This will also embed the current git commit hash
   into the plugin metadata.txt file. ::

     git archive -o enca_plugin.zip HEAD enca_plugin

   (Replace `HEAD` by another tree or commit to build a plugin zip for that version)

2. Extract the resulting archive and use `pb_tool <https://pypi.org/project/pb-tool>`_ to build an installable plugin
   zip file.  ``pb_tool`` will compile resources, .ui, translation and help files.  Due to a limitation in the official
   ``pb_tool`` version, I recommend using the verison from
   https://github.com/tdanckaert/plugin_build_tool/tree/qgis3_version for now.  From the ``enca_plugin`` directory of
   the unzipped git archive, run ::

     pb_tool zip
