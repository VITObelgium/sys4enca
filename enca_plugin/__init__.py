# Check all required dependencies are met
from .install_deps import check_dependencies

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
    """Load ENCAPlugin class from file ENCAPlugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    # Only try to load real plugin if all required dependencies are there:
    if not check_dependencies():
        return empty_plugin()

    from .enca_plugin import ENCAPlugin
    return ENCAPlugin(iface)
