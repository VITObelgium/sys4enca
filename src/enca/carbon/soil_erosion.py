import logging
import os
import subprocess

import rasterio

import enca
from enca.framework.config_check import ConfigItem, YEARLY
from enca.framework.geoprocessing import block_window_generator, average_rasters


logger = logging.getLogger(__name__)

SOIL_LOSS = 'soil_loss'
R_FACTOR_1 = 'R_factor_1km'
R_FACTOR_25 = 'R_factor_25km'
SOIL_CARBON_10 = 'soil_carbon_10cm'
SOIL_CARBON_20 = 'soil_carbon_20cm'
SOIL_CARBON_30 = 'soil_carbon_30cm'

_nodata_out = -9999

# assuming soil_loss unit: tonne / ha / year, carbon_soil unit: 'g / kg', we need the following conversion factor
_conversion_factor = 0.001

_GDAL_FILLNODATA = 'gdal_fillnodata.bat' if os.name == 'nt' else 'gdal_fillnodata.py'
_GDAL_EDIT = 'gdal_edit.bat' if os.name == 'nt' else 'gdal_edit.py'


class CarbonErosion(enca.ENCARun):
    """Carbon Soil Erosion preprocessing run."""

    component = 'CARBON_SOIL_EROSION'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                # These are all raster datasets, but we use a special method to warp them to desired
                # resolution / CRS / bbox -> don't use ConfigRaster
                R_FACTOR_1: ConfigItem(),
                R_FACTOR_25: ConfigItem(),
                SOIL_CARBON_10: ConfigItem(),
                SOIL_CARBON_20: ConfigItem(),
                SOIL_CARBON_30: ConfigItem(),
                SOIL_LOSS: {YEARLY: ConfigItem()}}})

    def _start(self):
        print('Hello from Carbon Soil Erosion preprocessing.')
        carbon_average = None
        for year in self.years:
            soil_loss = self.soil_erosion_upsample(year)

            if carbon_average is None:
                with rasterio.open(soil_loss) as src:
                    tresolution = src.transform[0]
                    bbox = src.bounds
                    target_crs = src.crs
                carbon_average = self.soil_carbon_average(tresolution, bbox, target_crs)

            self.calculate_erosion_carbon(year, soil_loss, carbon_average)

    def calculate_erosion_carbon(self, year, soil_loss, carbon_content, block_shape=(1024, 1024)):
        """Multiply soil loss an soil carbon, bring to final projection / resolution / extent."""
        path_temp = os.path.join(self.temp_dir(), f'NCA_{self.component}_tons_{year}_temp.tif')
        with rasterio.open(soil_loss) as src_loss, rasterio.open(carbon_content) as src_carbon, \
             rasterio.open(path_temp, 'w', **dict(src_loss.profile,
                                                  compress='lzw',
                                                  dtype=rasterio.float32,
                                                  bigtiff='yes',
                                                  tiled=True,
                                                  nodata=_nodata_out,
                                                  blockysize=block_shape[0],
                                                  blockxsize=block_shape[1])) as out:
            for _, window in block_window_generator(block_shape, out.profile['height'], out.profile['width']):
                loss = src_loss.read(1, window=window, masked=True)
                carbon = src_carbon.read(1, window=window, masked=True)
                result = loss * carbon * _conversion_factor

                out.write(result.filled(out.nodata).astype(out.profile['dtype']), 1, window)

        path_out = os.path.join(self.maps, f'NCA_{self.component}_tons_{year}.tif')

        # Now resample to output resolution and AOI
        with rasterio.open(self.statistics_raster) as src:
            tresolution = src.transform[0]
            bbox = src.bounds
            target_epsg = src.crs.to_epsg()
        cmd = ('gdalwarp --config GDAL_CACHEMAX 256 -overwrite -t_srs '
               'EPSG:{} -te {} {} {} {} -tr {} {} -r bilinear '
               '-co COMPRESS=deflate -co BIGTIFF=YES -multi "{}" "{}"').format(
                   target_epsg, bbox.left, bbox.bottom, bbox.right, bbox.top, tresolution, tresolution,
                   path_temp, path_out)
        subprocess.check_call(cmd, shell=True)
        return path_out

    def soil_carbon_average(self, tresolution, bbox, target_crs, block_shape=(1024, 1024)):
        """Resample soil carbon datasets to upsampled soil loss AOI/resolution/projection and average them."""
        config = self.config[self.component]
        # 2. bring 10/20/30cm soil carbon datasets to same extent
        carbon_10_res = os.path.join(self.temp_dir(), SOIL_CARBON_10 + '_resampled.tif')
        carbon_20_res = os.path.join(self.temp_dir(), SOIL_CARBON_20 + '_resampled.tif')
        carbon_30_res = os.path.join(self.temp_dir(), SOIL_CARBON_30 + '_resampled.tif')
        Resample2AOI(config[SOIL_CARBON_10], carbon_10_res, target_crs, bbox, tresolution, wResampling='bilinear')
        Resample2AOI(config[SOIL_CARBON_20], carbon_20_res, target_crs, bbox, tresolution, wResampling='bilinear')
        Resample2AOI(config[SOIL_CARBON_30], carbon_30_res, target_crs, bbox, tresolution, wResampling='bilinear')
        # 3. average soil carbon datasets
        carbon_mean = os.path.join(self.temp_dir(), 'mean_soil_carbon.tif')
        average_rasters(carbon_mean, carbon_10_res, carbon_20_res, carbon_30_res)
        return carbon_mean

    def soil_erosion_upsample(self, year, block_shape=(1024, 1024)):
        """Upsample soil loss raster with pansharpening approach using R-factor rasters.

        :param year: year of the soil loss raster to work on.
        :returns: path to upsampled soil loss raster

        """
        # 1 get AOI bounds in soil loss EPSG
        # 2 bring 1km R-factor to new AOI
        # 3 bring 25km R-factor to new AOI (using 1km bounds & resolution)
        # 4 bring soil loss dataset to new AOI
        # 5 fill holes in 25km R-factor and soil loss datasets
        # 6 calculate soil loss resampled as (Rhighres / Rlowres) * Loss (~pansharpening approach)
        config = self.config[self.component]
        # first get the AOI bounds in EPSG of high resolution dataset
        with rasterio.open(self.reporting_raster) as src_AOI, rasterio.open(config[R_FACTOR_1]) as src_r_factor1:
            # now we have to re-project the AOI bounds to the input dataset crs
            AOI_bounds = rasterio.warp.transform_bounds(src_AOI.crs,  src_r_factor1.crs,
                                                        src_AOI.bounds.left,
                                                        src_AOI.bounds.bottom,
                                                        src_AOI.bounds.right,
                                                        src_AOI.bounds.top)

        # Cut the high resolution dataset to the bounds
        path_High_AOI = os.path.join(self.temp_dir(), 'High_res_dataset_Cut2AOI.tif')
        if not os.path.exists(path_High_AOI):
            Cut2AOI(config[R_FACTOR_1], path_High_AOI, AOI_bounds)

        # Second warp the low resolution and Soil loss dataset to the same extent 
        path_Low_AOI = os.path.join(self.temp_dir(), 'Low_res_dataset_Cut2AOI.tif')
        # get the new target resolution
        with rasterio.open(path_High_AOI) as src:
            tresolution = src.transform[0]
            bbox = src.bounds
            target_crs = src.crs

        if not os.path.exists(path_Low_AOI):
            Resample2AOI(config[R_FACTOR_25], path_Low_AOI, target_crs, bbox, tresolution, wResampling='cubicspline')

        # Third resample also the soil loss dataset
        path_SoilLoss_AOI = os.path.join(self.temp_dir(), f'soil_loss_{year}_Cut2AOI.tif')
        if not os.path.exists(path_SoilLoss_AOI):
            Resample2AOI(config[SOIL_LOSS][year], path_SoilLoss_AOI, target_crs, bbox, tresolution,
                         wResampling='cubicspline')

        # Fill holes in datasets
        path_Low_AOI_filled = os.path.join(self.temp_dir(), 'Low_res_dataset_Cut2AOI_filled.tif')
        path_SoilLoss_AOI_filled = os.path.join(self.temp_dir(), f'soil_loss{year}_Cut2AOI_filled.tif')
        if not os.path.exists(path_Low_AOI_filled):
            FillHoles(path_Low_AOI, path_Low_AOI_filled)
        if not os.path.exists(path_SoilLoss_AOI_filled):
            FillHoles(path_SoilLoss_AOI, path_SoilLoss_AOI_filled)

        path_out = os.path.join(self.temp_dir(), f'soil_loss_{year}_resampled.tif')
        with rasterio.open(path_High_AOI) as src_data1, \
             rasterio.open(path_Low_AOI_filled) as src_data2, \
             rasterio.open(path_SoilLoss_AOI_filled) as src_data3, \
             rasterio.open(path_out, 'w', **dict(src_data1.meta,
                                                 tiled=True,
                                                 compress='LZW',
                                                 blockysize=block_shape[0],
                                                 blockxsize=block_shape[1],
                                                 nodata=_nodata_out,
                                                 dtype=rasterio.float32)) as dst:
            for index, window in block_window_generator(block_shape,
                                                        dst.profile['height'], dst.profile['width']):
                aHigh = src_data1.read(1, window=window, masked=True)
                aLow = src_data2.read(1, window=window, masked=True)
                aLoss = src_data3.read(1, window=window, masked=True)
                aResult = (aHigh / aLow) * aLoss
                dst.write(aResult.filled(dst.nodata).astype(dst.profile['dtype']),
                          1, window=window)
        return path_out


