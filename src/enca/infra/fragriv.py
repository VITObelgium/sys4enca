'''
Calculate (simple) three-hybas level river fragmentation

inputs:
* Hybas shapefiles (3 levels)
* Dams shapefile

Created on Oct 26, 2020

@author: smetsb
'''

import os

import rasterio
import geopandas as gpd
import numpy as np
from math import e
from enca.framework.geoprocessing import block_window_generator

class FRAGRIV(object):

    def __init__(self, runObject):
        '''
        Constructor
        '''
        config = runObject.config
        self.years = runObject.years
        self.lc = config["infra"]["leac_result"]
        self.hybas = config["infra"]["nlep"]["catchments"]
        self.dams = config["infra"]["nrep"]["dams"]
        self.fragriv = runObject.fragriv
        self.fragriv_hybas = runObject.fragriv_hybas
        self.accord = runObject.accord
        self.block_shape = (2048,2048)

    def count_dams_perHybas(self, level, ID_FIELD='HYBAS_ID'):
        '''
        Count the number of dams per hybas level and calculate fragriv index
        '''
        #outfile = self.fragriv_hybas[level]

        polygons = gpd.GeoDataFrame.from_file(self.hybas[level])
        points = gpd.GeoDataFrame.from_file(self.dams)

        # make a copy to drop points when assigning to polys, to speed up subsequent search
        pts = points.copy()

        #initialize list
        pts_in_polys = []
        fragriv_in_polys = []



        #loop over hybas
        for i, poly in polygons.iterrows():

            pts_in_this_poly = []

            #loop over points
            for j, pt in pts.iterrows():
                if poly.geometry.contains(pt.geometry):
                    #we have a hit
                    pts_in_this_poly.append(pt.geometry)
                    pts = pts.drop([j])

            #calculate river fragmentation index = 1/ln(p + e), with p number of dams
            idx_fragriv = 1/np.log( len(pts_in_this_poly) + e)

            #append the number of points found and river fragmentation index
            pts_in_polys.append(len(pts_in_this_poly))
            fragriv_in_polys.append(idx_fragriv)


        #add number of points for each poly to the dataframe
        polygons['dams'] = gpd.GeoSeries(pts_in_polys)
        polygons['fragriv'] = gpd.GeoSeries(fragriv_in_polys)

        #polygons.to_file(outfile, drivers='ESRI Shapefile')

        #rasterize
        self.accord.rasterize(polygons, 'fragriv', self.fragriv_hybas[level])



    def join(self):
        '''
        Merge the different fragriv hybas rasters into one single fragriv indicator [1-0]
        :param rasters: list of raster files for each hybas fragriv
        :param path_out: final fragriv indicator
        '''
        new_profile = self.accord.ref_profile.copy()
        new_profile.update(driver='GTiff', dtype=np.float32, nodata=-1, compress='lzw')

        f_ins = [rasterio.open(self.fragriv_hybas[key]) for key in self.fragriv_hybas.keys()]
        try:
            with rasterio.open(self.fragriv, 'w', **new_profile) as f_out:
                for _, window in block_window_generator(self.block_shape, f_out.height, f_out.width):
                    aData = []
                    for f_in in f_ins:
                        aData.append(f_in.read(1, window= window))

                    aOut = np.sum(np.stack(aData, axis=0), axis= 0) / len(f_ins)
                    f_out.write(aOut.astype(rasterio.float32), 1,window=window)
        except:
            #remove wrong file
            os.unlink(self.fragriv)

        [f_in.close() for f_in in f_ins]
