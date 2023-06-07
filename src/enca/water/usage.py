import logging
import os

import numpy as np
import pandas as pd
import rasterio

import enca
from enca.framework.errors import Error
from enca.framework.config_check import ConfigItem, check_csv
from enca.framework.geoprocessing import RasterType, block_window_generator, pixel_area, SHAPE_ID

logger = logging.getLogger(__name__)


_GHS_POP = 'GHS_POP'
_MUNICIPAL = 'municipal_water'
_AGRICULTURAL = 'agricultural_water'
_LC_AGRI = 'lc_agri'

_block_shape = (256, 256)


class Usage(enca.ENCARun):

    run_type = enca.RunType.PREPROCESS
    component = 'WATER_USAGE'

    def __init__(self, config):
        super().__init__(config)

        self.config_template.update({
            self.component: {
                # User can provide a set of GHS POP input files.   Not all years must be provided, we check later on if
                # we have data for the years we need.
                _GHS_POP: {'y1990': ConfigItem(optional=True),
                           'y1995': ConfigItem(optional=True),
                           'y2000': ConfigItem(optional=True),
                           'y2005': ConfigItem(optional=True),
                           'y2010': ConfigItem(optional=True),
                           'y2015': ConfigItem(optional=True),
                           'y2020': ConfigItem(optional=True)},
                _MUNICIPAL: ConfigItem(check_function=check_csv, delimiter=';'),
                _AGRICULTURAL: ConfigItem(check_function=check_csv, delimiter=';'),
                _LC_AGRI: ConfigItem(default=[20])
            }})  # Dict of original GSH POP rasters

    def _start(self):
        ghs_pop_rasters = self.prepare_ghs_pop()

        df_agri = pd.read_csv(self.config[self.component][_AGRICULTURAL], delimiter=';', index_col=enca.ADMIN_ID)
        df_muni = pd.read_csv(self.config[self.component][_MUNICIPAL], delimiter=';', index_col=enca.ADMIN_ID)
        pixel_area_ha = pixel_area(self.accord.ref_profile['crs'], self.accord.ref_profile['transform']) / 10000.
        logger.debug('Pixel area in hectare to convert [m3 / ha] to [m3 / ha]: %s', pixel_area_ha)
        for year in self.years:
            logger.debug('Calculate agri usage for year %s', year)
            agri_mask = self.prepare_agri_mask(year)
            # multiply agri mask with agri water consumption per country.
            # We use spatial disaggregation function for this, setting proxy_sums = 1.
            data_agri = df_agri[f'AWWm3ha_{year}'] * pixel_area_ha
            path_agriusage = os.path.join(self.maps, f'NCA_WATER_AGRIusage_m3_{year}.tif')
            self.accord.spatial_disaggregation_byArea(agri_mask, data_agri,
                                                      self.admin_raster, self.admin_shape[SHAPE_ID],
                                                      path_agriusage,
                                                      proxy_sums=pd.Series(1, index=data_agri.index))
            # multiply ghs_pop raster with muni water consumption per country.
            logger.debug('Calculate muni usage for year %s', year)
            path_muniusage = os.path.join(self.maps, f'NCA_WATER_MUNIusage_m3_{year}.tif')
            data_muni = df_muni[f'MWWm3per_{year}']
            self.accord.spatial_disaggregation_byArea(ghs_pop_rasters[year], data_muni,
                                                      self.admin_raster, self.admin_shape[SHAPE_ID],
                                                      path_muniusage,
                                                      proxy_sums=pd.Series(1, index=data_agri.index))

    def prepare_agri_mask(self, year):
        logger.debug('Create agriculture mask for year %s', year)
        file_out = os.path.join(self.temp_dir(), f'agri_mask_{year}.tif')
        agri_classes = self.config[self.component][_LC_AGRI]
        new_nodata = 0
        with rasterio.open(self.config[enca.LAND_COVER][year]) as ds_lc, \
             rasterio.open(file_out, 'w',
                           **dict(ds_lc.profile,
                                  nodata=new_nodata,
                                  compress='lzw',
                                  dtype=rasterio.ubyte,
                                  driver='GTiff',
                                  bigtiff='yes',
                                  tiled=True,
                                  blockysize=_block_shape[0],
                                  blockxsize=_block_shape[1])) as ds_agri:
            for _, window in block_window_generator(_block_shape, ds_agri.profile['height'], ds_agri.profile['width']):
                lc = ds_lc.read(1, window=window)
                agri = np.isin(lc, agri_classes)
                lc[agri] = 1
                lc[~agri] = new_nodata
                ds_agri.write(lc.astype(rasterio.ubyte), 1, window=window)
        return file_out

    def prepare_ghs_pop(self):
        # warp and possibly interpolate GSH POP rasters as needed.
        #
        # warping GHS_POP accurately is an expensive operation, so first find out which input rasters we actually need
        # for the years we want to process.
        logger.debug('Preparing GHS POP rasters for account AOI.')
        # Convert 'y1995', 'y2000', ... config keys to year integers 1995, 2000, ... for all provided GHS_POP files:
        ghs_pop_input = {}
        for key in self.config_template[self.component][_GHS_POP].keys():
            val = self.config[self.component][_GHS_POP].get(key)
            logger.debug('GHS_POP input for year %s: %s', key, val)
            if val:
                year = int(key[1:])
                ghs_pop_input[year] = val

        years_input = sorted(ghs_pop_input.keys())
        years_needed = set()
        for year in self.years:
            if year in years_input:
                years_needed.add(year)
                continue

            # GHS_POP input does not contain this year, so find the years needed for linear interpolation/extrapolation:
            if len(years_input) < 2:
                raise Error(f'No GHS POP dataset provided for year {year}.  '
                            f'Please provide data for {year}, or suitable data sets for interpolation.')
            i0 = find_interval(year, years_input)
            years_needed.add(years_input[i0])
            years_needed.add(years_input[i0 + 1])

        # Now warp input for all needed years to our AOI:
        res = int(self.accord.ref_profile['transform'].a)
        epsg = self.accord.ref_profile['crs'].to_epsg()
        ghs_pop_aoi = {}
        for year in years_needed:
            input_file = ghs_pop_input[year]
            name = os.path.splitext(os.path.basename(input_file))[0]
            output_file = os.path.join(self.maps, f'{name}_{res}m_EPSG{epsg}.tif')
            ghs_pop_aoi[year] = self.accord.AutomaticBring2AOI(input_file, RasterType.ABSOLUTE_VOLUME,
                                                               path_out=output_file, secure_run=True)

        # Now interpolate for those years that need it:
        years_warped = sorted(ghs_pop_aoi.keys())
        for year in self.years:
            if year in years_input:  # nothing to be done anymore
                continue

            i0 = find_interval(year, years_warped)
            year0 = years_warped[i0]
            year1 = years_warped[i0 + 1]
            logger.debug('Calculate population raster for year %s by interpolating GHS POP years  %s and %s.',
                         year, year0, year1)
            # Linear interpolation weight:
            t = (year - year0) / float(year1 - year0)
            if year < year0 or year > year1:
                logger.warning('Extrapolating GHS POP for years %s and %s to obtain data for %s.',
                               year0, year1, year)
            # TODO add warnings in case of extrapolation
            out_file = os.path.join(self.maps, f'GHS_POP_interpolated-data_{year}_{res}m_EPSG{epsg}.tif')
            with rasterio.open(ghs_pop_aoi[year0]) as ds_year0, rasterio.open(ghs_pop_aoi[year1]) as ds_year1, \
                 rasterio.open(out_file, 'w',
                               **dict(ds_year0.profile,
                                      Info=f'Interpolated GHS POP data in inhabitant per pixel for year {year}.',
                                      NODATA_value=ds_year0.nodata,
                                      VALUES='valid: > 0',
                                      PIXEL_UNIT='inhabitants')) as ds_out:
                for _, window in block_window_generator(_block_shape,
                                                        ds_out.profile['height'], ds_out.profile['width']):
                    pop0 = ds_year0.read(1, window=window)
                    pop1 = ds_year1.read(1, window=window)

                    pop_interp = t * pop1 + (1. - t) * pop0

                    ds_out.write(pop_interp.astype(ds_out.profile['dtype']), 1, window=window)
            ghs_pop_aoi[year] = out_file
        # ghs_pop_aoi now contains GHS POP rasters for our AOI for every required year.
        return ghs_pop_aoi

def find_interval(x, x_in):
    """Given a sorted list of values x_in, find the interval index i such that x_in[i] <= x < x_in[i+1].

    If x is smaller than all elements in the list, return the first interval (index 0).
    If x is larger than all elements in the list, return the last interval (index len(x_in) -2).
    """
    try:
        # Find the first x_in strictly greater than x:
        index_right = next(i for i, xi in enumerate(x_in) if xi > x)
        if index_right == 0:
            return 0
        return index_right - 1
    except StopIteration:  # all x_in <= x
        return len(x_in) - 2
