import os
import subprocess

import rasterio
import rasterio.warp as warp

import enca
from enca.framework.config_check import ConfigItem, YEARLY
from enca.framework.geoprocessing import block_window_generator, RasterType

FOREST_BIOMASS = 'forest_biomass'
BURNT_AREAS = 'burnt_areas'

_fire_intensity = 0.2
_weight_2_carbon = 0.475

class CarbonFire(enca.ENCARun):

    run_type = enca.PREPROCESS
    component = 'CARBON_FIRE'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                FOREST_BIOMASS: ConfigItem(),
                BURNT_AREAS: {YEARLY: ConfigItem()}
                }})

        self.file_biomass_modis = os.path.join(self.temp_dir(), f'{FOREST_BIOMASS}_AOI.tiff')


    def _start(self):
        print('Hello from carbon fire preprocessing.')
        self.preprocess_biomass()
        for year in self.years:
            self.fire_carbon(year)

    def preprocess_biomass(self):
        """Bring required subset of biomass raster to MODIS projection/raster."""
        # Assuming all burnt area rasters have identical raster projection/resolution/extent:
        file_modis = self.config[self.component][BURNT_AREAS][self.years[0]]
        file_biomass = self.config[self.component][FOREST_BIOMASS]
        with rasterio.open(self.reporting_raster) as ds_aoi, rasterio.open(file_modis) as ds_modis:
            aoi_bounds = warp.transform_bounds(ds_aoi.crs,  ds_modis.crs, *ds_aoi.bounds)
            aoi_window = ds_modis.window(*aoi_bounds).round_offsets().round_lengths(op='ceil')
            bbox = rasterio.windows.bounds(aoi_window, ds_modis.transform)
            cmd = (f'gdalwarp --config GDAL_CACHEMAX 256 -overwrite -t_srs "{ds_modis.crs}" '
                   f'-te {bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]} '
                   f'-tr {ds_modis.res[0]} {ds_modis.res[1]} '
                   '-r bilinear -co COMPRESS=DEFLATE -co INTERLEAVE=BAND -co TILED=YES -overwrite '
                   f'"{file_biomass}" "{self.file_biomass_modis}"')
        subprocess.check_call(cmd, shell=True)

    def fire_carbon(self, year, block_shape=(1024, 1024)):
        """Mask biomass with MODIS burnt areas, and convert to carbon."""
        # Assuming biomass_unit == 'Mg/ha':
        c_factor = _weight_2_carbon * _fire_intensity
        file_carbon_temp = os.path.join(self.temp_dir(), f'{self.component}_{year}_biomass_grid.tif')
        with rasterio.open(self.file_biomass_modis) as ds_biomass, \
             rasterio.open(self.config[self.component][BURNT_AREAS][year]) as ds_modis, \
             rasterio.open(file_carbon_temp, 'w',
                           **dict(ds_biomass.profile, nodata=-9999., compress='deflate')) as ds_out:
            for _, window in block_window_generator(block_shape, ds_out.profile['height'], ds_out.profile['width']):
                biomass = ds_biomass.read(1, window=window, masked=True)
                # ds_biomass and ds_out are defined on a subgrid of the modis dataset (covering only our AOI)
                # -> convert the window from ds_out to ds_modis:
                window_modis = ds_modis.window(*ds_out.window_bounds(window))
                burnt = ds_modis.read(1, window=window_modis, masked=True)
                c_sum = biomass * burnt * c_factor
                ds_out.write(c_sum.filled(ds_out.nodata).astype(ds_out.profile['dtype']), 1, window=window)
        self.accord.AutomaticBring2AOI(file_carbon_temp, RasterType.RELATIVE,
                                       path_out=os.path.join(self.maps, f'NCA_{self.component}_{year}.tif'))
