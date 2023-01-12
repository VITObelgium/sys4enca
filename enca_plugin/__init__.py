# noinspection PyPep8Naming
def classFactory(iface):  # pylint: disable=invalid-name
    """Load ENCAPlugin class from file ENCAPlugin.

    :param iface: A QGIS interface instance.
    :type iface: QgsInterface
    """
    #
    from .enca_plugin import ENCAPlugin
    return ENCAPlugin(iface)
