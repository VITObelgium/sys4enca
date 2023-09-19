'''
This class calculates the landscape fragmentation index (LFI). It consists of combining a catchment (river basins) with
a trunk_roads and railways layer.

inputs:
* water basin catchments (at least level 3)
* OpenStreetMap (cleaned) trunk_roads and railways, either as separate line vectors or already combined line vector

Created on Oct 28, 2019

@author: smetsb
'''
import os
import rasterio
import logging
import numpy as np
from enca.framework.geoprocessing import block_window_generator, adding_stats
from enca.framework.errors import Error

import geopandas as gpd
logger = logging.getLogger(__name__)

class Catchment(object):
    '''
    classdocs
    '''

    def __init__(self, runObject, basin):
        '''
        Constructor
        '''

        self.accord = runObject.accord
        self.years = runObject.years
        _ , scale = self.accord.ref_profile['crs'].linear_units_factor
        self.scale2ha = scale ** 2 / 10000
        self.catchment = runObject.config["infra"]["catchments"][basin]
        self.catchment_temp = runObject.catchments_temp[basin]
        self.reporting_profile_epsg = runObject.reporting_shape.crs.to_epsg()
        self.basin = basin
    
    def addArea(self):
        
        #check if all required columns are available
        try:
            data = gpd.read_file(self.catchment).sort_index()
            #write out new cleaned MEFF shapefile in reporting projection
            data = data.to_crs(epsg=int(self.reporting_profile_epsg))
            #Add area + scaling to polygons
            data['AREA'] = data['geometry'].area * self.scale2ha
            #clean up the shapefile
            cols_to_drop = ["PERIMETER","VALUE"]
            for colname in cols_to_drop:
                if colname in data.columns:
                    data = data.drop([colname], axis=1)
            cols_to_rename = [{"LEVEL3":"WSO3_ID"},{"LEVEL4":"WSO4_ID"},{"AREA":"WS_AREA_HA"}]
            for col in cols_to_rename:
                if list(col.keys())[0] in data.columns:
                    data = data.rename(index=str, columns={list(col.keys())[0]:list(col.values())[0]})
            data.to_file(self.catchment_temp, drivers='ESRI Shapefile')
        except Error as e:
            raise Error(e)
        

        
class OSM(object):
    '''
    classdocs
    '''

    def __init__(self, runObject):
        '''
        Constructor
        '''

        self.years = runObject.years
        self.temp_dir = runObject.temp_dir()
        self.accord = runObject.accord
        self.merged_trunkroads_railways = runObject.config["infra"]['osm']
        self.merged_trunkroads_railways_inv = runObject.merged_trunkroads_railways_inv
        self.merged_RR_inversed = runObject.merged_RR_inversed
        self.reporting_profile_epsg = runObject.reporting_shape.crs.to_epsg()

        
    def merge_road_railways(self):
        
        #TODO ADD OPTION TO MERGE ROAD AND RAILWAYS IF DIFFERENT INPUTS PROVIDED NOT YET IMPLEMENTED (Move to preprocessing
        outfile = 'saga_test/temporary/gis_osm_merged_trunk_roads_railways.shp'

        #Need to add check if both layers have same projection
        roads = gpd.read_file('tutorial/LEAC_Training-ENI_SEIS_II_East_2019/LEAC_Training-ENI_SEIS_II_East_2019/INPUT_DATA/Roads_Railways/gis_osm_motorways_trunk_roads_GEO_EPSG3035.shp')
        railways = gpd.read_file('tutorial/LEAC_Training-ENI_SEIS_II_East_2019/LEAC_Training-ENI_SEIS_II_East_2019/INPUT_DATA/Roads_Railways/gis_osm_railways_GEO_EPSG3035.shp')
        roads_railways = gpd.pd.concat([roads,railways])
        roads_railways.to_file(outfile)

        return outfile
    
    def inverse_RR(self):
        #gdal_rasterize does not warp (a_srs option is only for correcting invalid SRS), so first warp if needed
        try:
            df = gpd.read_file(self.merged_trunkroads_railways, rows=1)
            if df.crs.to_epsg() != self.reporting_profile_epsg:
                self.merged_trunkroads_railways_warped = os.path.join(self.temp_dir, os.path.splitext(os.path.basename(self.merged_trunkroads_railways))[0]+'_'+str(self.reporting_profile_epsg)+'.shp')
                self.merged_trunkroads_railways_inv = os.path.join(self.temp_dir, os.path.splitext(os.path.basename(self.merged_trunkroads_railways))[0]+'_'+str(self.reporting_profile_epsg)+'_inv.tif')
                self.merged_RR_inversed = os.path.join(self.temp_dir, 'vector_'+os.path.splitext(os.path.basename(self.merged_trunkroads_railways))[0]+'_'+str(self.reporting_profile_epsg)+'_inv.shp')
                self.accord.vector_2_AOI(self.merged_trunkroads_railways,self.merged_trunkroads_railways_warped,mode='reporting')
            else:
                self.merged_trunkroads_railways_warped = self.merged_trunkroads_railways
        except:
            raise RuntimeError('Failed to read {}.'.format(self.merged_trunkroads_railways))

        self.accord.rasterize_burn(self.merged_trunkroads_railways_warped,self.merged_trunkroads_railways_inv, nodata_value=1,
                                   burn_value=0, dtype='Byte')
    
    def vectorize_RR(self):
        self.accord.vectorize(self.merged_trunkroads_railways_inv, self.temp_dir)
        