def Cut2AOI(path_in, path_out, bbox):
    """Cut raster to AOI when in same coordinate system."""
    # get extent, resolution and projection from AOI raster file
    cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=LZW -projwin {} {} {} {} "{}" "{}"'.format(
                                                                  bbox[0], bbox[3], bbox[2], bbox[1],
                                                                  path_in, path_out)
    subprocess.check_call(cmd, shell=True)


def Resample2AOI(path_in, path_out, target_crs, bbox, tresolution, wResampling='bilinear'):
    """Resample a file to an AOI without any checks."""
    cmd = ('gdalwarp --config GDAL_CACHEMAX 256 -overwrite -t_srs "{}" '
           '-te {} {} {} {} -tr {} {} -r {} -co COMPRESS=LZW -co BIGTIFF=YES -multi "{}" "{}"').format(
               str(target_crs).replace('"', '\\"'), bbox.left, bbox.bottom, bbox.right, bbox.top,
               tresolution, tresolution, wResampling, path_in, path_out)
    subprocess.check_call(cmd, shell=True)


def FillHoles(path_in, path_out):
    """Fill nodata holes."""
    cmd = '"{}" -md 25 "{}" "{}"'.format(_GDAL_FILLNODATA , path_in, path_out)
    subprocess.check_call(cmd, shell=True)
    # bring back the nodata value in the file
    with rasterio.open(path_in) as src:
        nodata = src.nodata
    cmd = '"{}" -a_nodata "{}" "{}"'.format(_GDAL_EDIT, nodata, path_out)
    subprocess.check_call(cmd, shell=True)
