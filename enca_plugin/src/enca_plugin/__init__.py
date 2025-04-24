"""
 ENCA Natural Capital Accounting plugin.

 This script initializes the plugin, making it known to QGIS.
"""


class empty_plugin:
    """If checking/installing dependencies fails, load this empty plugin to avoid further errors and stacktraces."""

    def __init__(self):
        pass

    def initGui(self):
        pass

    def unload(self):
        pass


# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load EncaPlugin class from file enca_plugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """

    from .enca_plugin import EncaPlugin

    return EncaPlugin(iface)