import os

import enca.carbon as carbon
import enca.water as water
import yaml
from qgis.PyQt import QtCore, QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import Qgis, QgsMessageLog
from qgis.utils import iface

from .help import show_help
from .qt_tools import writeWidget, expand_template

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'enca_plugin_dockwidget_base.ui'))


#:  names of input widgets per component.
# Note: names must match the name of the corresponding ui widget *and* the config key to which they refer.
component_input_widgets = {
    carbon.Carbon.component: [carbon.FOREST_AGB,
                              carbon.FOREST_BGB,
                              carbon.FOREST_LITTER,
                              carbon.SOIL,
                              carbon.LIVESTOCK,
                              carbon.NPP,
                              carbon.AGRICULTURE_CEREALS,
                              carbon.AGRICULTURE_FIBER,
                              carbon.AGRICULTURE_FRUIT,
                              carbon.AGRICULTURE_OILCROP,
                              carbon.AGRICULTURE_PULSES,
                              carbon.AGRICULTURE_ROOTS,
                              carbon.AGRICULTURE_CAFE,
                              carbon.AGRICULTURE_VEGETABLES,
                              carbon.AGRICULTURE_SUGAR,
                              carbon.WOODREMOVAL,
                              carbon.SOIL_EROSION,
                              carbon.ILUP,
                              carbon.CEH1,
                              carbon.CEH4,
                              carbon.CEH6,
                              carbon.CEH7,
                              carbon.COW,
                              carbon.FIRE,
                              carbon.FIRE_SPLIT,
                              carbon.FIRE_INTEN],
    water.Water.component: [water.USE_AGRI,
                            water.USE_MUNI,
                            water.EVAPO_RAINFED,
                            water.PRECIPITATION,
                            water.EVAPO,
                            water.LT_PRECIPITATION,
                            water.LT_EVAPO,
                            water.DROUGHT_VULN,
                            water.RIVER_LENGTH,
                            water.LT_OUTFLOW,
                            water.AQUIFER,
                            water.SALINITY,
                            water.HYDRO_LAKES,
                            water.GLORIC_ADAPTED],
    'INFRA': ['leac_result'],
    'LEAC': ['base_year']
}


def findChild(widget: QtWidgets.QWidget, name: str):
    """Helper function to deal with the fact that .ui compilation does not allow duplicate widget names.

    When the .ui file gets compiled, widgets with duplicate names get a numerical suffix (e.g. 'run_name'
    -> 'run_name2').  Here, we search using a regular expressions, and return the first and widget.  Raise an error
    if not exactly one matching widget is found.
    """
    try:
        result, = widget.findChildren(QtWidgets.QWidget,
                                      QtCore.QRegularExpression(f'^{QtCore.QRegularExpression.escape(name)}\\d*$'))
    except ValueError:
        QgsMessageLog.logMessage(f'findChildren() did not find a unique result for widget name "{name}"',
                                 level=Qgis.Critical)
        raise
    return result


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

        self.continue_run = QtWidgets.QCheckBox('Continue existing run')
        self.continue_run.setObjectName('continue_run')
        self.toolbar.addWidget(self.continue_run)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)

        self.helpact = QtWidgets.QAction('Help', self)
        self.helpact.triggered.connect(show_help)
        self.toolbar.addAction(self.helpact)

        self.toolbarLayout.addWidget(self.toolbar)

        # Reporting area shapefiles should have 'GID_0' id attribute
        self.reporting_areas.set_id_label('GID_0')

        # Initialize Tier level combobox
        self.tier.addItem('1', 1)
        self.tier.addItem('2', 2)
        self.tier.addItem('3', 3)

        # Set up references to QStackedWidget pages grouping inputs for different components
        self.component_pages = {component: self.findChild(QtWidgets.QWidget, component)
                                for component in {'CARBON', 'WATER', 'LEAC', 'INFRA'}}

        self.config_template = {
            'years': [self.year],
            'component': self.component,
            'output_dir': self.output_dir,
            'aoi_name': self.aoi_name,
            'statistics_shape': self.data_areas,
            'reporting_shape': self.reporting_areas._filewidget,
            'selected_regions': self.reporting_areas._selected_regions,
            'land_cover': {self.year: self.land_cover},
            'continue': self.continue_run,
            'tier': self.tier
        }

    def saveConfig(self):
        """Save current ui state as a yaml config file."""
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Config File", "config.yaml", "*.yaml")
        if not filename:
            return
        with open(filename, 'wt') as f:
            f.write(yaml.dump(self.make_config()))

    def loadConfig(self):
        """Load a yaml config file."""
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load Config File", "",
                                                            "Config files (*.yaml);; All files (*.*)")
        if not filename:
            return

        try:
            with open(filename, 'rt') as f:
                config = yaml.safe_load(f)

            # Clear existing configuration:
            for widget in self.list_config_widgets():
                writeWidget(widget, None)

            # Handle year selection:
            self.year.setValue(config['years'][0])

            # remaining config values can be read automatically using the config templates
            main_template = {key: value for key, value in self.config_template.items()
                             if key not in ('years', )}
            self.load_template(config, main_template)
            self.reporting_areas.updateRegions()

            component_name = config['component']
            # TODO: later we need to select preprocessing/component/account tab first, and select component in that tab.
            self.component.setCurrentText(component_name)
            component_widget = self.component_pages[component_name]
            # handle run name:
            findChild(component_widget, 'run_name').setText(config['run_name'])
            # read other config values using the template
            self.load_template(config, {key: findChild(component_widget, key)
                                        for key in component_input_widgets[component_name]})

        except BaseException as e:
            QtWidgets.QMessageBox.critical(self, 'Error loading config', f'Could not load config {filename}: {e}.')

        if 'metadata' in config:
            self.metadata[config['service']] = config['metadata'][config['years'][0]]

    def list_widgets(self, element):
        result = []
        if isinstance(element, QtWidgets.QWidget):
            result.append(element)
        elif isinstance(element, dict):
            for x in element.values():
                result = result + self.list_widgets(x)
        elif isinstance(element, list):
            for x in element:
                result = result + self.list_widgets(x)
        else:
            pass
        return result

    def list_config_widgets(self):
        result = self.list_widgets(self.config_template)
        for component, keys in component_input_widgets.items():
            component_widget = self.component_pages[component]
            result += [findChild(component_widget, key) for key in keys]
        return result

    def load_template(self, config, template):
        """Update the widgets listed in the template with values from config.

        We use recursion to process te nested dictionary structure of the template and config.  This is more or less
        the inverse of expand_template().

        NOTE: Assumes widget self.year was already set to the correct value."""
        if type(template) == dict:
            for key, template_value in template.items():
                # special case for yearly datasets ...
                if key == self.year:
                    key = self.year.value()
                try:
                    self.load_template(config[key], template_value)
                except KeyError as e:
                    QgsMessageLog.logMessage(f'While loading config: missing config key {e}.', level=Qgis.Warning)
        elif type(template) in (int, bool, str):
            # We're only interested in the widgets referenced from the template.
            pass
        elif isinstance(template, QtCore.QObject):
            writeWidget(template, config)
        else:
            raise RuntimeError(f'Failed to process template {template}.')

    def run(self):
        pass

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()

    def make_config(self):
        """Generate a config from current settings."""
        component = self.component.currentText()
        component_widget = self.component_pages[component]
        return expand_template({**self.config_template,
                                'run_name': findChild(component_widget, 'run_name'),
                                component: {key: findChild(component_widget, key)
                                            for key in component_input_widgets[component]}})


