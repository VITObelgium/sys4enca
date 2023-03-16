'''
In situ monitoring gives additional information on ecosystem potential, in particular its nature value. 
In practical terms, high nature value is given to land areas by the scientific community and by the governments which 
decide of nature conservation measures on the basis of scientific reports. Protected areas are mapped and these maps 
will be used for assessing the high nature value index which will be combined with GBLI in order to identify within 
the “green”landscape what is more or less important and symetrically, in less green areas, what is of importance 
because of the presence of e.g. protected species or habitats.

inputs:
* Protected area map (i.e. naturalis)

Created on Oct 28, 2019

@author: smetsb
'''

import numpy as np
import rasterio

from enca.framework.geoprocessing import block_window_generator, GSM

class NATURALIS(object):

    def __init__(self, runObject):
        '''
        Constructor
        '''
        self.gaussian_kernel_radius = runObject.config["infra"]["general"]["gaussian_kernel_radius"]
        self.gaussian_sigma = runObject.config["infra"]["general"]["gaussian_sigma"]
        self.block_shape = (4096, 4096)
        self.naturalis = runObject.config["infra"]["nlep"]["naturalis"]
        self.nosm_reverse = runObject.naturalis_nosm_reverse
        self.nosm = runObject.naturalis_nosm
        self.sm = runObject.naturalis_sm
        self.accord = runObject.accord
        
        return
        
    def grid_PA(self):

        self.accord.rasterize(self.naturalis, 'Weight',self.nosm_reverse,dtype='Float64')

        with rasterio.open(self.nosm_reverse, 'r') as ds_open:
            profile = ds_open.profile()
            with rasterio.open(self.nosm, 'w', **dict(profile, driver='geotiff', dtype=np.ubyte, nodata = 255)) as ds_out:
                for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                    ablock = ds_open.read(1, window=window, masked=True)
                    ablock[ablock == -99999] = 255
                    ablock[ablock == -9999] = 255

                    ds_out.write(ablock, window=window, indexes=1)

    
    def smooth_PA(self):
        GSM(self.nosm,self.sm,self.gaussian_sigma,self.gaussian_kernel_radius)
