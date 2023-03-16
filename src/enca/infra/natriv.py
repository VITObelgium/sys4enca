'''
Like NATURALIS but for rivers

inputs:
* Naturalis (combined KBA + WDPA, non-smoothed version)

Created on Oct 26, 2020

@author: smetsb
'''

import rasterio
import numpy as np
from enca.framework.geoprocessing import block_window_generator


class NATRIV(object):

    def __init__(self, runObject):
        '''
        Constructor
        '''
        self.natriv = runObject.natriv
        self.accord = runObject.accord
        self.naturalis = runObject.naturalis_shape
        self.block_shape = (2048,2048)


    def create_natriv(self,path_Gloric_mask):

        out_profile = self.accord.ref_profile.copy()
        out_profile.update(dtype=rasterio.float32, driver='GTiff', compress='lzw', nodata=0)
        with rasterio.open(self.natriv,'w', **out_profile) as dst,\
                rasterio.open(self.naturalis) as src_naturalis,\
                rasterio.open(path_Gloric_mask) as src_river:
            for _, window in block_window_generator(self.block_shape, dst.height, dst.width):
                aNaturalis = src_naturalis.read().astype(np.float32)
                aRiver = src_river.read()

                aNaturalis = aNaturalis / 100.  # normalize
                aNaturalis[aNaturalis == 0.] = 0.05  # avoid zeros
                aNatriv = aNaturalis[0, :, :] * aRiver[0, :, :]

                dst.write(aNatriv.astype(rasterio.float32), 1)
