import glob
import logging
import os

from enum import Enum

import yaml

from .config_check import ConfigError, ConfigCheck, ConfigItem, ConfigRaster, ConfigRasterDir, check_csv, YEARLY
from .errors import Error

logger = logging.getLogger(__name__)
_log_format = logging.Formatter('%(asctime)s %(name)s [%(levelname)s] - %(message)s')
_logfile_handler = None
_logfile = None

logging.captureWarnings(True)
warnlogger = logging.getLogger('py.warnings')

_STATISTICS_SHAPE = 'statistics_shape'
_REPORTING_SHAPE = 'reporting_shape'
_DEFLATOR = 'deflator'


def set_up_console_logging(root_logger, verbose=False):
    """Install a log handler that prints to the terminal."""
    ch = logging.StreamHandler()
    ch.setFormatter(_log_format)
    ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root_logger.addHandler(ch)
    warnlogger.addHandler(ch)


def set_up_logfile(log_dir, root_logger, verbose=False, filename=f'{__name__}.log'):
    """Install a log handler that prints to a file in the provided directory."""
    global _logfile_handler
    global _logfile
    _logfile = os.path.join(log_dir, filename)
    fh = logging.FileHandler(_logfile, encoding='utf-8')
    fh.setLevel(logging.DEBUG if verbose else logging.WARNING)
    fh.setFormatter(_log_format)

    root_logger.addHandler(fh)
    warnlogger.addHandler(fh)
    _logfile_handler = fh


def remove_logfile_handler():
    """Remove the log file handler.

    Required to clean up when we reload the inca plugin.  Otherwise, an extra log handler starts writing to the same
    file every time we reload the plugin."""
    global _logfile_handler
    if _logfile_handler is not None:
        logger.removeHandler(_logfile_handler)


def get_logfile():
    """Return the filename of the current log file."""
    return _logfile


class ShapeType(Enum):
    STATISTICS = 0
    REPORTING = 1


class Cancelled(Exception):
    """Custom Exception to signal cancellation of a Run.

    This Exception not raised by the inca package itself.  Rather, it is "injected" into the thread of a running inca
    calculation by the QGIS plugin when the user clicks the cancel button."""
    pass


