import logging
import os

import numpy as np
import pandas as pd
import rasterio

import enca
from enca.framework.config_check import ConfigRaster, ConfigItem, check_csv
from enca.framework.geoprocessing import RasterType, block_window_generator, SHAPE_ID

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

    run_type = enca.PREPROCESS
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
        proxy_sums = self.accord.spatial_disaggregation_byArea(path_cf, agb,
                                                               self.reporting_raster, self.reporting_shape[SHAPE_ID],
                                                               os.path.join(self.maps, f'agb_{year}.tif'))
        self.accord.spatial_disaggregation_byArea(path_cf, bgb,
                                                  self.reporting_raster, self.reporting_shape[SHAPE_ID],
                                                  os.path.join(self.maps, f'bgb_{year}.tif'),
                                                  proxy_sums=proxy_sums)
        self.accord.spatial_disaggregation_byArea(path_cf, litter,
                                                  self.reporting_raster, self.reporting_shape[SHAPE_ID],
                                                  os.path.join(self.maps, f'litter_{year}.tif'),
                                                  proxy_sums=proxy_sums)

        self.accord.spatial_disaggregation_byArea(self.cf_clean_wr.format(year=year), removals,
                                                  self.reporting_raster,  self.reporting_shape[SHAPE_ID],
                                                  os.path.join(self.maps, f'removals_{year}.tif'))
