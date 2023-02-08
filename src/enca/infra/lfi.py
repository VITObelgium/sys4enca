'''
This class calculates the landscape fragmentation index (LFI). It consists of combining a catchment (river basins) with
a trunk_roads and railways layer.

inputs:
* water basin catchments (at least level 3)
* OpenStreetMap (cleaned) trunk_roads and railways, either as separate line vectors or already combined line vector

Created on Oct 28, 2019

@author: smetsb
'''
import os, sys
import subprocess
import pathlib
import shutil
import traceback
import rasterio
import numpy as np
from helper_functions import block_window_generator, rasterize, adding_stats, add_area

import general.process as process

os.environ['GDAL_DATA'] = r'/usr/share/gdal'
import geopandas as gpd

class Catchment(object):
    '''
    classdocs
    '''

    def __init__(self, params, options):
        '''
        Constructor
        '''
        self.nlep = params.nlep.nlepOut
        self.options = options
        self.scale2ha = float(params.process.scale2ha)
        self.pix2ha = float(params.process.pix2ha)
    
    def addArea(self,shapefile):
        
        #check if all required columns are available
        
        #copy input to process
        try:
            for filename in pathlib.Path(os.path.split(shapefile)[0]).glob(os.path.splitext(os.path.split(shapefile)[1])[0]+'*'):
                shutil.copy(str(filename), self.nlep.root_nlep_temp)     #PosixPath transfered in string
                if os.path.splitext(str(filename))[1] == '.shp':
                    self.catchment = os.path.join(self.nlep.root_nlep_temp, os.path.basename(str(filename)))
        except:
            print("Not able to copy catchment shape " + shapefile)
            traceback.print_stack()
            raise
            
        outfile = os.path.join(self.nlep.root_nlep_temp, os.path.basename(shapefile))

        #Add area + scaling to polygons
        add_area(outfile, outfile, self.scale2ha)

        
        #clean-up the shapefile
        try:
            data = gpd.read_file(outfile)
            #clean up the shapefile
            cols_to_drop = ["PERIMETER","VALUE"]
            for colname in cols_to_drop:
                if colname in data.columns:
                    data = data.drop([colname], axis=1)
            cols_to_rename = [{"LEVEL3":"WSO3_ID"},{"LEVEL4":"WSO4_ID"},{"AREA":"WS_AREA_HA"}]
            for col in cols_to_rename:
                if list(col.keys())[0] in data.columns:
                    data = data.rename(index=str, columns={list(col.keys())[0]:list(col.values())[0]})
            #write out new cleaned MEFF shapefile
            data.to_file(outfile, drivers='ESRI Shapefile')
        except:
            print("Error cleaning up shapefile %s through geopandas " % outfile)
            sys.exit(-1)
        
        return
        
class OSM(object):
    '''
    classdocs
    '''

    def __init__(self, params, options):
        '''
        Constructor
        '''
        self.nlep = params.nlep.nlepOut
        self.options = options
        self.years = params.run.yearsL
        self.lc = params.leac.leacOut.__dict__['lc'+str(self.years[0])]
        self.grid = process.Grid(self.lc)
        self.grid_ref = self.lc
        
    def merge_road_railways(self):
        
        #TODO ADD OPTION TO MERGE ROAD AND RAILWAYS IF DIFFERENT INPUTS PROVIDED
        outfile = 'saga_test/temporary/gis_osm_merged_trunk_roads_railways.shp'

        #Need to add check if both layers have same projection
        roads = gpd.read_file('tutorial/LEAC_Training-ENI_SEIS_II_East_2019/LEAC_Training-ENI_SEIS_II_East_2019/INPUT_DATA/Roads_Railways/gis_osm_motorways_trunk_roads_GEO_EPSG3035.shp')
        railways = gpd.read_file('tutorial/LEAC_Training-ENI_SEIS_II_East_2019/LEAC_Training-ENI_SEIS_II_East_2019/INPUT_DATA/Roads_Railways/gis_osm_railways_GEO_EPSG3035.shp')
        roads_railways = gpd.pd.concat([roads,railways])
        roads_railways.to_file(outfile)

        return outfile
    
    def inverse_RR(self, merged_trunkroads_railways):

        #outfile = os.path.join(self.nlep.root_nlep_temp, os.path.splitext(os.path.basename(merged_trunkroads_railways))[0]) + '.tif'
        outfile_inverse = os.path.join(self.nlep.root_nlep_temp, os.path.splitext(os.path.basename(merged_trunkroads_railways))[0]+'_inversed') + '.tif'


        #TO checked if inversed
        rasterize(self.grid_ref,merged_trunkroads_railways,None,outfile_inverse, nodata_value=0, dtype='uByte', burn=True, burn_value = 0)

        self.merged_roadrails_raster = outfile_inverse
        return outfile_inverse
    
    def vectorize_RR(self, merged_RR_inversed):
        
        outfile = os.path.join(self.nlep.root_nlep_temp,os.path.splitext(os.path.basename(merged_RR_inversed))[0]) + '.shp'

        #print('MAKE SURE ENOUGH MEMORY is available (i.e. 8GB for C5 region)')

        # setup cmd command
        #command slightly different won't no if output in single multi polygon or multiple polygon
        cmd = 'gdal_polygonize.py -8 "{}" "{}" vectorized ID'.format(os.path.normpath(merged_RR_inversed), outfile)

        if self.options.verbose: print("Running command %s" % cmd )
        if not os.path.exists(outfile):
            try:
                subprocess.check_call(cmd, shell=True)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not polygonize needed raster file: {e}')
                traceback.print_stack()
                sys.exit(-1)
        else:
            pass

        
        self.merged_roadrails = outfile
        return
        
