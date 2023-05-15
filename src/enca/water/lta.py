"""Long term average precipiation an evapotranspiration."""

import glob
import os
import time

import numpy as np
import rasterio

import enca
from enca.framework.config_check import ConfigItem, ConfigRaster
from enca.framework.geoprocessing import RasterType

_precipitation_2_m = 0.001  # precipitation input has unit [mm]

_WORLDCLIM = 'worldclim'
_CGIAR_AET = 'CGIAR_AET'

class WaterLTA(enca.ENCARun):

    run_type = enca.RunType.PREPROCESS
    component = 'WATER_LTA'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                _WORLDCLIM: ConfigItem(),  # Worldclim data directory.  Sum monthly data and then bring2aoi
                _CGIAR_AET: ConfigRaster(raster_type=RasterType.ABSOLUTE_POINT)  # configraster -> will be automatically adjusted
            }})

    def _start(self):
        # precipitation:
        precip_aoi = self.annual_precipitation()
        precip_m3 = os.path.join(self.maps, 'NCA_WATER_LTA-precipitation_m3.tif')
        self.mm_to_m3(precip_aoi, 'LTA Annual precipitation in m3 per pixel.', precip_m3)

        # evapotranspiration:
        evapo_m3 = os.path.join(self.maps, 'NCA_WATER_LTA-evapotranspiration_m3.tif')
        self.mm_to_m3(self.config[self.component][_CGIAR_AET], 'LTA Annual evapotranspiration in m3 per pixel.', evapo_m3)

    def annual_precipitation(self):
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
                                  blockysize=64,
                                  blockxsize=64)) as ds_out:
            ds_out.update_tags(file_creation=time.asctime(),
                               creator='sys4enca',
                               Info='Long-term annual precipitation extracted from WORLDCLIM.',
                               NODATA_value=out_profile['nodata'],
                               VALUES='valid: > 0',
                               PIXEL_UNIT='mm water')
            ds_out.write(data, 1)

        return self.accord.AutomaticBring2AOI(annual_precip, RasterType.ABSOLUTE_POINT, secure_run=True)

    def mm_to_m3(self, input, title, output):
        with rasterio.open(input) as ds_in, \
             rasterio.open(output, 'w', **dict(ds_in.profile,
                                               nodata=np.nan,
                                               compress='lzw',
                                               dtype=rasterio.float32,
                                               driver='GTiff',
                                               bigtiff='yes',
                                               tiled=True,
                                               blockysize=64,
                                               blockxsize=64)) as ds_out:
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
