"""Base implementation of a run."""

import glob
import logging
import math
import os

import geopandas as gpd
import rasterio
import yaml

import traceback

from .config_check import ConfigCheck, ConfigRaster, ConfigRasterDir
from .errors import Error, ConfigError
from .geoprocessing import SHAPE_ID, MINIMUM_RESOLUTION, POLY_MIN_SIZE, GeoProcessing
from .log_helper import set_up_log_file_for_logger, set_up_console_logging_for_logger, remove_log_file_handlers_from_logger
from .cancelled import Cancelled

# activate the py.warnings Logger that captures Python warnings
logging.captureWarnings(True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_LAND_COVER = 'land_cover'
_STATISTICS_SHAPE = 'statistics_shape'
_REPORTING_SHAPE = 'reporting_shape'
_DEFLATOR = 'deflator'

class Run:
    """Common skeleton for all runs, takes care of logging, output directories, config validation, ...

    :param config: dictionary containing all configuration for the current run.
    """

    id_col_statistics = None  #: Column name to use as index in input statistics region file.
    id_col_reporting = None  #: Column name to use as index in reporting region file.
    component = None  #: Name of module / component, to be set in subclasses.
    software_name = 'NCA Framework' # Name of software tool, to be overridden in subclasses

    def __init__(self, config):
        """Initialize a run from a config dict.

        :param config: Dictionary describing run settings and input.

        """
        self.config_template = {
        }  #: Dictionary of :obj:`.config_check.ConfigItem` describing the required configuration for this run.

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
        self._progress_weight_run = 0.85  #: Proportion of the progress bar used for the run itself.
        self.root_logger = logger  #: root logger for Run log file.  Can be overridden in subclass

    def start(self, progress_callback=None):
        """Call this method to start the actual calculation.

        Wraps :meth:`.config_check.ConfigCheck.validate` and :meth:`.run.Run._start` with exception handlers.

        :param progress_callback: progressbar for the QGIS plug-in
        """
        self._progress_callback = progress_callback
        assert (0. <= self._progress_weight_run <= 1.0)
        self.add_progress(0.)

        self._create_dirs()

        # log to console (for runs via CLI) and to a log file in directory run_dir
        set_up_log_file_for_logger(None,log_dir = self.run_dir, verbose = self.config['verbose'], filename = self.config['run_name']+'.log')
        set_up_log_file_for_logger("py.warnings",log_dir = self.run_dir, verbose = self.config['verbose'], filename = self.config['run_name']+'.log')
        set_up_console_logging_for_logger(None, verbose = False)
        set_up_console_logging_for_logger("py.warnings", verbose = False)
        if 'verbose' in self.config and self.config['verbose']:
            logger.setLevel(logging.DEBUG)

        logger.info(f'*** Welcome to the SYS4ENCA-tool ***')

        # dump config as provided by user, so we can see exactly what the input was when we want to debug
        self._dump_config()

        logger.info(self.version_info())

        if 'started_from' in self.config:
            logger.info('Started from ' + self.config['started_from'])

        try:
            self._configure()
            self._progress = 100. * (1. - self._progress_weight_run)
            logger.info('Starting account calculations')
            self._start()
        except Cancelled as e:
            logger.error(f'{self.software_name} run cancelled.')
            raise e
        except ConfigError as e:
            logger.error(f'Configuration error: {e.message}')
            logger.error(f'Please check following section:')
            logger.error('   ' + ': '.join(str(x) for x in e.path))
            raise e
        except Error as e:
            logger.error('Processing error: %s', e.message)
            raise e
        except BaseException as e:
            logger.error(f'{self.software_name} raised exception {type(e).__name__}: {e}')
            logger.debug(traceback.format_exc(chain=False))
            raise Error(f'{self.software_name} raised exception {type(e).__name__}: {e}')
        finally:
            logger.info(f'{self.software_name} run complete.')
            remove_log_file_handlers_from_logger(__name__)
            remove_log_file_handlers_from_logger("py.warnings")
        logger.info('Run complete.')

    def _configure(self):
        """Set up reference CRS and AOI, check configuration, and adjust input rasters."""
        logger.info('Examining the resolution of first land cover raster')
        # Get the resolution of the first land cover raster.
        try:
            land_cover_year0 = self.config[_LAND_COVER][self.years[0]]
        except KeyError:
            raise ConfigError(f'Please provide a land cover file for year {self.years[0]}.',
                              [_LAND_COVER, self.years[0]])
        try:
            with rasterio.open(land_cover_year0) as src:
                self.src_profile = src.profile
                self.src_res = src.res
                self.epsg = src.crs.to_epsg()
                if self.epsg == 4326:
                    logger.exception(f'Land cover {land_cover_year0} is in EPSG:4326 is not supported, please reproject')
                    raise ConfigError(f'Land cover {land_cover_year0} is in EPSG:4326 is not supported, please reproject', [_LAND_COVER, self.years[0]])
        except Exception as e:
            raise ConfigError(f'Failed to open land cover file for year {self.years[0]}: "{e}"',
                              [_LAND_COVER, self.years[0]])

        if self.src_res[0] > MINIMUM_RESOLUTION:
            logger.warning(
                f'The provided landcover map has a resolution of {self.src_res[0]}m, which is coarser than the '
                f'proposed MINIMUM RESOLUTION of {MINIMUM_RESOLUTION}m.  The land cover map will be '
                'resampled. Please consider using a higher resolution land cover map if the resampling '
                'produces non-desired results.')
            self.src_res = (MINIMUM_RESOLUTION, MINIMUM_RESOLUTION)

        logger.info('Loading region shapes')
        self._load_region_shapes()
        logger.info('Validating study scope')
        self._StudyScopeCheck()
        logger.info('Rasterizing shapes')
        self._rasterize_shapes()
        logger.info('Validating config items')
        config_check = ConfigCheck(self.config_template, self.config, self.accord)
        config_check.validate()
        logger.info('Adjusting input rasters')
        self.adjust_rasters(config_check)

    def _start(self):
        """Start the actual calculation.

        This method should be implemented in each subclass.
        """
        raise NotImplementedError

    def version_info(self):
        """Return a string describing package and dependency versions.

        This method should be implemented in each subclass.
        """
        raise NotImplementedError

    def temp_dir(self):
        """Return the temporary directory for this run (subdirectory of `self.run_dir`)."""
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
                logger.debug('Continuing work in existing run directory %s' % self.run_dir)
            else:
                raise ConfigError(f'Run directory {self.run_dir} already exists.  Use option "continue" to resume a previous run.', ['run_dir'])

        os.makedirs(self.temp_dir(), exist_ok=True)

    def _dump_config(self, config_filename='config.yaml'):
        """Write a YAML dump of the current config in our run directory."""
        logger.debug('Dump config at %s', self.run_dir)
        with open(os.path.join(self.run_dir, config_filename), 'w') as f:
            f.write(yaml.dump(self.config))

    def _load_region_shapes(self):
        """Load shapes for statistics (input) and shapes for reporting (output) into a :class:`geopandas.GeoDataFrame`.

        The resulting :class:`geopandas.GeoDataFrame` is indexed by NUTS_ID, and has an additional column SHAPE_ID,
        which contains an integer identifier for each shape.  This column can be used when rasterizing shapefiles.

        Statistics regions are reduced to the set of regions needed to cover the chosen reporting regions.
        """
        assert self.id_col_statistics is not None
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
            raise ConfigError(f'Failed to read input statistics geographical regions file "{file_statistics}": {e}.', [_STATISTICS_SHAPE])

        #bug fix it seems sometimes the statistics file 'fid' column is not correctly read : as float iso int. Since this is a protected columns this gives rise to errors in write out of gpkg's
        if 'fid' in self.statistics_shape.columns:
            self.statistics_shape['fid'] = self.statistics_shape['fid'].astype(int)

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
            raise ConfigError(f'Failed to read reporting geographical regions file "{file_reporting}": {e}.', [_REPORTING_SHAPE])

        # Reduce reporting regions GeoDataFrame to the set of selected regions:
        logger.debug('* get the reporting regions')
        # first we check the given "selected_regions" from  config all exist in reporting_shape
        # mainly needed when tool is run in command line mode
        logger.debug('** check if all selected_regions (reporting_regions) exist in the reporting vector file')
        selected_regions = self.config.get('selected_regions')
        if not isinstance(selected_regions, list) or not len(selected_regions):
            raise ConfigError('Please select one or more reporting regions.', ['selected_regions'])
        lMismatch = [x for x in selected_regions if x not in self.reporting_shape.index]
        if lMismatch:
            raise ConfigError('The following regions are missing from the provided reporting vector file: ' +
                              ', '.join(lMismatch), [_REPORTING_SHAPE])
        self.reporting_shape = self.reporting_shape.reindex(selected_regions).sort_index()

        logger.debug('Check if statistics and reporting vector files have correct EPSG')
        # reporting vector file
        try:
            check_epsg = self.reporting_shape.crs.to_epsg()
        except Exception:
            raise ConfigError('Please provide a reporting shapefile with a valid EPSG projection.',
                              [_REPORTING_SHAPE])
        if check_epsg != self.epsg:
            self.reporting_shape.to_crs(epsg=self.epsg, inplace=True)
            logger.debug('** reporting vector file had to be warped')
        # statistics vector file
        try:
            check_epsg = self.statistics_shape.crs.to_epsg()
        except Exception:
            raise ConfigError('Please provide a statistics shapefile with a valid EPSG projection.',
                              [_STATISTICS_SHAPE])
        if check_epsg != self.epsg:
            self.statistics_shape.to_crs(epsg=self.epsg, inplace=True)
            logger.debug('** statistics vector file had to be warped')

        # SHAPE_ID: integer number to be used as identifier when rasterizing .
        self.statistics_shape[SHAPE_ID] = range(1, 1 + self.statistics_shape.shape[0])
        self.reporting_shape[SHAPE_ID] = range(1, 1 + self.reporting_shape.shape[0])

        # Get the "statistics_regions" needed to cover the selected "reporting_regions":
        #
        # We clip the statistics vector file by the reporting one to get a list of all names from the statistic regions
        # intersecting the reporting ones --> faster then a gpd.overlay()
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
        # assume that any area difference less then a third of a pixel will disappear after rasterization
        if abs(area_delta) > (self.src_res[0] * self.src_res[1] / 3.):
            raise ConfigError(
                'The statistics regions file does not completely cover all selected reporting regions.',
                [_STATISTICS_SHAPE])
        # extract the identifier to get the reporting_regions
        self.statistics_shape = self.statistics_shape.reindex(df_check.index.unique()).sort_index()

    def _StudyScopeCheck(self):
        """Set up project extent, resolution, raster metadata.

        - Initialize the metadata object to handle raster tags and standard output profiles for rasterio.  The
          landcover map provided for the first reference year is used as a master.

        - Generate raster masks of the reporting_regions and statistics_regions, which can be used for
          block-processing.
        """
        # Generate the bounds for the statistical AOI
        logger.debug('Calculate the raster statistical AOI')
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

        # Set up the accord object with the needed extent
        logger.debug('Initialize the global raster AccoRD object')
        # Note: since we do not give a reference raster file to GeoProcessing object we have to fill some info manually.
        self.accord = GeoProcessing(self.software_name, self.component, self.temp_dir())
        # set the extent for the raster files using the statistical domain
        self.accord.ref_extent = AOI_bbox

        # Set the reference profile in the accord GeoProcessing object
        logger.debug('* give statistic raster info as reference file to the AccoRD object (profile, extent)')
        # we set up the standard raster profile
        self.accord.ref_profile = {'driver': 'GTiff',
                                   'dtype': self.src_profile['dtype'],
                                   'nodata': self.src_profile['nodata'],
                                   'width': int((AOI_bbox.right - AOI_bbox.left) / self.src_res[0]),
                                   'height': int((AOI_bbox.top - AOI_bbox.bottom) / self.src_res[1]),
                                   'count': 1,
                                   'crs': rasterio.crs.CRS.from_epsg(self.epsg),
                                   'transform': rasterio.transform.from_origin(AOI_bbox.left, AOI_bbox.top,
                                                                               self.src_res[0], self.src_res[1]),
                                   'blockxsize': 256,
                                   'blockysize': 256,
                                   'tiled': True,
                                   'compress': 'deflate',
                                   'interleave': 'band',
                                   'bigtiff': 'if_safer'}
        # create rasterio profile for the reporting area also for further processing
        logger.debug('* set up the raster profile and extent for the reporting regions')
        self._create_reporting_profile()

        # Check if input land cover map covers the required AOI
        land_cover_year0 = self.config[_LAND_COVER][self.years[0]]
        logger.debug(
            '* pre-check the provided MASTER land cover raster file if all statistical regions are covered')
        try:
            aoi_ok = self.accord.vector_in_raster_extent_check(land_cover_year0, self.statistics_shape,
                                                               check_projected=True, check_unit=True,
                                                               stand_alone=True)
        except Error as e:
            # Catch Error exceptions and re-raise as ConfigError linked to the config['landcover'][self.years[0]]:
            raise ConfigError(e.message, [_LAND_COVER, self.years[0]])
        if not aoi_ok:
            raise ConfigError(
                'Not all needed statistical_regions specified by the reporting_regions are included in the ' +
                'provided land cover map ({}). '.format(land_cover_year0) +
                'Please provide a land cover map with a minimum extent of {} in EPSG:{}.'.format(AOI_bbox,
                                                                                                 self.epsg),
                [_LAND_COVER, self.years[0]])

    def _create_reporting_profile(self):
        """Generate the reporting regions rasterio profile out of the profile of the statistical regions."""
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

    def _rasterize_shapes(self):
        """Rasterize reporting and statistics shapes for the AOI."""
        # Generate a raster version of stats and reporting vector file for blockprocessing tasks
        logger.debug('Create raster versions of statistic and reporting vectors for block processing tasks')
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

    def adjust_rasters(self, config_check):
        """If needed, warp or clip input raster data so it matches the current calculation's extent and projection.

        This function can only be called after calling :meth:`validate`.
        """
        if not config_check._validated:  # Must validate and check all configitems before we can run this method
            raise RuntimeError('adjust_rasters() called on ConfigCheck before validation.  This is an error.')
        config_rasters = config_check.get_configitems(ConfigRaster)
        config_rasterdirs = config_check.get_configitems(ConfigRasterDir)
        rasterlists = {rasterdir: glob.glob(os.path.join(rasterdir.value, '*.tif')) +
                       glob.glob(os.path.join(rasterdir.value, '*.tiff'))
                       for rasterdir in config_rasterdirs if rasterdir.value}
        num_rasters = len(config_rasters) + sum(len(lst) for lst in rasterlists.values())
        if num_rasters == 0:
            return

        progress_per_raster = 100. / num_rasters  # for progress bar
        for raster in config_rasters:
            if raster.value is not None:
                # Make a safe output filename for each different raster in the configuration
                output_filename = '_'.join(str(x) for x in raster._path)
                output_filename = ''.join(char if char.isalnum() else '_' for char in output_filename)
                output_path = os.path.join(self.temp_dir(), output_filename +
                                           '_{}m_EPSG{}.tif'.format(
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
        """Increment total progress and pass on to progress bar callback function.

        The sum of all increments `p` during an entire run should equal 100.
        """
        if self._progress_callback is not None:
            self._progress += p
            self._progress_callback(p)

    def _add_progress_prerun(self, p):
        """Update progress bar outside of the main run phase.

        This method should be used to update the progress bar for calculations in during initialization and raster
        checks, outside of the specific ecosystem service run itself.
        """
        self._progress += p * (1 - self._progress_weight_run)
        if self._progress_callback:
            self._progress_callback(self._progress)