class LFI(object):
    '''
    classdocs
    '''


    def __init__(self, params, options):
        '''
        Constructor
        '''
        self.nlep = params.nlep.nlepOut
        self.AOI = params.run.region_short
        self.projection = params.run.projection
        self.options = options
        self.block_shape = (4096, 4096)
        self.years = params.run.yearsL
        self.scale2ha = float(params.process.scale2ha)
        self.pix2ha = float(params.process.pix2ha)
        self.meshOutlier_ha = int(10*100)   #10km2
        self.lc = params.leac.leacOut.__dict__['lc'+str(self.years[0])]
        self.grid = process.Grid(self.lc)
    
    def intersect_Catchment_OSM(self,catchment,catch_level,merged_roadrails):
    
        outfile = os.path.join(self.nlep.root_nlep_temp,'MESH_intersect_'+str(self.AOI)+'_'+str(catch_level)+'_'+str(self.projection)+'.shp')

        gdf_merger_RR = gpd.read_file(merged_roadrails)
        gdf_catchment = gpd.read_file(catchment)
        gdf = gdf_merger_RR.overlay(gdf_catchment, how='intersection').explode()
        gdf.to_file(outfile)

    
        #add feature properties of the mesh intersect
        add_area(outfile, outfile, self.scale2ha)
        
        #clean-up the shapefile
        try:
            data = gpd.read_file(outfile)
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
            #write out new cleaned MEFF shapefile
            data.to_file(outfile, drivers='ESRI Shapefile')
        except:
            print("Error cleaning up shapefile %s through geopandas " % outfile)
            sys.exit(-1)
        
        self.lfi_mesh = outfile
        return outfile
    
    def intersect_LCclass(self,landcover,lcClass=0,lcName='None'):
        
        #filename first 10 chars will be used later in mesh
        outfile = os.path.join(self.nlep.root_nlep_temp,os.path.splitext(lcName+'_'+os.path.basename(landcover))[0]) + '.tif'
        #'saga_test/Land_cover_ProbaV_PS-CLC_GEO_2000_100m_EPSG3035_urban'

        with rasterio.open(landcover, 'r') as ds_open:
            profile = ds_open.profile
            with rasterio.open(outfile, 'w', **dict(profile, driver='GTiff', nodata =255, dtype=np.ubyte)) as ds_out:
                for _, window in block_window_generator(self.block_shape, ds_open.height, ds_open.width):
                    ablock = ds_open.read(1, window=window, masked=True)

                    ds_out.write(ablock != int(lcClass), window=window, indexes=1)
        
        return outfile
    
    def calc_mesh(self,lcmask,lfi_mesh):
        adding_stats([lcmask],lfi_mesh,lfi_mesh, [np.sum])

    def calc_meff(self, lfi_mesh, catchment, year, lcName, frag_field):

        if not 'clean' in lfi_mesh:  #we are using temp since we need to combine the 3 fragmeff levels to final output
            outfile = os.path.join(self.nlep.root_nlep,'temp',os.path.splitext(os.path.basename(lfi_mesh))[0]+'_clean'+'.shp')
        else:
            outfile = lfi_mesh
        if not 'clean' in catchment:
            outfile_catchment = os.path.join(self.nlep.root_nlep,'temp',os.path.splitext(os.path.basename(catchment))[0]+'_clean'+'.shp')
        else:
            outfile_catchment = catchment

        lcName = lcName  # + '_' SAGA seems to add an underscore during calc_mesh not so with Rasterio version
        
        #now calculate the mesh statistics and write in catchment
        try:
            data = gpd.read_file(lfi_mesh)
            data_catchment = gpd.read_file(catchment)
            #determine catchment level
            ws_level = None
            for col in data.columns:
                if (col.startswith('WS') or col.startswith('HY')) and col.endswith('_ID'):
                    ws = col
            if ws is None:
                print('No watershed level found')
                sys.exit(-1)
            else:
                #check if catchment level also in catchment layer
                if not ws in data_catchment.columns:
                    print('Inconsistent watershed level between catchment and mesh vectors')
                    sys.exit(-1)

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
            self.lfi_mesh = outfile
            self.lfi_frag_meff = outfile_catchment
        except:
            print("GEOPANDAS MESH calculation failed")
            traceback.print_stack()
            sys.exit(-1)
        
        return
    
    def rasterize_MEFF(self, meff_shape, field):

        outfile = os.path.join(self.nlep.root_nlep_temp,os.path.splitext(os.path.basename(meff_shape))[0]+'_'+str(self.projection)+'_'+str(field)) + '.tif'

        rasterize(self.lc, meff_shape, field, outfile , dtype = 'Float32')
        # s.path.splitext(self.lc)[0]+'.sgrd'

        return outfile

        
        
        
    