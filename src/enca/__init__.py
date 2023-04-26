import gettext
import logging
import os
from enum import Enum

from importlib.metadata import PackageNotFoundError, version
from importlib.resources import as_file, files

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

import pandas as pd
import pyproj
import rasterio

import enca
import enca.parameters
from enca.framework.config_check import YEARLY, ConfigError, ConfigItem, ConfigRaster, check_csv
from enca.framework.run import Run
from enca.framework.geoprocessing import SHAPE_ID, number_blocks, block_window_generator, statistics_byArea, RasterType

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
#should also be possible to be GID_1
GID_0 = 'GID_0'
C_CODE = 'C_CODE'
CODE = 'CODE'

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
    id_col_reporting = GID_0

    epsg = 3857
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
            col_dlct = f'DLCT_{year}'
            grp_dlct = df.join(self.statistics_shape[col_dlct]).groupby(col_dlct)
            # Aggregate sum per DLCT for all columns, plus count of a single column to get 'num_SELU':
            results_dlct = grp_dlct.sum().join(grp_dlct[AREA_RAST].count().rename('num_SELU'))
            results = pd.concat([results, results_dlct.T], axis=1)

            # weighted average for some of the indicators:
            for index in self._indices_average:
                results.loc[index] /= results.loc[AREA_RAST]

            results = pd.merge(results, self.load_lut(), left_index=True, right_index=True)
            results.to_csv(os.path.join(self.reports, f'NCA_{self.component}_report_{area.Index}_{year}.csv'))

    @classmethod
    def load_lut(cls):
        """Return a `pd.DataFrame` with the index codes and their descriptions."""
        with files(enca).joinpath(f'data/LUT_{cls.component}_INDEX_CAL.csv').open() as f:
            return pd.read_csv(f, sep=';').set_index(CODE)
