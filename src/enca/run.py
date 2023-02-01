import glob
import logging
import math
import os
from enum import Enum

import geopandas as gpd
import rasterio
import yaml

from .config_check import ConfigError, ConfigCheck, ConfigRaster, ConfigRasterDir
from .errors import Error
from .geoprocessing import SHAPE_ID, MINIMUM_RESOLUTION, POLY_MIN_SIZE, GeoProcessing

logger = logging.getLogger(__name__)
_log_format = logging.Formatter('%(asctime)s %(name)s [%(levelname)s] - %(message)s')
_logfile_handler = None
_logfile = None

logging.captureWarnings(True)
warnlogger = logging.getLogger('py.warnings')

_LAND_COVER = 'land_cover'
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

    Required to clean up when we reload the plugin.  Otherwise, an extra log handler starts writing to the same
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

    This Exception not raised by the package itself.  Rather, it is "injected" into the thread of a running calculation
    by the QGIS plugin when the user clicks the cancel button."""
    pass


class Run:
    """Common skeleton for all runs, takes care of logging, output directories, config validation, ...

    :param config: dictionary containing all configuration for the current run.
    """

    id_col_statistics = None  #: Column name to use as index in input statistics region file.
    id_col_reporting = None  #: Column name to use as index in reporting region file.

    def __init__(self, config):
        logger.debug('Run.__init__')
        self.config_template = {
        }  #: Dictionary of :obj:`enca.config_check.ConfigItem` describing the required configuration for this run.

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

        Wraps :meth:`enca.config_check.ConfigCheck.validate` and :meth:`enca.Run._start` with exception handlers."""
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
            self._load_region_shapes()
            self._StudyScopeCheck()
            config_check = ConfigCheck(self.config_template, self.config, self.accord)
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
        return os.path.join(self.run_dir, 'temp')

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

        os.makedirs(self.temp_dir(), exist_ok=True)

    def _dump_config(self):
        """Write a YAML dump of the current config in our run directory."""
        with open(os.path.join(self.run_dir, 'config.yaml'), 'w') as f:
            f.write(yaml.dump(self.config))

    def _load_region_shapes(self):
        """Load the shapes used for statistics (input) and shapes for reporting (output) into a
        :class:`geopandas.GeoDataFrame`.

        The resulting :class:`geopandas.GeoDataFrame` is indexed by NUTS_ID, and has an additional column SHAPE_ID,
        which contains an integer identifier for each shape.  This column can be used when rasterizing shapefiles.
        """
        assert self.id_col_reporting is not None
        assert self.id_col_reporting is not None
        file_statistics = self.config.get(_STATISTICS_SHAPE)
        if not file_statistics:
            raise ConfigError('Please provide vector file describing the input statistics geographical regions.',
                              [_STATISTICS_SHAPE])
        try:
            self.statistics_shape = gpd.read_file(file_statistics).set_index(self.id_col_statistics).sort_index()
        except KeyError:
            raise ConfigError(f'The provided file "{file_statistics}" for input statistics geographical regions does '
                              f'not have a column {self.id_col_statistics}.', [_STATISTICS_SHAPE])
        except Exception as e:
            raise Error(f'Failed to read input stastistics geographical regions file "{file_statistics}": {e}.')
        #: Column name to use as index in reporting region file.
        file_reporting = self.config.get(_REPORTING_SHAPE)
        if not file_reporting:
            raise ConfigError('Please provide a vector file describing the geographical regions for reporting.',
                              [_REPORTING_SHAPE])
        try:
            self.reporting_shape = gpd.read_file(file_reporting).set_index(self.id_col_reporting).sort_index()
        except KeyError:
            raise ConfigError(f'The provided file "{file_reporting}" for reporting geographical regions does not have '
                              f'a column {self.id_col_reporting}.', [_REPORTING_SHAPE])
        except Exception as e:
            raise Error(f'Failed to read reporting geographical regions file "{file_reporting}": {e}.')

        # SHAPE_ID: integer number to be used as identifier when rasterizing .
        self.statistics_shape[SHAPE_ID] = range(1, 1 + self.statistics_shape.shape[0])
        self.reporting_shape[SHAPE_ID] = range(1, 1 + self.reporting_shape.shape[0])

    def _StudyScopeCheck(self):
        """Set up statistical and reporting region vector files, project extent, resolution, raster metadata.

         - Establish the list of statistics regions needed to cover the chosen reporting regions.

         - Initialize the metadata object to handle raster tags and standard output profiles for rasterio.  The
           landcover map provided for the first reference year is used as a master.

         - Generate raster masks of the reporting_regions and statistics_regions, which can be used for
           block-processing.
        """
        ### 1. check if reporting and statistic vectors are in corrct EPSG
        logger.debug('* check if provided vector files have correct EPSG')
        # reporting vector file
        try:
            check_epsg = self.reporting_shape.crs.to_epsg()
        except BaseException:
            raise ConfigError('Please provide a reporting shapefile with a valid EPSG projection.',
                              [_REPORTING_SHAPE])
        if check_epsg != self.epsg:
            self.reporting_shape.to_crs(epsg=self.epsg, inplace=True)
            logger.debug('** reporting vector file had to be warped')

        # statistics vector file
        try:
            check_epsg = self.statistics_shape.crs.to_epsg()
        except BaseException:
            raise ConfigError('Please provide a statistics shapefile with a valid EPSG projection.',
                              [_STATISTICS_SHAPE])
        if check_epsg != self.epsg:
            self.statistics_shape.to_crs(epsg=self.epsg, inplace=True)
            logger.debug('** statistics vector file had to be warped')

        ### 2. now we generate the "statistics_regions" out of the "reporting_regions"
        logger.debug('* get the reporting regions')
        # first we check the given "selected_regions" from  config all exist in reporting_shape
        # mainly needed when tool is run in command line mode
        logger.debug('** check if even all selected_regions (reporting_regions) exist in the reporting vector file')
        selected_regions = self.config.get('selected_regions')
        if not isinstance(selected_regions, list) or not len(selected_regions):
            raise ConfigError('Please select one or more reporting regions.', ['selected_regions'])
        lMismatch = [x for x in selected_regions if x not in self.reporting_shape.index]
        if len(lMismatch) != 0:
            raise ConfigError('The following regions are missing from the provided reporting vector file: ' +
                              ', '.join(lMismatch), [_REPORTING_SHAPE])
        self.reporting_shape = self.reporting_shape.reindex(selected_regions).sort_index()

        # second, we clip the statistics vector file by the reporting one to get a list of all names from the statistic
        # regions intersecting the reporting ones --> faster then a gpd.overlay()
        logger.debug('** clip the statistical vector file by selected reporting regions')
        df_check = gpd.clip(self.statistics_shape, self.reporting_shape)
        # we have to remove false areas (boundary of a statistical region is identical to boundary of reporting one)
        df_check = df_check[~df_check.is_empty]
        df_check = df_check[df_check.area > POLY_MIN_SIZE]
        if df_check.empty:
            raise ConfigError(
                'No areas in the statistics regions file overlap with the selected reporting regions.',
                [_STATISTICS_SHAPE])
        # check if all reporting polygons are completely covered by a statistical ones (minimum overlap)
        area_delta = self.reporting_shape.area.sum() - df_check.area.sum()
        if abs(area_delta) > POLY_MIN_SIZE:
            raise ConfigError(
                'The statistics regions file does not completely cover all selected reporting regions.',
                [_STATISTICS_SHAPE])
        # extract the identifier to get the reporting_regions
        self.statistics_shape = self.statistics_shape.reindex(df_check.index.unique()).sort_index()

        ### 3. now we generate the bounds for the statistical AOI
        logger.debug('* calculate the raster statistical AOI')
        # first get total bounds for selected regions in statistical vector file
        # format: minx, miny, maxx, maxy
        bbox = self.statistics_shape.total_bounds
        # allign to min_resolution increment to support better merges
        bbox = bbox / MINIMUM_RESOLUTION
        # named tuple - BoundingBox(left, bottom, right, top)
        AOI_bbox = rasterio.coords.BoundingBox(left=math.floor(bbox[0]) * MINIMUM_RESOLUTION,
                                               bottom=math.floor(bbox[1]) * MINIMUM_RESOLUTION,
                                               right=math.ceil(bbox[2]) * MINIMUM_RESOLUTION,
                                               top=math.ceil(bbox[3]) * MINIMUM_RESOLUTION)

        ### 4. now we set up the accord object with the needed extent
        logger.debug('* initialize the global raster AccoRD object')
        # Note: since we do not give a reference raster file to GeoProcessing object we have to fill some info manual
        self.accord = GeoProcessing("ENCA Tool", self.component, self.temp_dir())
        # set the extent for the raster files using the statistical domain
        self.accord.ref_extent = AOI_bbox

        ### 5. check if input land cover map MASTER to get resolution for set up of metadata object
        logger.debug(
            '** pre-check the provided MASTER land cover raster file if all statistical regions are covered')
        land_cover_year0 = self.config.get(_LAND_COVER, {}).get(self.years[0])
        if not land_cover_year0:
            raise ConfigError(f'Please provide a land cover file for year {self.years[0]}.',
                              [_LAND_COVER, self.years[0]])
        try:
            aoi_ok = self.accord.vector_in_raster_extent_check(land_cover_year0, self.statistics_shape,
                                                               check_projected=True, check_unit=True,
                                                               stand_alone=True)
        except Error as e:
            # Catch ENCA.Error exceptions and re-raise as ConfigError linked to the config['landcover'][self.years[0]]:
            raise ConfigError(e.message, [_LAND_COVER, self.years[0]])
        if not aoi_ok:
            raise ConfigError(
                'Not all needed statistical_regions specified by the reporting_regions are included in the ' +
                'provided land cover map ({}). '.format(land_cover_year0) +
                'Please provide a land cover map with a minimum extent of {} in EPSG:{}.'.format(AOI_bbox,
                                                                                                 self.epsg),
                [_LAND_COVER, self.years[0]])

        ### 6. we still have to fill the reference profile in the AccoRD object for GeoProcessing
        logger.debug('** give statistic raster info as reference file to the AccoRD object (profile, extent)')
        # (we use information of the master land cover map for that)
        # we need a test to check if we fullfill the ENCA minimum resolution
        with rasterio.open(land_cover_year0) as src:
            src_profile = src.profile
            src_res = src.res
        if src_res[0] > MINIMUM_RESOLUTION:
            logger.warning(
                f'The provided landcover map has a resolution of {src_res[0]}m, which is coarser than the '
                f'proposed MINIMUM RESOLUTION of {MINIMUM_RESOLUTION}m.  The land cover map will be '
                'resampled. Please consider using a higher resolution land cover map if the resampling '
                'produces non-desired results.')
            src_res = (MINIMUM_RESOLUTION, MINIMUM_RESOLUTION)

        # we set up the standard raster profile
        self.accord.ref_profile = {'driver': 'GTiff',
                                   'dtype': src_profile['dtype'],
                                   'nodata': src_profile['nodata'],
                                   'width': int((AOI_bbox.right - AOI_bbox.left) / src_res[0]),
                                   'height': int((AOI_bbox.top - AOI_bbox.bottom) / src_res[1]),
                                   'count': 1,
                                   'crs': rasterio.crs.CRS.from_epsg(self.epsg),
                                   'transform': rasterio.transform.from_origin(AOI_bbox.left, AOI_bbox.top,
                                                                               src_res[0], src_res[1]),
                                   'blockxsize': 256,
                                   'blockysize': 256,
                                   'tiled': True,
                                   'compress': 'deflate',
                                   'interleave': 'band',
                                   'bigtiff': 'if_saver'}
        # create rasterio profile for the reporting area also for further processing
        logger.debug('** set up the raster profile and extent for the reporting regions')
        self._create_reporting_profile()

        ### 7. also generate a raster version of stats and reporting vector file for future blockprocessing tasks
        logger.debug('* create raster versions of statistic and reporting vectors for block processing tasks')
        # first, reporting vector file
        # output file name
        self.reporting_raster = os.path.join(self.temp_dir(), 'reporting_shape_rasterized.tif')
        # run rasterization
        self.accord.rasterize(self.reporting_shape, SHAPE_ID, self.reporting_raster,
                              guess_dtype=True, mode='statistical')

        # second, statistical vector file
        # output file name
        self.statistics_raster = os.path.join(self.temp_dir(), 'statistics_shape_rasterized.tif')
        # run rasterization
        self.accord.rasterize(self.statistics_shape, SHAPE_ID, self.statistics_raster,
                              guess_dtype=True, mode='statistical')

    def _create_reporting_profile(self):
        """ function to generate the rasterio profile for the reporting regions out of the
            rasterio profile of the statistical regions

        :return:
        """
        # first get total bounds for selected regions in reporting vector file
        # format: minx, miny, maxx, maxy
        bbox = self.reporting_shape.total_bounds
        # allign to min_resolution increment to support better merges
        bbox = bbox / MINIMUM_RESOLUTION
        # named tuple - BoundingBox(left, bottom, right, top)
        AOI_bbox = rasterio.coords.BoundingBox(left=math.floor(bbox[0]) * MINIMUM_RESOLUTION,
                                               bottom=math.floor(bbox[1]) * MINIMUM_RESOLUTION,
                                               right=math.ceil(bbox[2]) * MINIMUM_RESOLUTION,
                                               top=math.ceil(bbox[3]) * MINIMUM_RESOLUTION)
        self.accord.reporting_extent = AOI_bbox
        # adapt the rasterio profile of statistical_regions
        self.accord.reporting_profile = self.accord.ref_profile.copy()
        self.accord.reporting_profile.update(width=int((AOI_bbox.right - AOI_bbox.left) /
                                                       self.accord.ref_profile['transform'].a),
                                             height=int((AOI_bbox.top - AOI_bbox.bottom) /
                                                        abs(self.accord.ref_profile['transform'].e)),
                                             transform=rasterio.transform.from_origin(AOI_bbox.left,
                                                                                      AOI_bbox.top,
                                                                                      self.accord.
                                                                                      ref_profile['transform'].a,
                                                                                      abs(self.accord.
                                                                                          ref_profile['transform'].e)))

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
                output_path = os.path.join(self.temp_dir(), output_filename +
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
            tmpdir = os.path.join(self.temp_dir(), output_dirname)
            os.makedirs(tmpdir, exist_ok=True)
            warped_rasters = []
            for file in rasters:
                output_filename = os.path.join(tmpdir, os.path.basename(file))
                warped_rasters.append(self.accord.AutomaticBring2AOI(file, path_out=output_filename,
                                                                     raster_type=rasterdir.type, secure_run=True))
                self._add_progress_prerun(progress_per_raster)

            try:  # If input rasters already have right dimension, tmpdir will be empty -> attempt cleanup.
                os.rmdir(tmpdir)  # Delete tmpdir if it's empty.
            except OSError:  # Directory was not empty.
                pass

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

    def _add_progress_prerun(self, p):
        """Update progress bar outside of the main run phase.

        This method should be used to update the progress bar for calculations in during initialization and raster
        checks, outside of the specific ecosystem service run itself."""
        self._progress += p * (1 - self._progress_weight_run)
        if self._progress_callback:
            self._progress_callback(self._progress)
