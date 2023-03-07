from qgis.PyQt.QtGui import QIntValidator
from qgis.PyQt.QtWidgets import QDialog
from .pip_install_dialog_base import Ui_Dialog


class PipInstallDialog(QDialog, Ui_Dialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        port_validator = QIntValidator(0, 65535, self)
        self.proxyPort.setValidator(port_validator)
