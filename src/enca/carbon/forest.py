import logging
import math
import os

import numpy as np
import pandas as pd
import rasterio

import enca
from enca.framework.errors import Error
from enca.framework.config_check import ConfigRaster, ConfigItem, check_csv, ConfigError
from enca.framework.geoprocessing import GeoProcessing, RasterType, block_window_generator, SHAPE_ID, MINIMUM_RESOLUTION
from enca.framework.run import _LAND_COVER

LAND_COVER_FRACTION = 'land_cover_fraction'
WOOD_REMOVAL_LIMIT = 'wood_removal_limitation'

FOREST_LC_CLASSES = 'forest_lc_classes'

FAOFRA_AGB = 'faofra_agb'
FAOFRA_BGB = 'faofra_bgb'
FAOFRA_LITTER = 'faofra_litter'
FAOFRA_WREM = 'faofra_wood_removals'

logger = logging.getLogger(__name__)


class CarbonForest(enca.ENCARun):
    """Forest carbon preprocessing run."""

    run_type = enca.RunType.PREPROCESS
    component = 'CARBON_FOREST'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                FOREST_LC_CLASSES: ConfigItem(),
                LAND_COVER_FRACTION: ConfigRaster(raster_type=RasterType.ABSOLUTE_POINT),
                WOOD_REMOVAL_LIMIT: ConfigRaster(raster_type=RasterType.ABSOLUTE_POINT),
                FAOFRA_AGB: ConfigItem(check_csv),
                FAOFRA_BGB: ConfigItem(check_csv),
                FAOFRA_LITTER: ConfigItem(check_csv),
                FAOFRA_WREM: ConfigItem(check_csv)}
        })

        self.cf_clean = os.path.join(self.temp_dir(), 'cf_clean_{year}.tif')
        self.cf_clean_wr = os.path.join(self.temp_dir(), 'cf_clean_wr_{year}.tif')

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
                                   'bigtiff': 'if_saver'}
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
            raise ConfigError(e.message, [enca._LAND_COVER, self.years[0]])
        if not aoi_ok:
            raise ConfigError(
                'Not all needed statistical_regions specified by the reporting_regions are included in the ' +
                'provided land cover map ({}). '.format(land_cover_year0) +
                'Please provide a land cover map with a minimum extent of {} in EPSG:{}.'.format(AOI_bbox,
                                                                                                 self.epsg),
                [enca._LAND_COVER, self.years[0]])

    def _start(self):
        print('Hello from ENCA Carbon Forest preprocessing.')
        for year in self.years:
            self.create_clean_cf(year)
            self.make_forest_carbon_maps(year)

    def create_clean_cf(self, year, block_shape=(1024, 1024)):
        """Set unneeded cover fraction pixel values to nodata (=0).

        For the wood removal calculation, we reduce the cover fraction values by the wood removal limitation value.
        """
        comp_config = self.config[self.component]
        with rasterio.open(self.config[enca.LAND_COVER][year]) as ds_lc, \
             rasterio.open(comp_config[LAND_COVER_FRACTION]) as ds_cf, \
             rasterio.open(comp_config[WOOD_REMOVAL_LIMIT]) as ds_wr_limit, \
             rasterio.open(self.cf_clean.format(year=year), 'w' ,
                           **dict(ds_lc.profile, dtype=rasterio.uint8,
                                  nodata=0, compress='lzw')) as ds_out, \
             rasterio.open(self.cf_clean_wr.format(year=year), 'w', compress='lzw', **ds_out.profile) as ds_out_wr:
            for _, window in block_window_generator(block_shape, ds_lc.profile['height'], ds_lc.profile['width']):
                cf = ds_cf.read(1, window=window)
                lc = ds_lc.read(1, window=window)

                # Set cover fraction to nodata outside forest:
                cf[~np.isin(lc, comp_config[FOREST_LC_CLASSES])] = ds_out.nodata
                cf[np.isin(lc, comp_config[FOREST_LC_CLASSES]) & ((cf == 0) | (cf == 255))] = 1
                ds_out.write(cf.astype(ds_out.profile['dtype']), 1, window=window)

                # adjust cover fraction in protected area for reallocation of carbon in woodremoval:
                limit = ds_wr_limit.read(1, window=window)
                valid = (cf > 0) & (cf <= 100)
                cf[valid] *= limit[valid]
                ds_out_wr.write(cf.astype(ds_out_wr.profile['dtype']), 1, window=window)

    def make_forest_carbon_maps(self, year, block_shape=(1024, 1024)):
        """Disaggregate AGB/BGB/Litter/Removals statistics using tree cover fraction as a proxy."""
        comp_config = self.config[self.component]
        agb = pd.read_csv(comp_config[FAOFRA_AGB], sep=';', index_col=enca.GID_0)[f'agbCt_{year}']
        bgb = pd.read_csv(comp_config[FAOFRA_BGB], sep=';', index_col=enca.GID_0)[f'bgbCt_{year}']
        litter = pd.read_csv(comp_config[FAOFRA_LITTER], sep=';', index_col=enca.GID_0)[f'litterCt_{year}']
        removals = pd.read_csv(comp_config[FAOFRA_WREM], sep=';', index_col=enca.GID_0)[f'WremCt_{year}']

        path_cf = self.cf_clean.format(year=year)
        logger.debug('Spatial disaggregation for above-ground biomass.')
        proxy_sums = self.accord.spatial_disaggregation_byArea(path_cf, agb,
                                                               self.admin_raster, self.admin_shape[SHAPE_ID],
                                                               os.path.join(self.maps, f'agb_{year}.tif'))
        logger.debug('Spatial disaggregation for below-ground biomass.')
        self.accord.spatial_disaggregation_byArea(path_cf, bgb,
                                                  self.admin_raster, self.admin_shape[SHAPE_ID],
                                                  os.path.join(self.maps, f'bgb_{year}.tif'),
                                                  proxy_sums=proxy_sums)
        logger.debug('Spatial disaggregation for forest litter.')
        self.accord.spatial_disaggregation_byArea(path_cf, litter,
                                                  self.admin_raster, self.admin_shape[SHAPE_ID],
                                                  os.path.join(self.maps, f'litter_{year}.tif'),
                                                  proxy_sums=proxy_sums)
        logger.debug('Spatial disaggregation for wood removal.')
        self.accord.spatial_disaggregation_byArea(self.cf_clean_wr.format(year=year), removals,
                                                  self.admin_raster,  self.admin_shape[SHAPE_ID],
                                                  os.path.join(self.maps, f'removals_{year}.tif'))
