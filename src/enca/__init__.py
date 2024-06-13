import gettext
import logging
import math
import os
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import as_file, files

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import pandas as pd
import pyproj
import rasterio

import enca
import enca.parameters
from enca.framework.config_check import (
    YEARLY,
    ConfigError,
    ConfigItem,
    ConfigRaster,
    check_csv,
)
from enca.framework.errors import Error
from enca.framework.geoprocessing import (
    MINIMUM_RESOLUTION,
    POLY_MIN_SIZE,
    SHAPE_ID,
    GeoProcessing,
    RasterType,
    block_window_generator,
    number_blocks,
    statistics_byArea,
)
from enca.framework.run import _LAND_COVER, Run

try:
    dist_name = 'sys4enca'
    __version__ = version(dist_name)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
finally:
    del PackageNotFoundError

with as_file(files(__name__).joinpath('locale')) as path:
    t = gettext.translation(dist_name, path, fallback=True)
    _ = t.gettext

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class RunType(Enum):
    """ENCA runs belong to one of these types."""

    ENCA = 0  #: Regular run for a single component.
    ACCOUNT = 1  #: Yearly account or trend.
    PREPROCESS = 2  #: Preprocessing.


HYBAS_ID = 'HYBAS_ID'
ADMIN_ID = 'ADMIN_ID'  # id attribute for administrative boundaries shapefile
REP_ID = 'REP_ID'  # reporting id
C_CODE = 'C_CODE'
CODE = 'CODE'

_ADMIN_BOUNDS = 'admin_boundaries'  # Administrative boundaries shapefile

AREA_RAST = 'Area_rast'

PARAMETERS_CSV = 'parameters_csv'
LAND_COVER = 'land_cover'

# Use non-interactive matplotlib backend
matplotlib.use('Agg')


