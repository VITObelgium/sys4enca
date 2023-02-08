'''
Green background landscape index (GBLI) aims at estimating the sustainable biomass of various land cover types on the basis of 
stocks and flows abundance and their relative independence to anthropogenic inputs.

inputs:
* PS-CLC land cover maps (2 digit acc. Pseudo-Corine types)
* Lookup table to rate each land cover class on greeness

Created on Oct 28, 2019

@author: smetsb
'''
import os
import rasterio
from enca.geoprocessing import block_window_generator
from enca.classification import CSV_2_dict, reclassification
    , GSM

class GBLI(object):

    def __init__(self, params, options):
        '''
        Constructor
        '''
        self.lut_gbli = params.nlep.nlepIn.lut_gbli
        self.gaussian_kernel_radius = params.process.gaussian_kernel_radius
        self.gaussian_sigma = params.process.gaussian_sigma
        self.yearsL = params.run.yearsL
        self.nlep = params.nlep.nlepOut
        self.options = options
        self.block_shape = (4096, 4096)
        
        return
    
    def reclassify_PSCLC(self, psclc, nodata = 0):

        file = os.path.splitext(os.path.basename(psclc))[0]+'_gbli_nosm.tif'
        outfile = os.path.join(self.nlep.root_nlep_temp,file)

        reclass_dict = CSV_2_dict(self.lut_gbli, old_class='PSCLC_CD', new_class='GBLI2')

        with rasterio.open(psclc, 'r') as ds_open:
                profile = ds_open.profile
                with rasterio.open(outfile, 'w', **dict(profile, driver='GTiff', nodata=nodata)) as ds_out:
                    for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                        aBlock = ds_open.read(1, window=window, masked=True)

                        reclassified, dict_classes  = reclassification(aBlock, reclass_dict, profile["nodata"], nodata)
                        ds_out.write(reclassified, window=window, indexes=1)

        
        return outfile
    
    def gaussian_smooth(self,gbli_nosm):
        
        file = (os.path.splitext(os.path.basename(gbli_nosm))[0]).strip('nosm')+'sm'+str(self.gaussian_sigma)+'_'+str(self.gaussian_kernel_radius) + '.tif'
        outfile = os.path.join(self.nlep.root_nlep_temp,file)

        GSM(gbli_nosm, outfile, self.gaussian_sigma, self.gaussian_kernel_radius, self.block_shape)

        return outfile
    
    def diff_gbli(self,gbli1_sm, gbli2_sm):

        file = (os.path.splitext(os.path.basename(gbli2_sm))[0]).replace('gbli','gbli-change-'+str(self.yearsL[0])) + '.tif'
        outfile = os.path.join(self.nlep.root_nlep_temp,file)


        with rasterio.open(gbli1_sm, 'r') as ds_open, \
                rasterio.open(gbli2_sm, 'r') as ds_open2:
            profile = ds_open.profile
            with rasterio.open(outfile, 'w', **dict(profile, driver='GTiff')) as ds_out:
                for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                    Ablock = ds_open.read(1, window=window, masked=True)
                    Bblock = ds_open2.read(1, window=window, masked=True)
                    ds_out.write(Ablock -  Bblock, window=window, indexes=1)

            
        return outfile