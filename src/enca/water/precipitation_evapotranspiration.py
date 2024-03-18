"""Yearly and Long-term average precipitation and evapotranspiration."""

import glob
import logging
import os
import time
from datetime import datetime, timedelta, date
from calendar import monthrange

#import netCDF4
import numpy as np
import rasterio

import enca
from enca.framework.config_check import ConfigItem, ConfigRaster, YEARLY
from enca.framework.geoprocessing import RasterType, block_window_generator

_precipitation_2_m = 0.001  # precipitation input has unit [mm]

_WORLDCLIM = 'worldclim'
_CGIAR_AET = 'CGIAR_AET'
_PRECIPITATION = 'precipitation'
_COPERNICUS_PRECIPITATION = 'copernicus_precipitation'
_LC_RAINFED_AGRI = 'LC_rainfed_agri'

_block_shape = (256, 256)

logger = logging.getLogger(__name__)


class WaterPrecipEvapo(enca.ENCARun):

    run_type = enca.RunType.PREPROCESS
    component = 'WATER_PRECIPITATION_EVAPOTRANSPIRATION'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                _WORLDCLIM: ConfigItem(),  # Worldclim data directory.  Sum monthly data and then bring2aoi
                _CGIAR_AET: ConfigRaster(raster_type=RasterType.ABSOLUTE_POINT),  # configraster -> will be automatically adjusted
                _COPERNICUS_PRECIPITATION: {YEARLY: ConfigItem()},  # netCDF files with Copernicus global precipitation
                _LC_RAINFED_AGRI: ConfigItem(default=[10, 11, 12, 30])
            }})
        self.lta_precip = None
        self.lta_evapo = None

    def _start(self):
        # precipitation:
        #lta_precip_aoi = self.lta_annual_precipitation()  # [mm]
        self.lta_precip = os.path.join(self.maps, 'NCA_WATER_LTA-precipitation_m3.tif')
        #mm_to_m3(lta_precip_aoi, 'LTA Annual precipitation in m3 per pixel.', self.lta_precip)

        # evapotranspiration:
        self.lta_evapo = os.path.join(self.maps, 'NCA_WATER_LTA-evapotranspiration_m3.tif')
        # mm_to_m3(self.config[self.component][_CGIAR_AET],
        #         'LTA Annual evapotranspiration in m3 per pixel.', self.lta_evapo)

        for year in self.years:
            precipitation_mm = self.convert_copernicus_netcdf(year)
            precipitation_mm_aoi = self.accord.AutomaticBring2AOI(precipitation_mm, RasterType.ABSOLUTE_POINT,
                                                                  secure_run=True)
            precipitation_m3 = os.path.join(self.maps, f'NCA_WATER_precipitation_m3_{year}.tif')
            mm_to_m3(precipitation_mm_aoi, f'Annual precipitation in m3 per pixel for year {year}',
                     precipitation_m3)
            evapotranspiration = self.evapotranspiration(year, precipitation_m3)
            self.et_rainfed_agriculture(year, evapotranspiration)

    def evapotranspiration(self, year, annual_precipitation):
        out_file = os.path.join(self.maps, f'NCA_WATER_evapotranspiration_m3_{year}.tif')
        with rasterio.open(self.lta_evapo) as ds_lta_evapo, \
             rasterio.open(self.lta_precip) as ds_lta_precip, \
             rasterio.open(annual_precipitation) as ds_precip, \
             rasterio.open(out_file, 'w',
                           **ds_lta_precip.profile) as ds_out:
            ds_out.update_tags(file_creation=time.asctime(),
                               creator='sys4enca',
                               Info=f'Annual evapotranspiration in m3 per pixel for year {year}.  '
                               'Extrapolated data from LTA and annual precipitation data.',
                               NODATA_value=np.nan,
                               VALUES='valid: > 0',
                               PIXEL_UNIT='m3 water')
            for _, window in block_window_generator(_block_shape, ds_out.profile['height'], ds_out.profile['width']):
                precip = ds_precip.read(1, window=window)
                lta_precip = ds_lta_precip.read(1, window=window, masked=True)
                lta_evapo = ds_lta_evapo.read(1, window=window, masked=True)

                data = lta_evapo * (precip / lta_precip)

                ds_out.write(data.filled(np.nan).astype(rasterio.float32), 1, window=window)
        return out_file

    def lta_annual_precipitation(self):
        worldclim_dir = self.config[self.component][_WORLDCLIM]
        worldclim_files = glob.glob(os.path.join(worldclim_dir, '*.tif'))

        data = None
        for f in worldclim_files:
            with rasterio.open(f) as ds:
                data_month = ds.read(1)
                if data is None:  # first iteration
                    out_profile = ds.profile
                    data = np.zeros_like(data_month)
                data[data_month > 0] += data_month[data_month > 0]

        annual_precip = os.path.join(self.temp_dir(), 'WORLDCLIM_LTA_annual_precipitation_mm.tif')
        with rasterio.open(annual_precip, 'w',
                           **dict(out_profile,
                                  compress='lzw',
                                  bigtiff='yes',
                                  tiled=True,
                                  blockysize=_block_shape[0],
                                  blockxsize=_block_shape[1])) as ds_out:
            ds_out.update_tags(file_creation=time.asctime(),
                               creator='sys4enca',
                               Info='Long-term annual precipitation extracted from WORLDCLIM.',
                               NODATA_value=out_profile['nodata'],
                               VALUES='valid: > 0',
                               PIXEL_UNIT='mm water')
            ds_out.write(data, 1)

        return self.accord.AutomaticBring2AOI(annual_precip, RasterType.ABSOLUTE_POINT, secure_run=True)

    def et_rainfed_agriculture(self, year, evapotranspiration):
        """Create raster of evapotranspiration in areas with rainfed agriculture."""
        out_file = os.path.join(self.maps, f'NCA_WATER_ET-rainfed-agriculture_m3_{year}.tif')
        with rasterio.open(self.config[enca.LAND_COVER][year]) as ds_lc, \
             rasterio.open(evapotranspiration) as ds_evapo, \
             rasterio.open(out_file, 'w', **ds_evapo.profile) as ds_out:
            lc = ds_lc.read(1)
            is_rainfed = np.isin(lc, self.config[self.component][_LC_RAINFED_AGRI])
            data = ds_evapo.read(1, masked=True)
            data[~is_rainfed & ~data.mask] = 0
            ds_out.write(data.filled(np.nan).astype(rasterio.float32), 1)
            ds_out.update_tags(file_creation=time.asctime(),
                               creator='sys4enca',
                               Info='Annual evapotranspiration of rainfed agriculture land and pasture '
                               f'in m3 per pixel for year {year}.',
                               NODATA_value=np.nan,
                               VALUES='valid: > 0',
                               PIXEL_UNIT='m3 water')

    def convert_copernicus_netcdf(self, year):
        """Convert Copernicus precipitation data from netCDF4 format to GeoTiff."""
        # open dataset
        with rasterio.open(self.config[self.component][_COPERNICUS_PRECIPITATION][year]) as src:
            tags = src.tags()
            profile = src.profile
            bounds = src.bounds
            psizex, psizey= src.res

        # check that the variable time is available
        if "NETCDF_DIM_time_VALUES" not in tags.keys():
            raise ValueError("Time variable can not be found in the NetCDF.")


        # these netCDFs are special lons.min() is all the time zeros, but we know that this is a global dataset (subtract 180)
        # but do a check
        if (bounds.right < 180.5) :
            # is a normal netCDF ranging from -180 to +180 in longitude
            affine = profile['transform']
            data_roll = False
        else:
            # is a special netCDF ranging from 0 - 360 in longitude
            # do a data rolling by 180deg and change Image origin
            UL_x, UL_y = bounds.left - 180, bounds.top
            affine = rasterio.transform.from_origin(UL_x, UL_y, abs(psizex), abs(psizey))
            data_roll = True

        # add the other needed profile info
        new_profile = {
            'crs': rasterio.crs.CRS.from_epsg(4326),
            'transform': affine,
            'driver': 'GTiff',
            'compress': 'lzw',
            'tiled': 'False',
            'interleave': 'band',
            'count': 1,
            'dtype': rasterio.float32,
            'nodata': -32767,
            'height': profile['height'],
            'width': profile['width']
        }

        conversion_factor = 1000  # from meter to milimeter
        #actual this means evarge meter per day
        if tags['tp#units'] != 'm':
            raise ValueError(f"Unit of the netCDF does not seem te be correct. It was expected to be in m but is in {tags['tp#units']}")

        # ini output raster
        aOut = np.zeros((profile['height'], profile['width']), dtype=np.float32)

        # loop over all timesteps
        with rasterio.open(self.config[self.component][_COPERNICUS_PRECIPITATION][year]) as src:
            for i in range(profile['count']):
                tags_band = src.tags(i+1)
                refdate  =  date(1900, 1,1)
                timeref = tags_band['NETCDF_DIM_time']
                time_coverage_start = refdate + timedelta(hours=int(timeref))
                startday,days = monthrange(int(year), time_coverage_start.month)

                logger.debug("* Working on timestep: %s/%s")
                # read out data for first time step (scaling and offset is directly applied)
                datax = src.read(i+1, masked = True)*float(tags['tp#scale_factor'])+ float(tags['tp#add_offset'])
                logger.info(datax.dtype)
                # check if masked array - if not create
                if type(datax) != np.ma.core.MaskedArray:
                    datax = np.ma.core.MaskedArray(datax, np.zeros(datax.shape, dtype=bool))

                # do the data_roll if needed to bring data in -180 to +180 longitude format
                if data_roll:
                    logger.debug("** do a data roll to get 0deg center meridian.. ")
                    datax = np.roll(datax, int(datax.shape[1]/2), axis=1)

                aOut += datax.filled(0) * conversion_factor * days

        aOut[aOut < 0] = 0
        if np.any(aOut > 40000):
            logger.warning(f"** It seems that the total percipitation {year} is unusual high please check input and output data")
        path_out_file = os.path.join(self.temp_dir(), f'Copernicus_C3S_ERA5_Total-precipitation_mm_{year}.tif')

        # write out the geoTif with new data
        logger.debug("** write out the final global dataset...")
        if os.path.exists(path_out_file):
            os.remove(path_out_file)

        with rasterio.open(path_out_file, 'w', **new_profile) as dst:
            dst.update_tags(file_creation=time.asctime(),
                            creator='Dr. Bert De Roo (VITO)',
                            Info='Global dataset for annual total precipitation retrieved from reanalysis era5 land monthly webpage.',
                            NODATA_value=-32767,
                            unit='mm',
                            data_year=year)
            dst.write(aOut, 1)

        return path_out_file


def mm_to_m3(input, title, output):
    # TODO use block processing
    with rasterio.open(input) as ds_in, \
         rasterio.open(output, 'w', **dict(ds_in.profile,
                                           nodata=np.nan,
                                           compress='lzw',
                                           dtype=rasterio.float32,
                                           driver='GTiff',
                                           bigtiff='yes',
                                           tiled=True,
                                           blockysize=_block_shape[0],
                                           blockxsize=_block_shape[1])) as ds_out:
        ds_out.update_tags(file_creation=time.asctime(),
                           creator='sys4enca',
                           Info=title,
                           NODATA_value=ds_out.nodata,
                           VALUES='valid: > 0',
                           PIXEL_UNIT='m3 water')
        data = ds_in.read(1).astype(float)
        data *= _precipitation_2_m * float(ds_in.profile['transform'].a) * float(abs(ds_in.profile['transform'].e))
        data[data < 0] = np.nan
        ds_out.write(data.astype(rasterio.float32), 1)