class ENCARun(Run):
    """Run class with extra properties for ENCA."""

    component = None  #: ENCA component, to be set in each subclass.
    run_type = None  #: One of ENCA, ACCOUNT, or PREPROCESS
    software_name = 'ENCA Tool'
    id_col_statistics = HYBAS_ID
    id_col_reporting = REP_ID


    _indices_average = None  #: List of SELU-wide indicators, to be defined in each subclass

    def __init__(self, config):
        """Initialize an ENCA run."""
        super().__init__(config)
        self.root_logger = logger
        try:
            self.aoi_name = config['aoi_name']
            self.tier = config['tier']
        except KeyError as e:
            raise ConfigError(f'Missing config key {str(e)}', [str(e)])

        self.config_template.update({
            PARAMETERS_CSV: ConfigItem(check_csv, optional=True),
            LAND_COVER: {YEARLY: ConfigRaster(raster_type=RasterType.CATEGORICAL)},
            }
        )

        self.run_dir = os.path.join(self.output_dir, self.aoi_name, str(self.tier), self.run_type.name, self.component,
                                    self.run_name)
        self.maps = os.path.join(self.run_dir, 'maps')
        self.reports = os.path.join(self.run_dir, 'reports')
        self.statistics = os.path.join(self.run_dir, 'statistics')
        self.parameters = enca.parameters.defaults

        self.admin_shape = None
        self.admin_raster = None

        logger.debug('Running with config:\n%s', config)

    def _create_dirs(self):
        super()._create_dirs()

        os.makedirs(self.maps, exist_ok=True)
        os.makedirs(self.reports, exist_ok=True)
        os.makedirs(self.statistics, exist_ok=True)

    def _configure(self):
        """Add extra configure steps for ENCA.

        - Update default parameters with custom parameters provided by the user.
        """
        super()._configure()

        if self.config['parameters_csv']:
            custom_params = enca.parameters.read(self.config['parameters_csv'])
            logger.debug('Custom parameters:\n%s', custom_params)
            # Check parameters_csv contains no typos / wrong parameter names
            unknown_params = [param for param in custom_params if param not in self.parameters]
            if unknown_params:
                raise ConfigError(f'Unknown parameter(-s) in custom parameters csv file: {", ".join(unknown_params)}.',
                                  ['parameters_csv'])
            self.parameters.update(custom_params)

    def version_info(self):
        """Return string with describing version of ENCA and its main dependencies."""
        return f'ENCA version {__version__} using ' \
               f'GDAL (osgeo) {version("GDAL")}, rasterio {version("rasterio")}, geopandas {version("geopandas")} ' \
               f'numpy {version("numpy")}, pandas {version("pandas")}, ' \
               f'pyproj {pyproj.__version__}., PROJ {pyproj.proj_version_str}'

    def area_stats(self, block_shape=(2048, 2048), add_progress=lambda p: None):
        """Count number of pixels per statistics region, and number of overlapping pixels with each reporting region.

        :return: Dataframe with a multiindex from reporting shape and statistics shape indices, and the number of
         pixels in the intersection of each pair (reporting_shape, statistics_shape).
        """
        # counts of overlapping pixels per window
        REPORT_NUM = 'REPORT_NUM'
        STATS_NUM = 'STATS_NUM'
        wdw_blocks = []
        with rasterio.open(self.statistics_raster) as ds_stats, \
                rasterio.open(self.reporting_raster) as ds_reporting:
            nblocks = number_blocks(ds_stats.profile, block_shape)

            for _, window in block_window_generator(block_shape, ds_stats.profile['height'], ds_stats.profile['width']):
                stats = ds_stats.read(1, window=window).flatten()
                reporting = ds_reporting.read(1, window=window).flatten()
                valid = stats != ds_stats.nodata
                df_window = pd.DataFrame({
                    REPORT_NUM: reporting[valid],
                    STATS_NUM: stats[valid]})

                # Remove pixels outside of statistics regions
                wdw_blocks.append(df_window.groupby([REPORT_NUM, STATS_NUM]).size())
                add_progress(100. / nblocks)

        df_overlap = pd.concat(wdw_blocks).groupby([REPORT_NUM, STATS_NUM]).sum().rename('count')

        # Transform REPORT_NUM and STATS_NUM back to statistics and reporting shape id's by joining with corresponding
        # columns of reporting_shape and statistics_shape.
        # ... we need some index resetting and renaming to do this
        df_overlap = df_overlap.to_frame().join(
            self.statistics_shape[SHAPE_ID].rename(STATS_NUM).reset_index().set_index(STATS_NUM)).join(
            self.reporting_shape[SHAPE_ID].rename(REPORT_NUM).reset_index().set_index(REPORT_NUM)).set_index(
            [self.reporting_shape.index.name, self.statistics_shape.index.name]
        )

        return df_overlap

    def _load_region_shapes(self):
        """Extend _load_region_shapes to also load the administrative boundaries file."""
        super()._load_region_shapes()

        file_admin_boundaries = self.config.get(_ADMIN_BOUNDS)
        if not file_admin_boundaries:
            raise ConfigError('Please provide a vector file describing the adminstrative boundaries '
                              'used in regional statistics data.', [_ADMIN_BOUNDS])

        try:
            self.admin_shape = gpd.read_file(file_admin_boundaries).set_index(ADMIN_ID)
        except KeyError:
            raise ConfigError(f'The provided file "{file_admin_boundaries}" for administrative boundaries does not have '
                              f'a column {ADMIN_ID}.', [_ADMIN_BOUNDS])
        except Exception as e:
            raise Error(f'Failed to read administrative boundaries input file "{file_admin_boundaries}": {e}.')

        try:
            check_epsg = self.admin_shape.crs.to_epsg()
        except Exception:
            raise ConfigError('Please provide an administrative boundaries shapefile with a valid EPSG projection.',
                              [_ADMIN_BOUNDS])

        if check_epsg != self.epsg:
            self.admin_shape.to_crs(epsg=self.epsg, inplace=True)
            logger.debug('Warped administrative boundaries vector file.')

        self.admin_shape[SHAPE_ID] = range(1, 1 + self.admin_shape.shape[0])

        # Get the administrative regions needed to cover all selected SELU ("statistical") regions:
        logger.debug('Clip administrative boundaries shapefile by selected statistical regions.')
        df_check = gpd.clip(self.admin_shape, self.statistics_shape)
        # Remove empty / false areas:
        df_check = df_check[~df_check.is_empty]
        df_check = df_check[df_check.area > POLY_MIN_SIZE]
        # Check if the adminstrative regions cover the set of seleted statistical regions:
        area_delta = self.statistics_shape.area.sum() - df_check.area.sum()
        '''
        if abs(area_delta) > (self.src_res[0] * self.src_res[1] / 3.):
            raise ConfigError('The administrative boundaries shapefile does not cover all selected SELU shapes.',
                              [_ADMIN_BOUNDS])
        '''
        # Extract the selected regions:
        self.admin_shape = self.admin_shape.reindex(df_check.index.unique()).sort_index()

    def _rasterize_shapes(self):
        """Extend _rasterize_shapes to rasterize the administrative boundary file.

        By default, we rasterize the admin boundaries shapefile for the same extent as the statistics (=SELU) shapes.
        """
        super()._rasterize_shapes()
        self.admin_raster = os.path.join(self.temp_dir(), 'admin_shape_rasterized.tif')
        self.accord.rasterize(self.admin_shape, SHAPE_ID, self.admin_raster, guess_dtype=True, mode='statistical')

    def selu_stats(self, raster_files):
        """Calculate sum of raster values per SELU region for a dict of input rasters.

        The keys of the input dictionary are used as column labels in the resulting `pd.DataFrame`.

        :param raster_files: Dictionary of labeled input rasters.
        :returns: `pd.DataFrame` with the sum of each raster per SELU region.

        """
        logger.debug('Calculate SELU stats %s', ', '.join(raster_files))
        result = pd.DataFrame(index=self.statistics_shape.index)
        for key, filename in raster_files.items():
            stats = statistics_byArea(filename, self.statistics_raster, self.statistics_shape[SHAPE_ID])
            result[key] = stats['sum']

        return result

    def write_selu_maps(self, parameters, selu_stats, year):
        """Plot some columns of the SELU + statistics GeoDataFrame."""
        for column in parameters:
            fig, ax = plt.subplots(figsize=(10, 10))
            selu_stats.plot(column=column, ax=ax, legend=True,
                            legend_kwds={'label': column, 'orientation': 'horizontal'})
            plt.axis('equal')
            ax.set(title=f'NCA {self.component} map for indicator: {column} \n year: {year}')
            x_ticks = ax.get_xticks().tolist()
            y_ticks = ax.get_yticks().tolist()
            ax.xaxis.set_major_locator(ticker.FixedLocator(x_ticks))
            ax.yaxis.set_major_locator(ticker.FixedLocator(y_ticks))
            ax.set_xticklabels([f'{int(x):,}' for x in x_ticks])
            ax.set_yticklabels([f'{int(x):,}' for x in y_ticks])
            fig.savefig(os.path.join(self.maps, f'NCA_{self.component}_map_year_parameter_{column}_{year}.tif'))
            plt.close('all')

    def write_reports(self, indices, area_stats, year):
        """Write final reporting CSV per reporting area."""
        # Calculate fraction of pixels of each SELU region within reporting regions:
        area_ratios = area_stats['count'] / indices[AREA_RAST]

        # indices for which we want to report plain sum:
        indices_sum = [x for x in indices.columns if x not in self._indices_average]

        for area in self.reporting_shape.itertuples():
            logger.debug('**** Generate report for %s %s', area.Index, year)
            f_area = area_ratios.loc[area.Index]
            # Select indices for SELU's from this reporting area
            df = indices.loc[area_stats.loc[area.Index].index]

            for column in indices_sum:
                df[column] *= f_area

            for column in self._indices_average:
                df[column] *= df[AREA_RAST]

            results = df.sum().rename('total')
            results['num_SELU'] = len(df)

            # Also collect indicators per dominant landcover type
            col_dlct = 'DLCT'
            grp_dlct = df.join(self.statistics_shape[col_dlct]).groupby(col_dlct)
            # Aggregate sum per DLCT for all columns, plus count of a single column to get 'num_SELU':
            results_dlct = grp_dlct.sum().join(grp_dlct[AREA_RAST].count().rename('num_SELU'))
            results = pd.concat([results, results_dlct.T], axis=1)

            # weighted average for some of the indicators:
            for index in self._indices_average:
                results.loc[index] /= results.loc[AREA_RAST]

            results = pd.merge(self.load_lut(), results, left_index=True, right_index=True)
            results.to_csv(os.path.join(self.reports, f'NCA_{self.component}_report_{area.Index}_{year}.csv'))

    def check_leac(self):
        logger.info("Checking if LEAC is available")
        for year in self.years:
            if self.config.get(self.component, {}).get("leac_result",{}).get(year,None):
                logger.info("leac information was manual added")
                continue
            expected_path = os.path.join((self.maps).replace(self.component, 'leac').replace(self.run_name,'leac'),
                                         f'LEAC_{self.aoi_name}_{year}.tif')
            if not os.path.exists(expected_path):
                logger.error('It seems that no input leac location was given and that the default location ' + \
                             f'{expected_path} does not contain a valid raster. please run leac module first.' )
            else:
                self.config[self.component]["leac_result"].update({year : expected_path})
    @classmethod
    def load_lut(cls):
        """Return a `pd.DataFrame` with the index codes and their descriptions."""
        with files(enca).joinpath(f'data/LUT_{cls.component}_INDEX_CAL.csv').open() as f:
            return pd.read_csv(f, sep=';').set_index(CODE)


