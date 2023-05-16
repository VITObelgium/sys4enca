import logging
import os

import rasterio

import enca
from enca.framework.config_check import ConfigItem
from enca.framework.geoprocessing import RasterType, block_window_generator

logger = logging.getLogger(__name__)


_GHS_POP = 'GHS_POP'

_block_shape = (256, 256)


class Usage(enca.ENCARun):

    run_type = enca.RunType.PREPROCESS
    component = 'WATER_USAGE'

    def __init__(self, config):
        super().__init__(config)

        self.config_template.update({
            self.component: {
                _GHS_POP: ConfigItem()}})  # Dict of original GSH POP rasters

    def _start(self):
        self.prepare_ghs_pop()

    def prepare_ghs_pop(self):
        # warp and possibly interpolate GSH POP rasters as needed.
        #
        # warping GHS_POP accurately is an expensive operation, so first find out which input rasters we actually need
        # for the years we want to process.
        years_input = sorted(self.config[self.component][_GHS_POP].keys())
        years_needed = set()
        for year in self.years:
            if year in years_input:
                years_needed.add(year)
                continue

            # GHS_POP input does not contain this year, so find the years needed for linear interpolation/extrapolation:
            i0 = find_interval(year, years_input)
            years_needed.add(years_input[i0])
            years_needed.add(years_input[i0 + 1])

        # Now warp input for all needed years to our AOI:
        res = int(self.accord.ref_profile['transform'].a)
        epsg = self.accord.ref_profile['crs'].to_epsg()
        ghs_pop_aoi = {}
        for year in years_needed:
            input_file = self.config[self.component][_GHS_POP][year]
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
            # TODO add warnings in case of extrapolation
            out_file = os.path.join(self.maps, f'GHS_POP_interpolated-data_{year}_{res}m_EPSG{epsg}.tif')
            with rasterio.open(ghs_pop_aoi[year0]) as ds_year0, rasterio.open(ghs_pop_aoi[year1]) as ds_year1, \
                 rasterio.open(out_file, 'w', **dict(ds_year0.profile,
                                                     Info=f'Interpolated GHS POP data in inhabitant per pixel for year {year}.',
                                                     NODATA_value=ds_year0.nodata,
                                                     VALUES='valid: > 0',
                                                     PIXEL_UNIT='inhabitants')) as ds_out:
                for _, window in block_window_generator(_block_shape, ds_out.profile['height'], ds_out.profile['width']):
                    pop0 = ds_year0.read(1, window=window)
                    pop1 = ds_year1.read(1, window=window)

                    # Linear interpolation:
                    t = (year - year0) / float(year1 - year0)
                    pop_interp = t * pop1 + (1. - t) * pop0

                    ds_out.write(pop_interp.astype(ds_out.profile['dtype']), 1, window=window)

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
