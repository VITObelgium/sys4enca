'''
In situ monitoring gives additional information on ecosystem potential, in particular its nature value. 
In practical terms, high nature value is given to land areas by the scientific community and by the governments which 
decide of nature conservation measures on the basis of scientific reports. Protected areas are mapped and these maps 
will be used for assessing the high nature value index which will be combined with GBLI in order to identify within 
the “green”landscape what is more or less important and symetrically, in less green areas, what is of importance 
because of the presence of e.g. protected species or habitats.

inputs:
* Protected area map (i.e. WDPA)

Created on Oct 28, 2019

@author: smetsb
'''

import os
import sys
import numpy as np
import subprocess
import traceback
import rasterio

from helper_functions import block_window_generator, rasterize, GSM
from general.process import Grid

class NATURALIS(object):

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
        
        "get grid extent from land cover map"
        self.lc = params.leac.leacOut.__dict__['lc'+str(params.run.yearsL[0])]
        #self.lc = '/data/nca_vol1/saga_test/grids/Land-cover_ProbaV_PS-CLC_GEO_2000_100m_EPSG3035.sdat'
        try:
            '''
            with rs.open(self.lc) as ds:
                self.rows = ds.height
                self.cols = ds.width
                self.bounds = ds.bounds         #BoundingBox(left, bottom, right, top)
                self.affine = ds.transform      #Affine()
                self.crs = ds.crs
                #adjust center / top-left
                self.box_left = self.bounds.left + self.affine[1]/2
                self.box_bottom = self.bounds.bottom + self.affine[1]/2
                self.box_top  = self.box_bottom + ((self.rows-1) * self.affine[1])
                self.box_right = self.box_left + ((self.cols-1) * self.affine[1]) 
            '''
            self.grid = Grid(self.lc)
        except:
            print("Not able to open raster %s to retrieve grid extent " % self.lc)
            traceback.print_stack()
            sys.exit(-1)
        
        return
        
    def grid_PA(self,wdpa_shape):
        
        file = os.path.splitext(os.path.basename(wdpa_shape))[0]+'_nosm_reverse'
        outfile_temp = os.path.join(self.nlep.root_nlep_temp, file)

        rasterize(os.path.splitext(self.lc)[0]+'.tif', wdpa_shape, 'Weight',outfile_temp,dtype='Float64')


        file = os.path.splitext(os.path.basename(wdpa_shape))[0]+'_nosm' + '.tif'
        outfile = os.path.join(self.nlep.root_nlep_temp, file)

        with rasterio.open(outfile_temp, 'r') as ds_open:
            profile = ds_open.profile()
            with rasterio.open(outfile, 'w', **dict(profile, driver='geotiff', dtype=np.ubyte, nodata = 255)) as ds_out:
                for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                    ablock = ds_open.read(1, window=window, masked=True)
                    ablock[ablock == -99999] = 255
                    ablock[ablock == -9999] = 255

                    ds_out.write(ablock, window=window, indexes=1)


        #no-data value is 0 but should be set to 255 instead to smooth in case we have binary 0 - 1000
        
        '''
        tempfile = outfile
        file = os.path.splitext(os.path.basename(wdpa_shape))[0]+'_nosm'
        outfile = os.path.join(self.nlep.root_nlep_temp, file)
        
        cmd = 'gdal_translate -a_nodata 255'
        cmd = cmd + ' ' + tempfile + '.sdat ' + outfile + '.sdat'
        #run it
        if self.options.verbose: print("Running command %s" % cmd )
        try:
            subprocess.check_call(cmd, shell=True)
        except:
            print("GDAL_TRANSLATE failed " + cmd)
            traceback.print_stack()
            raise
        '''

        return outfile
    
    def smooth_PA(self,infile):
        file = (os.path.splitext(os.path.basename(infile))[0]).strip('nosm')+'_sm'+str(self.gaussian_sigma)+'_'+str(self.gaussian_kernel_radius) + '.tif'
        outfile = os.path.join(self.nlep.root_nlep,'stock',file)
        GSM(infile,outfile,self.gaussian_sigma,self.gaussian_kernel_radius)
        return outfile
    