class ENCARunAdminAOI(ENCARun):
    """ENCA Run where the reference AOI is based on the administrative boundaries shapefile.

    If we need to run spatial disaggregation for statistics per administrative boundary, the reference AOI must contain
    the all adminstrative boundaries for the regios involved.
    """

    def _StudyScopeCheck(self):
        """Override _StudyScopeCheck to set AOI based administrative boundaries shapefile."""
        # Generate the bounds for the AOI
        logger.debug('Calculate the raster AOI based on administrative boundaries')
        # first get total bounds for selected regions in statistical vector file
        # format: minx, miny, maxx, maxy
        bbox = self.admin_shape.total_bounds
        # allign to min_resolution increment to support better merges
        bbox = bbox / MINIMUM_RESOLUTION
        # named tuple - BoundingBox(left, bottom, right, top)
        AOI_bbox = rasterio.coords.BoundingBox(left=math.floor(bbox[0]) * MINIMUM_RESOLUTION,
                                               bottom=math.floor(bbox[1]) * MINIMUM_RESOLUTION,
                                               right=math.ceil(bbox[2]) * MINIMUM_RESOLUTION,
                                               top=math.ceil(bbox[3]) * MINIMUM_RESOLUTION)

        # Set up the accord object with the needed extent
        logger.debug('Initialize the global raster AccoRD object')
        # Note: since we do not give a reference raster file to GeoProcessing object we have to fill some info manually
        self.accord = GeoProcessing(self.software_name, self.component, self.temp_dir())
        # set the extent for the raster files using the statistical domain
        self.accord.ref_extent = AOI_bbox

        # Set the reference profile in the accord GeoProcessing object
        logger.debug('* give administrative boundaries raster info as reference file to the AccoRD object (profile, extent)')
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
                                   'bigtiff': 'if_saver'}
        # create rasterio profile for the reporting area also for further processing
        logger.debug('* set up the raster profile and extent for the reporting regions')
        self._create_reporting_profile()

        # Check if input land cover map covers the required AOI
        land_cover_year0 = self.config[_LAND_COVER][self.years[0]]
        logger.debug(
            '* pre-check the provided MASTER land cover raster file if all admin regions are covered')
        try:
            aoi_ok = self.accord.vector_in_raster_extent_check(land_cover_year0, self.admin_shape,
                                                               check_projected=True, check_unit=True,
                                                               stand_alone=True)
        except Error as e:
            # Catch Error exceptions and re-raise as ConfigError linked to the config['landcover'][self.years[0]]:
            raise ConfigError(e.message, [enca._LAND_COVER, self.years[0]])
        if not aoi_ok:
            raise ConfigError(
                'Not all needed admin regions specified by the reporting_regions are included in the ' +
                'provided land cover map ({}). '.format(land_cover_year0) +
                'Please provide a land cover map with a minimum extent of {} in EPSG:{}.'.format(AOI_bbox,
                                                                                                 self.epsg),
                [enca._LAND_COVER, self.years[0]])
