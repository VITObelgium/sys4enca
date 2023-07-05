'''
Green background landscape index (GBLI) aims at estimating the sustainable biomass of various land cover types on the basis of 
stocks and flows abundance and their relative independence to anthropogenic inputs.

inputs:
* PS-CLC land cover maps (2 digit acc. Pseudo-Corine types)
* Lookup table to rate each land cover class on greeness

Created on Oct 28, 2019

@author: smetsb
'''
import rasterio
from enca.framework.geoprocessing import block_window_generator, GSM
from enca.classification import CSV_2_dict, reclassification
import os

class GBLI(object):

    def __init__(self, runObject):
        '''
        Constructor
        '''
        config = runObject.config
        self.lut_gbli = config["infra"]["lut_gbli"]
        self.gaussian_kernel_radius = config["infra"]["general"]["gaussian_kernel_radius"]
        self.gaussian_sigma = config["infra"]["general"]["gaussian_sigma"]
        self.leac = config["infra"]["leac_result"]
        self.years = runObject.years
        self.leac_gbli_nosm = runObject.leac_gbli_nosm
        self.leac_gbli_sm = runObject.leac_gbli_sm
        self.leac_gbli_diff = runObject.leac_gbli_diff
        if 'ref_year' in config['infra']:
            self.ref_year  = config['infra']['ref_year']
        else:
            self.ref_year = 0
        self.block_shape = (4096, 4096)


    
    def reclassify_PSCLC(self, year, nodata = 0):
        reclass_dict = CSV_2_dict(self.lut_gbli, old_class='PSCLC_CD', new_class='GBLI2')
        if os.path.exists(self.leac_gbli_nosm[year]):
            return

        with rasterio.open(self.leac[year], 'r') as ds_open:
                profile = ds_open.profile
                with rasterio.open(self.leac_gbli_nosm[year], 'w', **dict(profile, driver='GTiff', \
                                                                                   nodata=nodata)) as ds_out:
                    for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                        aBlock = ds_open.read(1, window=window, masked=True)

                        reclassified, dict_classes  = reclassification(aBlock, reclass_dict, profile["nodata"], nodata)
                        ds_out.write(reclassified, window=window, indexes=1)
    
    def gaussian_smooth(self,year):

        if os.path.exists(self.leac_gbli_sm[year]):
            return

        GSM(self.leac_gbli_nosm[year], self.leac_gbli_sm[year], self.gaussian_sigma, self.gaussian_kernel_radius, self.block_shape)

    
    def diff_gbli(self,year):

        comp_year = self.ref_year

        with rasterio.open(self.leac_gbli_sm[comp_year], 'r') as ds_open, \
                rasterio.open(self.leac_gbli_sm[year], 'r') as ds_open2:
            profile = ds_open.profile
            with rasterio.open(self.leac_gbli_diff[year], 'w', **dict(profile, driver='GTiff')) as ds_out:
                for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                    Ablock = ds_open.read(1, window=window, masked=True)
                    Bblock = ds_open2.read(1, window=window, masked=True)
                    ds_out.write(Ablock -  Bblock, window=window, indexes=1)
