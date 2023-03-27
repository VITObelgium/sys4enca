import ctypes
import operator
import os
import threading
from functools import reduce

import yaml
from qgis.PyQt import QtCore, QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsTask
from qgis.gui import QgsFileWidget
from qgis.utils import iface

import enca.carbon as carbon
import enca.components
import enca.framework
import enca.water as water
from enca.framework.errors import Error
from enca.framework.config_check import ConfigError
from enca.framework.run import Cancelled

from .help import show_help
from .qt_tools import writeWidget, expand_template
from .qgis_tools import load_vector_layer

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'enca_plugin_dockwidget_base.ui'))


# We need to keep a reference to our tasks prevent tasks from "disappearing" when we pass them on to the taskmanager.
_tasks = []  #: global reference to currently launched tasks.

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

#: vector output files to be loaded after a run, with visualization parameters (keyword arguments for
component_vector_layers = {
    carbon.Carbon.component: [(os.path.join('temp', 'CARBON_Indices_SELU_{year}.gpkg'), dict(
        layer_name='NEACS [ha]',
        attribute_name='C10_ha'))],
    water.Water.component: [(os.path.join('temp', 'WATER_Indices_SELU_{year}.gpkg'), dict(
        layer_name='TOTuseEW',
        attribute_name='W9_ha',
        color_ramp='Blues'))]
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

        self.set_up_component_dropdowns()

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

        self.config_template = {
            'years': [self.year],
            'output_dir': self.output_dir,
            'aoi_name': self.aoi_name,
            'statistics_shape': self.data_areas,
            'reporting_shape': self.reporting_areas._filewidget,
            'selected_regions': self.reporting_areas._selected_regions,
            'land_cover': {self.year: self.land_cover},
            'continue': self.continue_run,
            'tier': self.tier
        }

    def set_up_component_dropdowns(self):
        """Fill the dropdown menu for preprocessing/run/ accounts tabs, and connect signals."""
        for i in range(self.run_types.count()):
            tab = self.run_types.widget(i)
            dropdown = findChild(tab, 'component')
            components_stack = findChild(tab, 'components_stack')
            for j in range(components_stack.count()):
                dropdown.addItem(components_stack.widget(j).objectName())
            dropdown.currentIndexChanged.connect(components_stack.setCurrentIndex)

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

            # Handle year selection and reporting regions manually:
            self.year.setValue(config['years'][0])
            self.reporting_areas.setShapefile(config.get('reporting_shape'))

            # remaining config values can be read automatically using the config templates
            main_template = {key: value for key, value in self.config_template.items()
                             if key not in ('years', 'reporting_regions')}
            self.load_template(config, main_template)

            # select the right tab (preprocessing/components/accounts), and the right component within that tab:
            component_name = config['component']
            run_class = enca.components.get_component(component_name)
            run_type = run_class.run_type
            # select tab:
            run_type_tab = findChild(self.run_types, run_type.name)
            self.run_types.setCurrentWidget(run_type_tab)
            # select component:
            component_widget = findChild(run_type_tab, component_name)
            component_combo = findChild(run_type_tab, 'component')
            component_combo.setCurrentText(component_name)

            # handle run name:
            try:
                findChild(component_widget, 'run_name').setText(config['run_name'])
            except ValueError:
                pass  # Not all input pages have a run_name widget yet
            # read other config values using the template
            self.load_template(config, {component_name:
                                            {key: findChild(component_widget, key)
                                             for key in component_input_widgets.get(component_name, {})}})

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
            component_page = self.findChild(QtWidgets.QWidget, component)
            result += [findChild(component_page, key) for key in keys]
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
        template = self.make_template()
        taskname = f'{self.component.currentText()} run {template["run_name"].text()}'
        task = Task(taskname, template, output_vectors=component_vector_layers[self.component.currentText()])
        _tasks.append(task)
        QgsMessageLog.logMessage(f'Submitting task {taskname}', level=Qgis.Info)
        QgsApplication.taskManager().addTask(task)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()

    def make_config(self):
        """Generate a config from current settings."""
        return expand_template(self.make_template())

    def make_template(self):
        # Get the currently selected component.
        # 1. see which tab we are on (preprocessing / components / accounts)
        tab_runtype = self.run_types.currentWidget()
        # 2. select the component form this tab
        component_dropdown = findChild(tab_runtype, 'component')
        component = component_dropdown.currentText()  # TODO fill component drop-down with user data corresponding to internal component name?
        component_widget = findChild(tab_runtype, component)
        template =  {**self.config_template,
                     'component': component_dropdown,
                     component: {key: findChild(component_widget, key)
                                 for key in component_input_widgets.get(component, {})}}
        try:
            template['run_name'] = findChild(component_widget, 'run_name')
        except ValueError:  # Some pages are currently incomplete
            pass
        return template


class Task(QgsTask):

    def __init__(self, description, template, output_rasters=None, output_vectors=None):
        super().__init__(description)
        self.config = expand_template(template)
        self.widget_dict = expand_year(template)
        self.output_rasters = output_rasters or []
        self.output_vectors = output_vectors or []
        self.run = None
        self.run_thread = None
        self.exception = None

    def cancel(self):
        """If the task is canceled, raise a Cancelled exception in the run thread to stop it."""
        super().cancel()
        if self.run_thread is not None:
            # Use the Python C API to raise an exception in another thread:
            ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self.run_thread.ident),
                                                             ctypes.py_object(Cancelled))
            # ref: http://docs.python.org/c-api/init.html#PyThreadState_SetAsyncExc
            if ret == 0:
                QgsMessageLog.logMessage('Failed to cancel run thread.', level=Qgis.Critical)

    def run(self):
        try:
            self.run_thread = threading.current_thread()  # save current thread so we can stop it if task is canceled.
            self.run = enca.components.make_run(self.config)
            self.run.start(progress_callback=self.setProgress)
            return True
        except Exception as e:
            self.exception = e
            return False

    def finished(self, result):
        if result:
            QgsMessageLog.logMessage('Task completed', level=Qgis.Info)
            for raster in self.output_rasters:
                path = os.path.join(self.run.run_dir, raster)
                iface.addRasterLayer(path)

            for filename, kwargs in self.output_vectors:
                path = os.path.join(self.run.run_dir, filename).format(year=self.run.config['years'][0])
                load_vector_layer(path, **kwargs)

        else:
            if self.exception is None:
                QgsMessageLog.logMessage('Task failed for unknown reason')
            elif isinstance(self.exception, Cancelled):
                QtWidgets.QMessageBox.information(iface.mainWindow(), 'Cancelled', 'Run was cancelled by user.')
            elif isinstance(self.exception, ConfigError):
                widget = reduce(operator.getitem, self.exception.path, self.widget_dict)
                if isinstance(widget, QgsFileWidget):  # TODO clean up!
                    widget = widget.lineEdit()
                widget.setStyleSheet('border: 1px solid red')
                QtWidgets.QMessageBox.warning(iface.mainWindow(), 'Configuration error', self.exception.message)
                widget.setStyleSheet('')
            elif isinstance(self.exception, Error):
                QtWidgets.QMessageBox.warning(iface.mainWindow(), 'Error', str(self.exception.message))
            else:
                QtWidgets.QMessageBox.critical(iface.mainWindow(), 'Unexpected error',
                                               f'Something went wrong: "{self.exception}".  Please refer to the '
                                               f'log file at {enca.framework.run.get_logfile()} for more details.')
        _tasks.remove(self)


def expand_year(template):
    """Recursively replace all occurrences of the self.year widget as a dict key by the year value.

    This is needed so we can look up widgets by their config path when we catch a
    ConfigError."""
    if type(template) == dict:  # nested dictionary
        result = {}
        for key, value in template.items():
            result_val = expand_year(value)
            if isinstance(key, QtWidgets.QSpinBox):
                result[key.value()] = result_val
            else:
                result[key] = result_val
        return result
    else:
        return template