class Run:
    """Common skeleton for all runs, takes care of logging, output directories, config validation, ...

    :param config: dictionary containing all configuration for the current run.
    """

    def __init__(self, config):
        logger.debug('Run.__init__')
        self.config_template = {
        }  #: Dictionary of :obj:`inca.common.config_check.ConfigItem` describing the required configuration for this run.

        # If running from command line, config contains a 'func' attribute used to select the run type (side effect of
        # our use of argparse subparsers).  This attribute must not be written to the final config file.
        config.pop('func', None)
        self.years = config.get('years')
        # Check self.years is a non-empty list of integers
        if not self.years or not isinstance(self.years, list) or any(not isinstance(y, int) for y in self.years):
            raise ConfigError('Please provide a list of years for which to calculate accounts.', ['years'])
        self.config = config
        if not self.config.get('run_name'):
            raise ConfigError('Please provide a run name.', ['run_name'])
        self.run_name = self.config['run_name']
        if not self.config.get('output_dir'):
            raise ConfigError('Please provide an output directory.', ['output_dir'])
        self.output_dir = self.config['output_dir']
        self.run_dir = os.path.join(self.output_dir, self.run_name)

        self._progress = 0.
        self._progress_callback = None  #: Callback function to report progress to QGIS.
        self._progress_weight_run = 0.85  #: default contribution of run itself to progress bar, remaining part is for raster check.
        self.root_logger = logger  #: root logger for Run log file.  Can be overridden in subclass

    def start(self, progress_callback=None):
        """Call this method to start the actual calculation.

        Wraps :meth:`inca.common.config_check.ConfigCheck.validate` and :meth:`inca.Run._start` with exception handlers."""
        self._progress_callback = progress_callback
        assert(0. <= self._progress_weight_run <= 1.0)
        self.add_progress(0.)

        self._create_dirs()
        global _logfile_handler
        if _logfile_handler is None:
            set_up_logfile(self.run_dir, self.root_logger, self.config.get('verbose', False))
        logger.info(self.version_info())

        # dump config as provided by user, so we can see exactly what the input was when we want to debug
        self._dump_config()

        try:
            config_check = ConfigCheck(self.config_template, self.config)
            config_check.validate()
            self.adjust_rasters(config_check)
            self._progress = 100. * (1. - self._progress_weight_run)
            self._start()
        except Cancelled:
            logger.exception('Run canceled.')
            raise
        except BaseException:
            logger.exception('Error:')
            raise
        logger.info('Run complete.')

    def _start(self):
        """This method should be implemented in each subclass.  It is the starting point of the actual calculation."""
        raise NotImplementedError

    def version_info(self):
        """This method should be implemented in a subclass.  It can be used to print a string describing package
         versions."""
        raise NotImplementedError

    def temp_dir(self):
        return os.path.join(self.run_dir(), 'temp')

    def _create_dirs(self):
        """Create output directory for this run, or check if it already exists."""
        try:
            os.makedirs(self.output_dir, exist_ok=True)  # top output directory
        except OSError as e:
            raise RuntimeError(f'Failed to create output directory "{self.config["output_dir"]}": {e}')

        # We want a single subdir "run_name", 1 level below the output dir.  Therefore, "run_name" should not contain
        # directory separators.
        if os.sep in self.run_name or (os.altsep is not None and os.altsep in self.run_name):
            raise Error(f'Run name "{self.run_name}" contains directory separator.')

        logger.debug('Create run_dir %s', self.run_dir)
        try:
            os.makedirs(self.run_dir)
        except FileExistsError:
            if self.config.get('continue'):
                logger.debug('Continuing work in existing run directory %s', self.run_dir)
            else:
                raise Error(f'Run directory {self.run_dir} already exists.  '
                            'Use option "continue" to resume a previous run.')

    def _dump_config(self):
        """Write a YAML dump of the current config in our run directory."""
        with open(os.path.join(self.run_dir, 'config.yaml'), 'w') as f:
            f.write(yaml.dump(self.config))

    def adjust_rasters(self, config_check):
        """If needed, warp or clip input raster data so it matches the current calculation's extent and projection.

        This function can only be called after calling :meth:`validate`."""
        if not config_check._validated:  # Must validate and check all configitems before we can run this method
            raise RuntimeError('adjust_rasters() called on ConfigCheck before validation.  This is an error.')
        config_rasters = config_check.get_configitems(ConfigRaster)
        config_rasterdirs = config_check.get_configitems(ConfigRasterDir)
        rasterlists = {rasterdir: glob.glob(os.path.join(rasterdir.value, '*.tif'))
                       for rasterdir in config_rasterdirs if rasterdir.value}
        num_rasters = len(config_rasters) + sum(len(l) for l in rasterlists.values())
        if num_rasters == 0:
            return

        progress_per_raster = 100. / num_rasters  # for progress bar
        for raster in config_rasters:
            if raster.value is not None:
                # Make a safe output filename for each different raster in the configuration
                output_filename = '_'.join(str(x) for x in raster._path)
                output_filename = ''.join(char if char.isalnum() else '_' for char in output_filename)
                output_path = os.path.join(self.temp_dir, output_filename +
                                           '_ENCA_{}m_EPSG{}.tif'.format(
                                               int(self.accord.ref_profile['transform'].a),
                                               self.accord.ref_profile['crs'].to_epsg()))
                warped_raster = self.accord.AutomaticBring2AOI(raster.value, path_out=output_path,
                                                               raster_type=raster.type, secure_run=True)
                self._add_progress_prerun(progress_per_raster)
                # Update config entry: recurse into config until we find the last item
                entry = self.config
                for key in raster._path:
                    item = entry[key]
                    if not isinstance(item, dict):
                        entry[key] = warped_raster
                        break
                    else:
                        entry = item

        for rasterdir, rasters in rasterlists.items():
            output_dirname = '_'.join(str(x) for x in rasterdir._path)
            output_dirname = ''.join(char if char.isalnum() else '_' for char in output_dirname)
            tmpdir = os.path.join(self.temp_dir, output_dirname)
            os.makedirs(tmpdir, exist_ok=True)
            warped_rasters = []
            for file in rasters:
                output_filename = os.path.join(tmpdir, os.path.basename(file))
                warped_rasters.append(self.accord.AutomaticBring2AOI(file, path_out=output_filename,
                                                                     raster_type=rasterdir.type, secure_run=True))
                self._add_progress_prerun(progress_per_raster)

            entry = self.config
            for key in rasterdir._path:
                item = entry[key]
                if not isinstance(item, dict):
                    entry[key] = sorted(warped_rasters)
                    break
                else:
                    entry = item

    def add_progress(self, p):
        if self._progress_callback is not None:
            self._progress += p
            self._progress_callback(p)
