import os

from qgis.PyQt import QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal
from qgis.utils import iface

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'enca_plugin_dockwidget_base.ui'))


class ENCAPluginDockWidget(QtWidgets.QDockWidget, FORM_CLASS):

    closingPlugin = pyqtSignal()

    def __init__(self, parent=None):
        """Constructor."""
        super(ENCAPluginDockWidget, self).__init__(parent)
        # Set up the user interface from Designer.
        # After setupUI you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://doc.qt.io/qt-5/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.setupUi(self)

        self.toolbar = QtWidgets.QToolBar()
        self.toolbar.setIconSize(iface.iconSize(dockedToolbar=True))

        self.loadact = QtWidgets.QAction(QtGui.QIcon(":/plugins/enca_plugin/mActionFileOpen.svg"),
                                         'Load Configuration', self)
        self.loadact.triggered.connect(self.loadConfig)
        self.saveact = QtWidgets.QAction(QtGui.QIcon(":/plugins/enca_plugin/mActionFileSaveAs.svg"),
                                         'Save Configuration', self)
        self.saveact.triggered.connect(self.saveConfig)
        self.toolbar.addAction(self.loadact)
        self.toolbar.addAction(self.saveact)
        self.toolbar.addSeparator()

        self.runact = QtWidgets.QAction(
            QtGui.QIcon(":/plugins/enca_plugin/play-button-svgrepo-com.svg"),
            'Run', self)
        self.runact.triggered.connect(self.run)
        self.toolbar.addAction(self.runact)

        self.toolbarLayout.addWidget(self.toolbar)

    def loadConfig(self):
        pass

    def saveConfig(selfe):
        pass

    def run(self):
        pass

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()
