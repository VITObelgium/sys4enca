from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices

# from qgis.utils import showPluginHelp

def show_help():
    # showPluginHelp()
    url = QUrl("https://papbio.vito.be/en/sys4enca-tool")
    QDesktopServices.openUrl(url)