class LFI(object):
    '''
    classdocs
    '''


    def __init__(self, runObject):
        '''
        Constructor
        '''
        self.runObject = runObject
        self.AOI = runObject.aoi_name
        self.accord = runObject.accord
        self.block_shape = (4096, 4096)
        self.years = runObject.years
        _ , scale = self.accord.ref_profile['crs'].linear_units_factor
        self.scale2ha = scale ** 2 / 10000
        self.pix2ha = self.accord.pixel_area_m2() / 10000 #m2 to hectares
        self.lcclass = runObject.config["infra"]["general"]["lc_urban"]
        self.lcname='NoUrb'
        self.basins = sorted([basin for basin in runObject.config["infra"]["catchments"].keys()])
        self.meshOutlier_ha = int(10*100)   #10km2
        self.catchments = runObject.config["infra"]["catchments"]
        self.catchments_processed = runObject.catchments_processed
        self.catchments_processed_aoi = runObject.catchments_processed_aoi
        self.catchments_clean = runObject.catchments_clean
        self.lfi_mesh = runObject.lfi_mesh
        self.lfi_mesh_clean = runObject.lfi_mesh_clean
        self.lfi_meff_hybas = runObject.lfi_meff_hybas
        self.lfi_meff = runObject.lfi_meff
        self.mask = {}
        if runObject.tier == 4:
            self.landcover_map = {}
            # if lcclass (i.e. Urban) still is comprised from several classes, then first reclassify into one single urban class
            # TODO temporary patch to cope with multiple Urban LandCover classes
            # currently manual in QGIS with raster calculator (ZAEG_reclassified > 0 and ZAEG_reclassified < 12)*10
            for year in self.years:
                self.landcover[year] = '/data/nca_vol1/aux_input/preprocessed_data/NLEP/PNMB/PNMB_Urban_'+str(year)+'_3857.sdat'
        else:
            self.landcover = runObject.config["infra"]["leac_result"]
    
    def intersect_Catchment_OSM(self,basin,merged_roadrails):

        gdf_merger_RR = gpd.read_file(merged_roadrails)
        self.accord.vector_2_AOI(self.catchments_processed[basin],self.catchments_processed_aoi[basin])

        gdf_catchment = gpd.read_file(self.catchments_processed_aoi[basin])
        #data = gdf_merger_RR.overlay(gdf_catchment, how='intersection').explode(index_parts=True)
        data = gdf_catchment.overlay(gdf_merger_RR, how='symmetric_difference').explode(index_parts=True)

        #clean-up
        data['AREA'] = data['geometry'].area * self.scale2ha
        #clean up the shapefile
        #complains about level_0
        cols_to_drop = ["PERIMETER","VALUE","level_0"]
        for colname in cols_to_drop:
            if colname in data.columns:
                data = data.drop([colname], axis=1)
        cols_to_rename = [{"LEVEL3":"WSO3_ID"},{"LEVEL4":"WSO4_ID"},{"AREA":"MESH_HA"}]
        for col in cols_to_rename:
            if list(col.keys())[0] in data.columns:
                data = data.rename(index=str, columns={list(col.keys())[0]:list(col.values())[0]})

        #write out MESH shapefile
        data.to_file(self.lfi_mesh[basin], drivers='ESRI Shapefile')

    def intersect_LCclass(self, year, lcName='None'):
        
        #filename first 10 chars will be used later in mesh
        self.mask[year] = os.path.join(self.runObject.temp_dir(),os.path.splitext(lcName+'_'+os.path.basename(self.landcover[year]))[0]) + '.tif'
        #'saga_test/Land_cover_ProbaV_PS-CLC_GEO_2000_100m_EPSG3035_urban'

        with rasterio.open(self.landcover[year], 'r') as ds_open:
            profile = ds_open.profile
            with rasterio.open(self.mask[year], 'w', **dict(profile, driver='GTiff', nodata =255, dtype=np.ubyte)) as ds_out:
                for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                    ablock = ds_open.read(1, window=window, masked=True)

                    ds_out.write(ablock != int(self.lcclass), window=window, indexes=1)
        
        return self.mask[year]
    
    def calc_mesh(self, year ,basin):
        if not os.path.exists(self.lfi_mesh_clean[basin]):
            adding_stats([self.mask[year]],self.lfi_mesh[basin] ,self.lfi_mesh[basin] , [np.sum])
        else:
            adding_stats([self.mask[year]],self.lfi_mesh_clean[basin] ,self.lfi_mesh_clean[basin] , [np.sum])

    def calc_meff(self, year, basin, lcName):
        frag_field = self.get_field(year)
        outfile = self.lfi_mesh_clean[basin]
        outfile_catchment = self.catchments_clean[basin]
        if os.path.exists(outfile):  #we are using temp since we need to combine the 3 fragmeff levels to final output
            infile = outfile
        else:
            infile = self.lfi_mesh[basin]
        if os.path.exists(outfile_catchment):
            infile_catchment = outfile_catchment
        else:
            infile_catchment = self.catchment_processed[basin]

        #now calculate the mesh statistics and write in catchment
        try:
            data = gpd.read_file(infile)
            data_catchment = gpd.read_file(infile_catchment)
            #determine catchment level
            ws = None
            for col in data.columns:
                if (col.startswith('WS') or col.startswith('HY')) and col.endswith('_ID'):
                    ws = col
            if ws is None:
                raise Error('No watershed level found')
            else:
                #check if catchment level also in catchment layer
                if not ws in data_catchment.columns:
                    raise Error('Inconsistent watershed level between catchment and mesh vectors')

            '''#SAGA uses first letters in intersect_LFI, so rename
            cols_to_rename = [{lcName.split('_')[0]+"_NCA": lcName}]
            for col in cols_to_rename:
                if list(col.keys())[0] in data.columns:
                    data = data.rename(index=str, columns={list(col.keys())[0]: list(col.values())[0]})
            '''

            #remove outlier polygons - delete meshes smaller than threshold
            indexNames = data[data['MESH_HA']<self.meshOutlier_ha].index
            data.drop(indexNames, inplace=True)
            
            #calculate Mesh_area = Intersect_area - Urban_area
            #data['MESH_AREA_HA'] = data['MESH_AREA']*self.pix2ha*self.pix2ha  #already in ha (used scale factor 0.01)
            MESH_year1 = 'Mesh'+str(year)
            #MESH_year2 = 'Mesh_'+str(self.year2)+'_HA'
            data[MESH_year1] = data[lcName]*self.pix2ha
            #data[MESH_year2] = data[lcName2]*self.pix2ha
            
            #calculate MEFF = (SUM (Mesh_area)²) /riverbasin_area
            #use intermediate result (sqr)
            MESH_year1_N2 = 'Mesh'+str(year)+'N2'
            #MESH_year2_N2 = 'Mesh_'+str(self.year2)+'_N2_HA'
            data[MESH_year1_N2] = data[MESH_year1]*data[MESH_year1]
            #data[MESH_year2_N2] = data[MESH_year2]*data[MESH_year2]
            #
            '''
            a = data.groupby(ws)[MESH_year1_N2].sum()
            b = data.groupby(ws)[MESH_year2_N2].sum()
            af = a.to_frame().reset_index()
            bf = b.to_frame().reset_index()
            '''
            #TODO WS_AREA_HA column is calculated through SAGA, but seems not to be as accurate as SUB_AREA but in km2 and result in fragm > 1
            #data_catchment['WS_AREA_N2_HA'] = (data_catchment['SUB_AREA']/self.scale2ha)*(data_catchment['SUB_AREA']/self.scale2ha)
            data_catchment['WS_AREA_N2'] = data_catchment['WS_AREA_HA'] * data_catchment['WS_AREA_HA']
            MESH_count = 'MESH_'+'_count'
            data_catchment = data_catchment.merge(data.groupby(ws)[MESH_year1_N2].sum().to_frame().reset_index(), on=ws)
            data_catchment = data_catchment.merge(data.groupby(ws)[MESH_year1].count().to_frame().reset_index(), on=ws)
            data_catchment = data_catchment.rename(columns={MESH_year1:MESH_count})
            #data_catchment = data_catchment.merge(data.groupby(ws)[MESH_year2_N2].sum().to_frame().reset_index(), on=ws)
            #data_catchment = data_catchment.merge(data.groupby(ws)[MESH_year2].count().to_frame().reset_index(), on=ws)
            #data_catchment = data_catchment.rename(columns={MESH_year2:MESH_count})
            
            '''
            MEFF_year1 = 'MEFF_'+str(self.year1)
            MEFF_year2 = 'MEFF_'+str(self.year2)
            data_catchment[MEFF_year1]= data_catchment[MESH_year1_N2]/data_catchment['WS_AREA_HA']
            data_catchment[MEFF_year2]= data_catchment[MESH_year2_N2]/data_catchment['WS_AREA_HA']
            '''
            
            #calculate FRAF_MEFF = MEFF / riverbasin_area
            FRAG_MEFF_year1 = frag_field
            #FRAG_MEFF_year2 = 'FRAGM_'+str(self.year2)
            #we don't need to divide by number of meshes in sub-basin, as we have not summed up the WS_AREA per mesh
            data_catchment[FRAG_MEFF_year1] = data_catchment[MESH_year1_N2]/(data_catchment['WS_AREA_N2']) #/data_catchment[MESH_year1_count])
            data_catchment[FRAG_MEFF_year1] = data_catchment[FRAG_MEFF_year1].clip(0,1)
            #data_catchment[FRAG_MEFF_year2] = data_catchment[MESH_year2_N2]/(data_catchment['WS_AREA_N2_HA']) #/data_catchment[MESH_year2_count])
            #data_catchment[FRAG_MEFF_year2] = data_catchment[FRAG_MEFF_year2].clip(0,1)
            
            #normalize to 0 (bad) -> 100 (good) to be compliant with WDPA and GBLI indexes ?????
            
            #clean-up by removing uninteresting columns
            cols_to_drop = ["NEXT_DOWN","NEXT_SINK","DIST_SINK","DIST_MAIN","UP_AREA","gridcode","SIDE","LAKE","ENDO","COAST","ORDER_",\
                            "SORT","Id","gridcode",\
                            "WS_AREA_N2",MESH_count,MESH_year1_N2]
            for colname in cols_to_drop:
                if colname in data_catchment.columns:
                    data_catchment = data_catchment.drop([colname], axis=1)

            #TODO clean also data (lfi_mesh) file

            #write out new cleaned MEFF shapefile
            data.to_file(outfile, drivers='ESRI Shapefile')
            data_catchment.to_file(outfile_catchment, drivers='ESRI Shapefile')
        except Error as e:
            logger.ERROR("GEOPANDAS MESH calculation failed")
            raise Error(e)
    
    def rasterize_MEFF(self, year, basin):
        field = self.get_field(year)
        gdf = gpd.read_file(self.catchments_clean[basin])
        self.accord.rasterize(gdf, field , self.lfi_meff_hybas[basin][year] , dtype = 'Float32')
        # s.path.splitext(self.lc)[0]+'.sgrd'

    def get_field(self, year):
        return 'FRAG'+str(year)[2:]+'_'+str(self.lcclass)
