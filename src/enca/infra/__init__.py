import logging
import os
import numpy as np
import geopandas as gpd
import pandas as pd


import enca
from enca.infra.nlep import create_NLEP
from enca.infra.nrep import create_NREP
from enca.framework.config_check import ConfigItem, ConfigRaster, ConfigShape, YEARLY
from enca.framework.geoprocessing import statistics_byArea, norm_1


logger = logging.getLogger(__name__)
RIVER_BUFFER = 100
INDICES = [f'l{idx}' for idx in range(1,13)]

class Infra(enca.ENCARun):

    run_type = enca.RunType.ENCA
    component = 'infra'
    #id_col_reporting = "GID_0"

    def __init__(self, config):
        super().__init__(config)

        self.config_template.update({
            self.component: {
                "paths_indices" :
                    {layer : ConfigRaster(optional= True) for layer in INDICES},
                "general" : {
                    "gaussian_kernel_radius": ConfigItem(),
                    "gaussian_sigma": ConfigItem(),
                    "lc_urban" : ConfigItem(),
                    "lc_water" : ConfigItem(default = [51])
                },
                'nlep' : {
                    "lut_gbli" : ConfigItem(),
                    "naturalis": ConfigRaster(), #should be changed to shape however can't seem to find original file
                    "catchments" : {'catchment_6' : ConfigShape(),
                                    'catchment_8' : ConfigShape(),
                                    'catchment_12' : ConfigShape()},
                    "osm" : ConfigShape()
                },
                'nrep' : {
                    "dams" : ConfigShape(),
                    "gloric" : ConfigShape()

                },
                'leac_result' : {YEARLY : ConfigRaster(optional = True)},
            }})
        self.check_leac()
        self.make_output_filenames()

    def _start(self):

        logger.debug('Hello from ENCA Infra')

        # region = self.aoi_name
        # tier = self.tier
        # path_out = self.run_dir
        # rc = self.run_name
        # path_temp = self.temp_dir()

        #first create NLEP
        create_NLEP(self)

        #second create NREP
        create_NREP(self)

        # extract statistics per SELU
        logger.info('* Calculate Acessible Ecosystem Infrastructure per SELU')
        #40s per year
        for year in self.years:
            logger.info('** processing year {} ...'.format(year))
            self.extract_stats(year)
        logger.info('* SELU statistics ready')

        #return

        # create INFRA account table per SELU
        logger.info('* Calculate Overall access & intensity of use and health')
        #40s
        for year in self.years:
            logger.info('** processing year {} ...'.format(year))
            self.calc_indices(year, ID_FIELD = 'HYBAS_ID', vrt_nodata=-9999)

        logger.info('* Indices available')

        # group INFRA account per reporting area -> done in TEC
        logger.debug('* Create INFRA account table')
        for year in self.years:
            #5s
            logger.info('** processing year {} ...'.format(year))
            self.create_account_table(year)
        logger.info('* INFRA account created')

    ######################################################################################################################
    def calc_indices(self, year, ID_FIELD = 'HYBAS_ID', vrt_nodata=-9999):
        # region = self.aoi_name
        path_SELU = self.path_results_eip[year]
        path_BASELINE = self.path_results_eip[self.years[0]]
        # 1. build datacube raster from all input
        # path_temp = self.temp_dir()

        # lPaths = []
        lColumns = []

        #remove keys with none or empty value
        self.config["infra"]["paths_indices"] = {k: v for k, v in self.config["infra"]["paths_indices"].items() if v}

        #TODO move to yaml incl key to indicate layer for indexing
        #note indexing the layer starts from 0
        # Layer-1 = Dummy
        # Layer-2 = Burnt Area (ad_8)
        # Layer-3 = Ecosystem Vulnerability (ad_6)
        # Layer-4 = Species Extinction Index (ad_7)
        # Layer-5 = Mean Species Abundance (ad_5)
        # Layer-6 = Biodiversity Intactness Index (ad_4)
        # Layer-7 = Fire Vulnerability (ad_9)
        # Layer-8 = Mine Pollution Risk (ad_10)
        # Layer-9 = Population statsitcs (ad_3)
        paths= [path for path in self.config["infra"]["paths_indices"].values()]
        keys = [keys for keys in self.config["infra"]["paths_indices"].keys()]
        rename_dict= {'l1':'dummy','l2':'ad_8','l3':'ad_6',
                      'l4':'ad_7','l5':'ad_5','l6':'ad_4',
                      'l7':'ad_9','l8':'ad_10','l9':'ad_3',
                      'l10':'ad_11','l11':'ad_12'}
        function = {'l1':None, 'l2':None, 'l3':norm_1,
                    'l4':norm_1, 'l5':None, 'l6':None,
                    'l7':None,'l8':None,'l9':None,
                    'l10':None,'l11':None}
        lColumns= [rename_dict.get(key) for key in keys]


        df = pd.DataFrame(index=self.statistics_shape.index.astype(str) ,  dtype=float)
        df.index.name = ID_FIELD
        # m2_2ha = 1/100**2
        pix2ha = self.accord.pixel_area_m2()/100**2

        for idx,path in enumerate(paths):
            stats = statistics_byArea(path, self.statistics_raster,
                                      {row[0] : row[1]['SHAPE_ID'] for row in self.statistics_shape.iterrows()}
                                      , transform=function[keys[idx]])
            if keys[idx] == 'l5':
                stats["sum"] = stats["sum"] *1.5
            elif keys[idx] == 'l11':
                stats["sum"] = stats["sum"] *10
            if keys[idx] in ['l2','l9']:
                df[lColumns[idx]] = stats["sum"]*pix2ha
            else:
                df[lColumns[idx]]= stats["sum"]/stats['px_count']

        #join with base table
        df2 = gpd.read_file(path_SELU)
        df = df2.merge(df, on=ID_FIELD)
        df2 = None

        df_baseline = gpd.read_file(path_BASELINE)
        cols_to_drop = [x for x in df_baseline.columns if x not in [ID_FIELD,'EIP4']]
        df_baseline.rename(columns = {'EIP4':'EIP6'}, inplace=True)
        df = df.merge(df_baseline.drop(cols_to_drop, axis=1), on=ID_FIELD)
        df_baseline = None

        #accessibility indicators
        df['EB1_1LC'] = df['Area_rast']
        if 'ad_3' in lColumns:
            df['EIP5_1'] = df['EIP4'] * df['ad_3']/df['Area_rast']
            df['EIP5_2'] = df['EIP3'] * df['ad_3']/df['Area_rast']

        #sustainability indicator
        df['EISUI'] = df['EIP4'] / df['EIP6']


        # EHI6_ready = False
        # EHI7_ready = False
        # EHI8_ready = False
        #health indicators
        if 'ad_4' in lColumns:
            df['ad_4'] = np.clip(df['ad_4'], a_max=1.0, a_min=0.7)         #Clamp biodiversity intactness index to 1.0
        if 'ad_5' in lColumns:
            df['ad_5'] = np.clip(df['ad_5'], a_max=1.0, a_min=0.7)         #Clamp MSA to 0.7 at lower boundaries
        if 'ad_6' in lColumns:
            df['ad_6'] = np.clip(1. - df['ad_6'], a_max=None, a_min = 0.7)  #Reverse vulnerability to health and clamp low value
            #note ad_6 (ecosystem vulnerability map) is too coarse and not used to calculate biodiversity health
        if 'ad_7' in lColumns:
            df['ad_7'] = np.clip(df['ad_7'], a_max=None, a_min = 0.7)       #Clamp EDGE score to 0.7 at low range

        if 'ad_12' in lColumns:
            df['ad_12'] = np.clip(0.8 + df['ad_12']/10., a_max=1.0, a_min=None )   #Clamp Fauna density index between 0.9 and 1.0
            if 'ad_5' in lColumns:
                df['EHI6']  = df[['ad_5','ad_12']].mean(axis=1)
                df['EHI6'] = np.clip(df['EHI6'], a_max=None, a_min=0.7)
                # EHI6_ready = True
            else: df['EHI6']    = 1
        elif 'ad_4'  in lColumns and 'ad_5'  in lColumns and 'ad_7' in lColumns:
            df['EHI6'] = df[['ad_4','ad_5','ad_7']].mean(axis=1)            #Average all biodiversity inputs
            df['EHI6'] = np.clip(df['EHI6'], a_max=None, a_min=0.7)         #Clip biodiversity between 0.7 and 1.0 to count for uncertainty in biodiv indices
            # EHI6_ready = True
        else: df['EHI6']    = 1

        if 'ad_8' in lColumns and 'ad_9' in lColumns:
            # non-burned_area/area + (burned/area * health_fire_danger-impact)
            if 'ad_11' in lColumns:
                # non-burned_area/area + (burned/area * (health_fire_danger-impact [0-1] / fire_density[0-3 fires in avg/year]))
                df['EHI7']    =  ( ((df['EB1_1LC'] - df['ad_8']) / df['EB1_1LC'] * 1.0) + (df['ad_8'] / df['EB1_1LC'] * (df['ad_9'] / df['ad_11'])) )
            else : df['EHI7']    =  ( ((df['EB1_1LC'] - df['ad_8']) / df['EB1_1LC'] * 1.0) + (df['ad_8'] / df['EB1_1LC'] * df['ad_9']) )
            # EHI7_ready = True
        else: df['EHI7']    = 1




        #pollution indicators
        if ['ad_10'] in lColumns:
            df['EHI8']    = 1 - ((df['ad_10'] - 1.)/(10./2))                #Mining Pollution ranges 1.0 to 10.0
            df['EHI8'] = np.clip(df['EHI8'], a_max=None, a_min=0.5)         #clip mining pollution between 0.5 and 1.0
            # EHI8_ready = True
        else: df['EHI8']    = 1

        df['EIH'] = np.power(df['EHI6']*df['EHI7']*df['EHI8'],1./(3))  #geometric mean

        #clip SUI to avoid overrun EI_IUV
        df['EIIUV']= df['EISUI']  * df['EIH']

        # save to disk
        logger.debug('** save to disk shapefile')
        df.to_file(self.path_results_infra[year], driver='ESRI Shapefile')

        # now drop geometry polygons to write out csv
        df.drop('geometry', axis=1)

        logger.debug('** save to disk csv for year: %s', year)
        df.to_csv(self.path_results_infra_csv[year], na_rep=0, index=False)


    ######################################################################################################################
    def SELUintersect(self,pArea, ID_FIELD):
        '''function to get results for only countries/areas'''
        # read in SELU file
        gdf = self.statistics_shape

        # read in country file
        gdf_country = self.reporting_shape
        # filter
        selection = gdf_country.loc[['BFA']]

        # check
        if selection.empty:
            raise RuntimeError('the chosen area is not existing in the region shapefile')

        # intersect
        gdf['HYBAS_HA'] = gdf.geometry.area/(10000.)
        gdf[ID_FIELD] = gdf.index
        gdf = gpd.overlay(gdf, selection, how='intersection')
        gdf['AREA_HA'] = gdf.geometry.area/(10000.)
        # calculate factor of hybas_area used after cut (<1.0 means hybas not fully used)
        gdf['F_AREA'] = gdf['AREA_HA'] / gdf['HYBAS_HA']
        return gdf[ID_FIELD].tolist(), gdf[[ID_FIELD, 'F_AREA']]

    ######################################################################################################################
    def create_account_table(self, year, ID_FIELD = 'HYBAS_ID'):
        #function creates the INFRA/FUNCTIONAL SERVICES ACCOUNT TABLE
        path_INFRA_shp = self.path_results_infra[year]
        path_LUT_CODE = self.config["infra"]["lut_infra"]
        reporting_shape = self.reporting_shape #path_aoi
        take_area_polygon = False



        lAverage = ['EIP1_11','EIP1_12_ha','EIP1_2','EIP1_3','EIP1_5','EIP1_6','NLEP_ha','NREP_ha','TEIP_ha','EISUI','EHI6','EHI7','EHI8','EIH','EIIUV']
        lSum = ['EIP1_12','EIP1_4','EIP2','EIP3','EIP4','EIP6','EIP5_1','EIP5_2']

        #1. read in results of SELU file for year
        df = gpd.read_file(path_INFRA_shp).drop('geometry', axis=1)

        #read dataframe with code explanation
        df_LUT = pd.read_csv(path_LUT_CODE, sep=';').set_index('I_CODE')

        #2. loop over all regions
        for pArea in self.reporting_shape.index:
            logger.debug('**** %s', pArea)
            #first special case
            if pArea == 'all':
                #df_grouped = df.merge(df_ad, on=ID_FIELD)
                df_grouped = df.drop(ID_FIELD, axis=1)
            else:
                #here we filer to country SELU IDs
                dRegionIDs,dAreaFactor = self.SELUintersect(pArea, ID_FIELD)
                df_grouped = df[df[ID_FIELD].isin(dRegionIDs)].copy()

                df_grouped = df_grouped.merge(dAreaFactor, on=ID_FIELD)
                #drop ID_FIELD
                df_grouped.drop(ID_FIELD, axis=1, inplace=True)

                #adjust values for columns were we need the SUM
                lSum = [x for x in df_grouped.columns if x not in lAverage]
                #remove some columsn
                lSum.remove('DLCT_{}'.format(2015))
                lSum.remove('F_AREA')
                for element in lSum:
                    df_grouped[element] = df_grouped[element] * df_grouped['F_AREA']
                df_grouped.drop('F_AREA', axis=1, inplace=True)

            #add a field for calculating averages
            #now we prepare the columns for the weighted area
            for element in lAverage:
                df_grouped[element] = df_grouped[element] * df_grouped['Area_rast']

            #add a field for calculating averages
            df_grouped['num_SELU'] = 1

            #calculate the sum of the fields by all and DLCT
            #results per DLCT
            results_DLCT = df_grouped.groupby('DLCT_{}'.format(2015)).sum()

            #results for full area
            results_all = df_grouped.drop('DLCT_{}'.format(2015),axis=1).groupby('num_SELU').sum()
            results_all.reset_index(inplace=True)
            results_all['num_SELU'] = df_grouped['num_SELU'].sum()
            results_all.rename({0: 'total'}, inplace=True)

            #combine
            results_all = results_all.append(results_DLCT, sort=True)
            #free
            df_grouped   = None
            results_DLCT = None

            #now we have to calculate the 'weighted' average for some of the columns 'and overwrite sum'
            for element in lAverage:
                results_all[element] = results_all[element] / results_all['Area_rast']

            #prepare for writing out
            #rotate map
            results_all = results_all.T
            #add the LUT for column names
            results_all = results_all.join(df_LUT)
            #keep order of LUT table
            results_all = results_all.reindex(df_LUT.index.to_list())
            #drop empty rows
            results_all = results_all.dropna(axis=0)
            results_all.index.name = 'I_CODE'

            #write out as csv
            path_report = self.report[year].format(pArea)
            results_all.to_csv(path_report, index_label = 'I_CODE'.format(year))

        return

    ######################################################################################################################
    def extract_stats(self, year, ID_FIELD='HYBAS_ID'):

        #1. build datacube raster from all input
        lPaths = []
        lColumns = []
        #lPaths.append(pm.leac.leacOut.__dict__['lc' + str(idx+1)])       # LandCover
        lColumns.append('EIP1_11')
        lPaths.append(self.leac_gbli_sm[year]) # GBLI
        lColumns.append('EIP1_2')
        lPaths.append(self.naturalis_sm)            # NATURALIS (duplicate to keep consistent datacube)
        lColumns.append('EIP1_3')
        lPaths.append(self.lfi_meff[year])      # FRAGMEFF
        lColumns.append('EIP1_4')
        lPaths.append(self.riverSRMU)              # RAWI (based on rasterized log(SRMU)
        lColumns.append('EIP1_5')
        lPaths.append(self.natriv)               # NATRIV
        lColumns.append('EIP1_6')
        lPaths.append(self.fragriv)              # FRAGRIV


        df = gpd.GeoDataFrame(index=self.statistics_shape.index, geometry=self.statistics_shape.geometry ,  dtype=float)
        df.index.name = ID_FIELD
        m2_2ha = 1/100**2
        pix2ha = self.accord.pixel_area_m2()/100**2
        df['DLCT_2015'] = self.statistics_shape["DLCT_2015"]
        df['Area_poly'] = df.area * m2_2ha

        for idx,path in enumerate(lPaths):
            stats = statistics_byArea(path, self.statistics_raster, {row[0] : row[1]['SHAPE_ID'] for row in self.statistics_shape.iterrows()})
            if idx == 0:
                df["Area_rast"] = stats['px_count']*pix2ha
                #normalize GBLI
                stats["sum"] = stats["sum"] / 100
            if idx == 3:
                df[lColumns[idx]] = stats["sum"] * pix2ha
            else:
                df[lColumns[idx]] = stats["sum"]/stats["px_count"]


        df['Area_delta'] = (df['Area_poly'] - df["Area_rast"]*m2_2ha) * 100.0 / df['Area_poly']

        #create final pandas dataframe to save to disk
        logger.debug('** generate result table...')
        df['EIP1_12'] = df['EIP1_11'] * df['Area_rast']
        df['EIP1_12_ha'] = df['EIP1_12'] / df['Area_poly']
        df['EIP2'] = df['EIP1_12'] * df['EIP1_2'] * df['EIP1_3']
        df['EIP3'] = df['EIP1_4'] * df['EIP1_5'] * df['EIP1_6']
        df['EIP4'] = df['EIP2'] + df['EIP3']

        df['EB1_1LC'] = df['Area_rast']
        df['NLEP_ha']    = df['EIP2'] / df['Area_poly']
        df['NREP_ha']    = df['EIP3'] / df['Area_poly']
        df['TEIP_ha']   = df['EIP4'] / df['Area_poly']

        #TODO remove temporary patch merge in river length + area
        srmu = gpd.read_file(self.rawi_selu)
        srmu = srmu.set_index('HYBAS_ID')
        df = pd.merge(df, srmu['LENGTH_GEO'], on=ID_FIELD)
        df = pd.merge(df, srmu['RS_'+str(year)], on=ID_FIELD)
        cols_to_rename = [{'LENGTH_GEO':'EB1_21RSE'},{'RS_'+str(year):'EB1_22RSE'}]
        for col in cols_to_rename:
            if list(col.keys())[0] in df.columns:
                df = df.rename(index=str, columns={list(col.keys())[0]: list(col.values())[0]})

        df.crs = self.accord.reporting_profile.get('crs')

        # save to disk
        logger.debug('** save to disk shapefile')
        df.to_file(self.path_results_eip[year], driver='ESRI Shapefile')

        #now drop geometry polygons to write out csv
        df = df.drop('geometry', axis=1).drop('Area_delta', axis=1)

        logger.debug('** save to disk csv for year: %s', year)
        #get list of columns specific for given year
        #dees snap ik niet goed want alle columns zijn net hernamed
        #lExtract = [x for x in df.columns if '_'+str(year) in x]
        path_out = os.path.join(self.temp_dir(),'NCA_INFRA-EIP_{}_SELU_{}.csv'.format(self.aoi_name,year))
        df.to_csv(path_out, na_rep=0, index=True)

    def check_leac(self):
        logger.info("Checking if LEAC is available")
        for year in self.years:
            if year in self.config["infra"]["leac_result"]:
                logger.info("leac information was manual added")
                # basename = os.path.basename(self.config["infra"]["leac_result"][year])
                continue
            expected_path = os.path.join(self.temp_dir().replace(self.component, 'leac'),
                                         f'cci_LC_{year}_100m_3857_PSCLC.tif')
            if not os.path.exists(expected_path):
                logger.error('It seems that no input leac location was given and that the default location ' +\
                             f'{expected_path} does not contain a valid raster. please run leac module first.' )
            else:
                self.config.update({self.component : {'leac_result' : {year : expected_path}}})

    def make_output_filenames(self):
        #easier typing
        general = self.config["infra"]["general"]
        smoothing_settings = f'{str(general["gaussian_sigma"])}_{str(general["gaussian_kernel_radius"])}'
        lc_urban = str(general["lc_urban"])
        epsg = str(self.epsg)

        #GBLI processing filenames
        self.leac_gbli_nosm = dict()
        self.leac_gbli_sm = dict()
        self.leac_gbli_diff = dict()

        for idx, year in enumerate(self.years):
            psclc = self.config["infra"]["leac_result"][year]
            basic_file = os.path.splitext(os.path.basename(psclc))[0]
            file = f'{basic_file}_gbli_nosm.tif'
            self.leac_gbli_nosm[year] = os.path.join(self.temp_dir(),file)

            file = f'{basic_file}_gbli_sm{smoothing_settings}.tif'
            self.leac_gbli_sm[year] = os.path.join(self.temp_dir(),file)
            if year != self.years[0]:
                file = file.replace('gbli','gbli-change-'+str(self.years[idx-1]))
                self.leac_gbli_diff[year] = os.path.join(self.temp_dir(),file)

        #Naturalis processing filenames
        self.naturalis_shape = self.config["infra"]["nlep"]["naturalis"]
        basic_file = os.path.splitext(os.path.basename(self.naturalis_shape))[0]
        self.naturalis_nosm_reverse = os.path.join(self.temp_dir(), f'{basic_file}_nosm_reverse.tif')
        self.naturalis_nosm = os.path.join(self.temp_dir(), f'{basic_file}_nosm.tif')
        #was in subfolder "stock"
        self.naturalis_sm = os.path.join(self.maps, f'{basic_file}_sm{smoothing_settings}.tif')

        #Lfi proccessing filenames
        #catchments
        self.catchments_temp = {}
        self.catchments_processed= {}
        self.catchments_clean = {}
        self.lfi_mesh= {}
        self.lfi_mesh_clean= {}
        self.lfi_meff = {}
        self.lfi_meff_hybas = {}


        for basin in self.config["infra"]["nlep"]["catchments"].keys():
            file =self.config["infra"]["nlep"]["catchments"][basin]
            self.catchments_temp[basin] = os.path.join(self.temp_dir(), os.path.basename(file))
            self.catchments_processed[basin] = os.path.join(self.temp_dir(), os.path.basename(file))
            self.catchments_clean[basin] = os.path.join(self.temp_dir(), os.path.basename(file))

            self.lfi_mesh[basin] = os.path.join(self.temp_dir(),
                                                f"MESH_intersect_WAP_{basin}_EPSG{epsg}.shp")
            self.lfi_mesh_clean[basin] = os.path.join(self.temp_dir(),
                                                      f"MESH_intersect_WAP_{basin}_EPSG{epsg}_clean.shp")
            self.lfi_meff_hybas[basin] = {}
            for idx, year in enumerate(self.years):
                self.lfi_meff_hybas[basin][year] = os.path.join(self.temp_dir(),
                                                    f"WAP_TIER{str(self.tier)}_hybas{str(basin)}_{epsg}_clean_" + \
                                                          f"EPSG{epsg}_FRAG{str(year)[-2:]}_{lc_urban}.tif")

        for idx, year in enumerate(self.years):
            self.lfi_meff[year] = os.path.join(self.maps, f"meff_{str(year)}_{lc_urban}.tif")

        #OSM
        #outfile = os.path.join(self.nlep.root_nlep_temp, os.path.splitext(os.path.basename(merged_trunkroads_railways))[0]) + '.tif'
        osm_base = os.path.splitext(os.path.basename(self.config["infra"]["nlep"]["osm"]))[0]
        self.merged_trunkroads_railways_inv = os.path.join(self.temp_dir(), f'{osm_base}_inversed.tif')
        self.merged_RR_inversed = os.path.join(self.temp_dir(), f'vector_{osm_base}_inversed.shp')

        #nlep
        self.nlep = {}
        self.clep = {}
        for idx, year in enumerate(self.years):
            self.nlep[year]= os.path.join(self.maps, f'nlep_{str(year)[-2:]}.tif')
            if idx != 0:
                self.clep[year]= os.path.join(self.maps, f'clep_{str(year)[-2:]}.tif')

        #RAWI
        self.river_buffer = RIVER_BUFFER
        #self.resolution = self.accord.
        base = os.path.splitext(os.path.basename(self.config["infra"]["nrep"]["gloric"]))[0]
        self.riverSRMU = os.path.join(self.maps, base + '_log_SRMU.tif')
        self.rawi_mask = os.path.join(self.temp_dir(), base + '.tif')
        self.rawi_shape = os.path.join(self.temp_dir(), base + '.shp')
        self.rawi_selu = os.path.join(self.maps, base + '_SRMU.shp')
        self.rawi = {}
        for year in self.years:
            self.rawi[year] = os.path.join(self.maps, base + f'_SRMU_RAWI_{year}.tif')

        #NATRIV
        #should be generalized to WAP or other shortnames
        self.natriv = os.path.join(self.maps, 'WAP_natriv_3857.tif')

        #FRAGRIV
        self.fragriv = os.path.join(self.maps, "WAP_fragriv_3857.tif")
        self.fragriv_hybas = {}
        for basin in self.config["infra"]["nlep"]["catchments"].keys() :
            file = os.path.splitext(os.path.basename(self.config["infra"]["nlep"]["catchments"][basin]))[0]
            self.fragriv_hybas[basin] = os.path.join(self.temp_dir(), file + 'fragriv.tif')

        #accounting results

        self.path_results_eip = {year: os.path.join(self.temp_dir(), 'NCA_INFRA-EIP_SELU_{}.shp'.format(year))
                                 for year in self.years}
        self.path_results_infra = {year: os.path.join(self.maps, 'NCA_INFRA_SELU_{}.shp'.format(year))
                                   for year in self.years}
        self.path_results_infra_csv = {year: os.path.join(self.temp_dir(), 'NCA_INFRA_SELU_{}.csv'.format(year))
                                       for year in self.years}
        self.report = {year: os.path.join(self.reports, 'CECN_infra_report_year-{}_for_{}.csv'.format(year, '{}'))
                       for year in self.years}
