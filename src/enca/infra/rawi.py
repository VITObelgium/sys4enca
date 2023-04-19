'''
RAWI class

Created on Oct 26, 2020

@author: smetsb
'''
import os, sys
import math
from math import sqrt
import geopandas as gpd
import numpy as np
import rasterio
from enca.framework.geoprocessing import adding_stats, block_window_generator

class RAWI(object):
    '''
    classdocs
    '''

    def __init__(self, runObject):
        '''
        Constructor
        '''
        config = runObject.config
        self.riverSRMU = runObject.riverSRMU
        self.rawi_shape = runObject.rawi_shape
        self.rawi_selu = runObject.rawi_selu
        self.rawi_mask = runObject.rawi_mask
        self.rawi =runObject.rawi
        self.accord = runObject.accord
        _ , scale = self.accord.ref_profile['crs'].linear_units_factor
        self.scale2ha = scale ** 2 / 10000
        self.pix2ha = self.accord.pixel_area_m2() / 10000 #m2 to hectares
        self.resolution = sqrt(self.accord.pixel_area_m2())
        self.river_buffer = runObject.river_buffer  #gloric represents 100m TODO, what if 10m landcover
        self.level = runObject.tier
        self.lc = config["infra"]["leac_result"]
        self.dams = config["infra"]["nrep"]["dams"]
        self.gloric = config["infra"]["nrep"]["gloric"]
        self.shapefile_catchment= config["infra"]["nlep"]["catchments"]['catchment_12']
        self.lc_water = config["infra"]["general"]["lc_water"]
        self.years = runObject.years
        self.block_shape = (2048,2048)

    def assign_RELU(self):

        #overwriting input
        outfile = self.rawi_shape

        #TODO move to lut table
        river_class_name = 'HSRU'
        #1- flows < 1m3/second
        #2- small rivers >=1 and < 5
        #3- medium rivers >=5 and < 10
        #4- large rivers >=10 and < 100
        #5- very large river >=100

        try:
            data = gpd.read_file(self.gloric)
            data[river_class_name] = 0

            #inverse the log avg discharge m3/sec
            data['Q_avg'] = 10 ** data['Log_Q_avg']

            #categorize according lut table
            data.loc[data.Q_avg < 1.0, river_class_name] = 1
            data.loc[(data.Q_avg >= 1.0) & (data.Q_avg < 5.0), river_class_name] = 2
            data.loc[(data.Q_avg >= 5.0) & (data.Q_avg < 10.0), river_class_name] = 3
            data.loc[(data.Q_avg >= 10.0) & (data.Q_avg < 100.0), river_class_name] = 4
            data.loc[(data.Q_avg >= 100.0), river_class_name] = 5

            #calculate SRMU
            data['SRMU'] = data.Q_avg * data.Length_km  #data.LENGTH_GEO take full river length for SRMU
            data['log_SRMU'] = np.log10(data['SRMU']+math.e)

            # clean up the shapefile
            cols_to_drop = ["Log_Q_var","Class_hydr","Temp_min","CMI_indx","Log_elev","Class_phys","Lake_wet", \
                            "Stream_pw","Class_geom","Reach_type","Kmeans_30", \
                            "NEXT_DOW_1","ENDO","COAST","ORDER_","SORT"]
            for colname in cols_to_drop:
                if colname in data.columns:
                    data = data.drop([colname], axis=1)
            cols_to_rename = []  #format{key:value}
            for col in cols_to_rename:
                if list(col.keys())[0] in data.columns:
                    data = data.rename(index=str, columns={list(col.keys())[0]: list(col.values())[0]})

            # write result with extra column HSRU (homegeneous stream reach units classes)
            data.to_file(outfile, drivers='ESRI Shapefile')
            return
        except:
            print("Error categorizing rivers units HSRU %s through geopandas " % outfile)
            sys.exit(-1)

    def group_SRMUperSELU(self):

        outfile = self.rawi_selu

        try:
            data = gpd.read_file(self.rawi_shape)
            data['HSRU_L'] = data.LENGTH_GEO * data.HSRU  #calculate weighted HSRU

            data_hybas = data.groupby('HYBAS_ID').sum()   #data.dissolve('HYBAS_ID',aggfunc='sum') #dissolve is slow
            #some hybas are 0 with 0 length
            data_hybas['HSRU_W'] = np.floor(data_hybas.HSRU_L / data_hybas.LENGTH_GEO)
            data_hybas['HSRU_W'][data_hybas.HSRU_L == 0]  = 0
            data_hybas['HSRU_W'] = data_hybas['HSRU_W'].astype(np.uint8)

            data_catch = gpd.read_file(self.shapefile_catchment)
            data_catch = data_catch.set_index('HYBAS_ID')

            #spatial join, extend catchments with HSRU and SRMU information
            df = data_catch.merge(data_hybas, on='HYBAS_ID')
            #df = gpd.sjoin(data_catch, data_hybas, how='left', op='contains')  #spatial join is not working properly, still get duplicated hybas rows
            df_missing = data_catch[(~data_catch.index.isin(data_hybas.index))] #some hybas have no river, so will be missing
            df = df.append(df_missing) #add again the hybas without any river
            df.reset_index(level=0, inplace=True)

            # clean up the shapefile
            cols_to_drop = [ "ENDO","COAST","ORDER_","SORT", \
                            "index_right","FID_GloRiC","Reach_ID","Next_down","Log_Q_avg","Stream_pow", \
                            "NEXT_SINK","MAIN_BAS","DIST_SINK","DIST_MAIN","SUB_AREA", "UP_AREA", "PFAF_ID", \
                             "NEXT_SINK_y", "MAIN_BAS_y", "DIST_SINK_y", "DIST_MAIN_y", "SUB_AREA_y", "UP_AREA_y", "PFAF_ID_y", \
                             "HSRU","HSRU_L"]
            for colname in cols_to_drop:
                if colname in df.columns:
                    df = df.drop([colname], axis=1)
            cols_to_rename = [{"NEXT_SINK_x":"NEXT_SINK"},{"MAIN_BAS_x":"MAIN_BAS"},{"DIST_SINK_x":"DIST_SINK"},\
                              {"DIST_MAIN_x":"DIST_MAIN"},{"SUB_AREA_x":"SUB_AREA"},{"UP_AREA_x":"UP_AREA"},\
                              {"PFAF_ID_x":"PFAF_ID"}]  # format{key:value}
            for col in cols_to_rename:
                if list(col.keys())[0] in df.columns:
                    df = df.rename(index=str, columns={list(col.keys())[0]: list(col.values())[0]})

            # write out grouped shapefile
            df.to_file(outfile, drivers='ESRI Shapefile')

        except:
            print("Error categorizing RAWI %s through geopandas " % outfile)
            sys.exit(-1)

    #rasterize Gloric rivers
    def rasterize_rivers(self):
        #TODO duplicate with fragriv - generalize
        if self.resolution >= self.river_buffer:
          file = self.rawi_shape
        else:
            file = os.path.splitext(self.rawi_shape)[0] + '_buffered'+ str(self.resolution) +'m'+ '.tif'
            gdb = gpd.read_file(self.rawi_shape)
            gdb["geometry"] = gdb.geometry.buffer(self.river_buffer)
            gdb.to_file(file, drivers='ESRI Shapefile')

        self.accord.rasterize_burn(file, self.rawi_mask)

        return

    #merge Gloric rivers with Landcover water (to count for lake sizes)
    def join(self,year,LCwater=51):
        #convert all to water

        path_out = os.path.splitext(self.rawi_mask)[0]+'_mergedLC_'+str(year)+'.tif'
        profile = self.accord.ref_profile
        profile.update(dtype=np.int16, nodata=-1)
        with rasterio.open(path_out, 'w', **profile) as dst, \
                rasterio.open(self.rawi_mask) as src_river, rasterio.open(self.lc[year]) as src_lc:
            for _, window in block_window_generator(self.block_shape, dst.height, dst.width):
                aMask = src_river.read(1, window = window)
                aLC = src_lc.read(1, window=window)


                #reclassify water
                for lcWater in self.lc_water:
                    aLC[aLC==lcWater] = LCwater

                #apply water mask
                aLC[aLC!=LCwater] = 0
                aLC[aLC==LCwater] = 1

                aOut = aMask+aLC
                aOut[aOut==2] = 1

                dst.write(aOut.astype(rasterio.int16), 1,window=window)

        return path_out

    #segment Gloric rivers mask (rasterized) and count ha rivers per hybas to generate rawi
    def calc_rawi(self,joinedMask,year):
        catchment = self.rawi_selu
        adding_stats([joinedMask], catchment, catchment, [np.sum])

        #rename count in shapefile to River Size (RS)
        try:
            df = gpd.read_file(catchment)
            col = os.path.split(catchment)[1][:10]
            cols_to_rename = [{col:'RS_'+str(year)}]  # format{key:value}
            for col in cols_to_rename:
                if list(col.keys())[0] in df.columns:
                    df = df.rename(index=str, columns={list(col.keys())[0]: list(col.values())[0]})

            #convert RS to ha (riverMask created acc. land cover resolution)
            df['RS_'+str(year)] = df['RS_'+str(year)]*self.pix2ha

            #calculate River accessibility Weight Index  TODO not sure on this calculation
            df['RAWI_'+str(year)] = df.log_SRMU * df['RS_'+str(year)]   #np.log10(df.SRMU)
            df['HYBAS_HA'] = df.geometry.area/10**4

            #reset RAWI to zero for hybas without rivers
            lstIndexNaN = df[df['RAWI_'+str(year)].isnull()].index
            for i in lstIndexNaN:
                df.at[i,'RAWI_'+str(year)] = 0.0   #TODO RAWI is expressed in area (ha) is 0 OK ?

            # write out grouped shapefile and export to CSV table
            if 'level_0' in df.columns:
                df.drop('level_0', axis = 1, inplace = True)

            df.to_file(catchment, drivers='ESRI Shapefile')

        except Exception as e:
            print("Updating shapefile to rename column to RS failed")
            print(e)
            raise

        return

    #
    def rasterize_rawi(self, shape, outfile, ID_FIELD):

        gdb = gpd.read_file(shape)
        #actually no buffer is applied in original 'reproducing this for standard -> not for difference
        if self.accord.ref_profile.get('transform')[0] >= self.river_buffer:
            pass
        else :
            gdb["geometry"] = gdb.geometry.buffer(self.river_buffer)

        self.accord.rasterize(gdb, ID_FIELD, outfile, nodata_value=0)

