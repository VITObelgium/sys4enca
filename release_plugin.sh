#!/bin/bash
# Helper script to build a plugin zip from a specific revision (e.g. HEAD, or 0.2.2).  Run it from the repository root:
#
# ./release_plugin.sh HEAD

if [[ $# -eq 0 ]]
then
    echo 'Please specify a git revision to build a plugin .zip file from.'
    exit 1
fi

GIT_REV=$1
COMMIT_HASH=`git rev-parse --short $GIT_REV`
TEMP_ENCA=`mktemp -d /tmp/enca_pluginXXXXXX`

git archive --format tar --worktree-attributes $GIT_REV enca_plugin \
    --prefix enca_plugin/marvin_qgis_tools/ --add-file qgis_tools/src/marvin_qgis_tools/osgeo4w.py \
    --prefix "" | tar -x -C $TEMP_ENCA
pushd $TEMP_ENCA/enca_plugin
pb_tool translate
pb_tool zip
popd
mv "${TEMP_ENCA}/enca_plugin/zip_build/enca_plugin.zip" "./enca_plugin_${COMMIT_HASH}.zip"
echo Removing $TEMP_ENCA
rm -r $TEMP_ENCA

