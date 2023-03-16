'''
RESTRICTIONS
This software is property of VITO and free-of-use. Any results originating from this
script should be referenced with:
"Smets, Buchhorn (2021), Ecosystem Capability Accounting (ENCA) software package, initially
developed in the framework of the PAPBIO project (FED/2018/399-509)."

VITO has no liability for any errors or omissions in the contents of this script.

SYNOPSIS
python3.6 leac_SELU.py -e path_to_yaml -v

DESCRIPTION
Processor to create Land Cover Account (LEAC), as part of Ecosystem Capability Accounting
(see http://ecosystemaccounting.net). This script calculates the Social Ecological Landscape
Units (SELU), derived from land cover map.

PREREQUISITES
Python >= 3.0

MANDATORY INPUTS
   * land cover maps : at lease 2 different dates with same classes
   * HYBAS shapefile
   * Look-up table for mapping landscape classes

AUTHORS
Bruno Smets <bruno.smets@vito.be>
Dr Marcel Buchhorn <marcel.buchhorn@vito.be>

VERSION
5.0 (2021-07-05, initial 2019-10-17)
'''

import os
import sys
import optparse
import pathlib
import shutil
import subprocess
import time
import datetime
import traceback
import numpy as np
import pandas as pd
import geopandas as gp
import rasterio
import rasterio.mask
import rasterio.features
import fiona
from scipy import ndimage
import shapefile
from shapely.geometry import shape
from tqdm import tqdm

from general.params import Parameters
import general.process as process
from general.prepare_web import Raster
from general.prepare_web import Shape

#################################################
#get the geometry of the area
def get_extent(path_aoi_shp):
    layer = fiona.listlayers(path_aoi_shp)[0]
    with fiona.open(path_aoi_shp, layer = layer) as shapefile:
        for feature in shapefile:
            if 'Id' in feature['properties']: key = 'Id'
            else: key = 'GID_0'  #TODO
            #if (feature['properties'][key] == 1) or (feature['properties'][key] == 0):
            if not feature['properties'][key] is None:
                ishape = feature['geometry']
    return ishape

##################################################################################################################
def calc_DLCT(pm, year, dict_DLCT, path_out_root, gf_radius=5):

    ###0. read out lc map in extent of the AOI
    print('* load in land cover map and cut to area of interest')
    #get the geometry of the FDH area
    path_aoi_shp = pm.leac.leacIn.__dict__['aoi']
    path_aoi = os.path.splitext(path_aoi_shp)[0]+'_mask_'+str(pm.run.resolution).zfill(3)+'m.tif'
    layer = fiona.listlayers(path_aoi_shp)[0]
    with fiona.open(path_aoi_shp, layer = layer) as shapefile:
        #TODO if only one feature, then we can just pick geometry - no need to check key
        for feature in shapefile:
            if 'Id' in feature['properties']: key = 'Id'  #TODO add check on key all small chars
            else: key = 'GID_0'  #TODO
            #if (feature['properties'][key] == 1) or (feature['properties'][key] == 0):
            if not feature['properties'][key] is None:
                ishape = feature['geometry']
    #extract the land cover for this area
    path_lc = pm.leac.leacIn.__dict__['lc'+str(year)]   #use original input map
    with rasterio.open(path_lc) as src:
        aLC, LC_transform = rasterio.mask.mask(src, [ishape], crop=True, indexes=1)
        LC_profile = src.profile
        LC_profile.update(height = aLC.shape[0],
                          width = aLC.shape[1],
                          transform = LC_transform,
                          nodata = 0)
    #run a check
    with rasterio.open(path_aoi) as src:
        check = src.profile
    if check['transform'] == LC_transform:
        print('* extraction of landcover map in extent of given AOI worked correctly')
    else:
        print('* WARNING: the LC map does not fully cover given AOI.')
        #raise RuntimeError('the extraction of the LC map for given AOI did not worked.')

    ###1.reclassify (group) into DLCT
    print('\n* generate pre-DLCT map')
    #get histogramm of current class ditribution in area of interest
    print('** histogram of the original land cover map')
    aLC[aLC == 200] = 80
    dStatistics = dict(zip(*np.unique(aLC, return_counts=True)))
    if 255 in dStatistics: del dStatistics[255]
    if 0 in dStatistics: del dStatistics[0]
    df_LC = pd.DataFrame.from_dict(dStatistics, orient = 'index', columns=['count'])
    df_LC['percentage'] =( df_LC['count'] / df_LC['count'].sum()) * 100
    print(df_LC[['percentage']])

    #rule set to convert into DLCT
    print('** REclassify LC map to pre-DLCT map')
    aPreDLCT = np.zeros_like(aLC, dtype=np.uint8)
    for key in dict_DLCT.keys():
        print('*** work on reclassifing class: {}'.format(key))
        aPreDLCT[aLC == key] = dict_DLCT[key]
    aLC = None

    print('** histogram of the pre-DLCT land cover map')
    dStatistics = dict(zip(*np.unique(aPreDLCT, return_counts=True)))
    del dStatistics[0]
    df_LC = pd.DataFrame.from_dict(dStatistics, orient = 'index', columns=['count'])
    df_LC['percentage'] =( df_LC['count'] / df_LC['count'].sum()) * 100
    print(df_LC[['percentage']])

    ###2. split the pre-CLCT into mask for each class
    print('\n* generate the DLCT map')
    print('** prepare Gaussian Filter')
    #we generate an array with as many dimensions as classes
    lClasses = sorted(list(set(dict_DLCT.values())))
    aDLCTgf = np.zeros((aPreDLCT.shape[0], aPreDLCT.shape[1], len(lClasses)), dtype=np.float32)
    #each 3rd dimension represent one of the pre-CLCT classes in the order of the lClasses list
    #set the corresponding class in each 3rd dimension to 1 and the rest stays zero
    for idx,value in enumerate(lClasses):
        print('*** prepare class {} for Gausian filtering'.format(value))
        aDLCTgf[aPreDLCT == value, idx] = 1

    #free
    aDLCT = None
    aPreDLCT = None

    #run the Gausian Filter
    print('** run Gaussian Filter')
    for idx,value in enumerate(lClasses):
        print('*** run gaussian filter for class {}'.format(value))
        ndimage.filters.gaussian_filter(aDLCTgf[:,:,idx], gf_radius/3, truncate=3, output=aDLCTgf[:,:,idx])

    #write out result of gaussian filter in temp
    path_out = os.path.join(path_out_root, 'DLCT_gaussian-filter_map_epoch{}.tif'.format(year))
    print('*** write reslults to file {}'.format(path_out))
    LC_profile.update(driver = 'GTiff',count = len(lClasses), dtype=rasterio.float32, nodata=None)
    with rasterio.open(path_out, 'w', **LC_profile) as dst:
        dst.update_tags(file_creation = time.asctime(), creator = 'Dr. Marcel Buchhorn (VITO)',
                        Info = 'pre-DLCT map. Each band shows the gaussian filtering of one of the pre-DLCT types.',
                        NODATA_value = -1,
                        Bands = ','.join([str(x) for x in lClasses]) )
        #dst.write(np.rollaxis(aDLCTgf, axis=2))
        for idx,value in enumerate(lClasses):
            dst.write(aDLCTgf[:,:,idx],idx+1)

    pm.leac.leacOut.__dict__['DLCT'+str(year)] = path_out
    pm.update(options.config)

    #free
    aDLCTgf = None

    return ishape

############################################################################################################
def calc_SELU(pm, year, ishape, dict_DLCT, dict_majority, rule_order, path_out_root, hydro_level=12):
    ### 5. create SELU
    print('\n* generate SELU')
    # load in hydro basin and rasterize to extent of landcover
    print('** run over all Hydro-Basins')
    ###Note: add a check that the shapefile is in the correct projection
    path_hydro_shp = pm.leac.leacIn.__dict__['adm_ws'+str(hydro_level)]
    layer = fiona.listlayers(path_hydro_shp)[0]
    #get BBOX of sieved file to filter the hydro file
    path_DLCT = pm.leac.leacOut.__dict__['DLCT'+str(year)]
    with rasterio.open(path_DLCT) as src:
        bbox = src.bounds

    lClasses = sorted(list(set(dict_DLCT.values())))

    pixArea = int(pm.run.resolution) * int(pm.run.resolution)   #100m is 100x100
    count = 0
    lResults = []

    #loop over all Hydro basins in the AOI
    with fiona.open(path_hydro_shp, layer = layer) as shapefile:
        # some stuff for progress bar
        max_counter = len(shapefile)
        t = tqdm(total=int(max_counter))
        for feature in shapefile.filter(bbox=(bbox.left, bbox.bottom, bbox.right, bbox.top)):
            #check if this hybas intersects our needed shape file
            hshape = feature['geometry']
            if not shape(hshape).intersects(shape(ishape)): continue
            #now check if that is not only an intersect of the boundry
            if not shape(hshape).representative_point().within(shape(ishape)): continue

            count +=1
            #print('*** work on basin {} [{}]'.format(feature['properties']['HYBAS_ID'], count))
            #extract the gaussian filtered pre-DLCT
            with rasterio.open(path_DLCT) as src:
                aGFmap, DLCT_transform = rasterio.mask.mask(src, [hshape], crop=True, nodata=-1)
            #create a masked array
            aGFmap = np.ma.masked_equal(aGFmap, -1)

            #run check if not all is zero
            if np.all(aGFmap == 0):
                lResults.append([feature['properties']['HYBAS_ID'], 255, shape(hshape) ])
                continue
            #run check if this Hybas is not only partially covered by the given raster file- due to border artefacts we use a threshold of 10%
            N = np.sum(aGFmap, axis=0)  #check if all non-masked pixels have DLCT, if not -> subtract to skip hybas covered with mostly no_value LC maps (but with few LC pixels)
            check = (aGFmap[0,:,:].count() - len(np.where(N==0)[0])) / (shape(hshape).area/pixArea)
            #print('......... ' + str(check))
            #TODO FIX CHECK for CCI-LC (300m)
            '''
            if (check < 0.9) or (check > 1.1):
                lResults.append([feature['properties']['HYBAS_ID'], 255, shape(hshape) ])
                t.update()
                continue
            '''
            N = None
            #apply the ruleset to determine the final DLCT per Hydrobasin
            DLCT_result = 0
            """
            rules: 
                1. urban >= 25% --> 30
                2. no mayority & urban + agri >= 5% --> mixed landscape, anthropized (70)
                3. no majority & urban + agri < 5%     --> mixed landscape, natural (80)
                4. agri >= 50%  --> 40
                [X. mangrove >= 50% --> 15]
                5. forest >= 50%   --> 10
                6. water Wet >= 50%   --> 60
                7. grass+shrub >= 50%  --> 20
                8. bare >= 50%   --> 50
            """

            #generate majority class array
            aMajority = np.zeros((len(lClasses),), dtype=np.uint8)
            for idx,value in enumerate(lClasses):
                aMajority[idx] = ((aGFmap[idx,:,:].sum() / aGFmap[:,:,:].sum()) >= dict_majority[value]).astype(np.uint8)

            #first: urban has majority
            idx = lClasses.index(30)
            if aMajority[idx] == 1:
                DLCT_result = lClasses[idx]

            #second: pixel without majority
            mNoMaj = (aMajority.sum() == 0)
            #now we have to know if urban + agri is over 5% threshold
            idx_urban = lClasses.index(30)
            idx_agri =  lClasses.index(40)
            aAnthropized = (aGFmap[idx_urban,:,:].sum() + aGFmap[idx_agri,:,:].sum() ) / aGFmap[:,:,:].sum()
            #free
            aGFmap = None

            if ((mNoMaj == True) & (aAnthropized >= 0.05) & (DLCT_result == 0)):
                DLCT_result = 70
            if ((mNoMaj == True) & (aAnthropized < 0.05) & (DLCT_result == 0)):
                DLCT_result = 80
            #free
            idx_urban = None
            idx_agri = None
            aAnthropized = None
            mNoMaj = None

            #now the rest of the rules in this order
            for iRule in rule_order:
                idx = lClasses.index(iRule)
                #print('idx {}'.format(idx))
                if (aMajority[idx] == 1) and (DLCT_result == 0):
                    DLCT_result = lClasses[idx]

            #append result to output
            lResults.append([feature['properties']['HYBAS_ID'], DLCT_result, shape(hshape) ])
            #free
            aMajority = None
            DLCT_result = None

            # update progress bar
            t.update()
        # close progress bar
        t.close()

    #write out
    print('\n * write out final SELU shapefile')

    #create final geopandas dataframe and write out
    gdf_SELU = gp.GeoDataFrame(pd.DataFrame(lResults, columns=['HYBAS_ID','DLCT_' + str(year),'geometry']), geometry='geometry')
    gdf_SELU.crs = "EPSG:"+str(pm.run.projection)

    #print(gdf_SELU)

    aoi = pm.run.region_short
    path_shp = os.path.join(path_out_root, 'SELU_'+str(aoi)+'_'+'epoch{}_HYDROlevel-{}.shp'.format(year, hydro_level))
    gdf_SELU.to_file(path_shp)

    pm.leac.leacOut.__dict__['SELU'+str(year)] = path_shp
    pm.update(options.config)

    return

######################################################################################################################
def merge_SELU(pm, years, path_out_root,  hydro_level=12):
    print('Merging years together into single SELU shapefile')
    df = [None] * len(years)
    for idx, year in enumerate(years):
        path_selu_epoch = pm.leac.leacOut.__dict__['SELU'+str(year)]
        df[idx] = gp.read_file(path_selu_epoch)

    data = df[0]
    for idx,year in enumerate(years[1:]):
        data['DLCT_'+str(year)] = df[idx+1]['DLCT_'+str(year)]

    print(data.shape)
    aoi = pm.run.region_short
    path_selu = os.path.join(path_out_root, 'SELU_'+str(aoi)+'_'+'HYDROlevel-{}.shp'.format(hydro_level))
    data.to_file(path_selu, drivers='ESRI Shapefile')

    pm.leac.leacOut.__dict__['SELU'] = path_selu
    pm.update(options.config)

    return

######################################################################################################################
def main():

    #read yaml configuration file
    pm = Parameters(options.config)

    #TODO move to yaml file
    #PAPBIO configuration
    #'''
    #in-classes: 11 OF, 12 CF, 13 riparian, 14 mangrove, 15 savana trees, 21 shrub, 31 grass, 40 crop, 41 agroforest, 50 urban,
    #         60 bare, 61 mines, 80 water bodies, 90 wetland
    """ 
    new_code | name  (value of LC map)
    -------------------------------------------------
    10 | forest landscape (11 + 12 + 13 + 14)
    20 | savanna landscape (15 + 21)
    25 | grass landscape (31)
    30 | Artificical landsacpe (50 + 61)
    40 | Cropland (40 + 41)
    50 | Bare (60)
    60 | Water_WetLands (80 + 90)
    """
    dict_DLCT = {11:10, 12:10, 13:10, 14:10, 15:20 ,21:20, 31:25, 40:40, 41:40, 50:30, 60:50, 61:30, 80:60, 90:60}
    #dictionary keeping the majority thresholds for DLCT
    dict_majority = {10: 0.5, 20: 0.5, 25:0.4 ,30:0.25, 40:0.5, 50:0.5, 60:0.5 }
    rule_order = [40, 10, 60, 20, 25, 50]

    #PAPBIO WCF TIER-3 configuration
    #'''
    #in-classes: 1 CF, 2 OF, 3 savanna, 4 grass, 5 water, 6 urban, 7 mines, 8 agriculture
    """ 
    new_code | name  (value of LC map)
    -------------------------------------------------
    10 | forest landscape (1 + 2)
    20 | savanna landscape (3)
    25 | grass landscape (4)
    30 | Artificical landsacpe (6 + 7)
    40 | Cropland (8)
    50 | Bare (-)
    60 | Water_WetLands (5)
    """
    if 'PNMB' in options.config:
        dict_DLCT = {1:10, 2:10, 3:20, 4:25, 8:40, 6:30, 7:30, 5:60}
        #dictionary keeping the majority thresholds for DLCT
        dict_majority = {10:0.5, 20:0.5, 25:0.4 , 30:0.25, 40:0.5, 60:0.5 }
        rule_order = [40, 10, 60, 20, 25]
    #'''
    #CCI-LC configuration
    """ 
    new_code | name  (value of LC map)
    -------------------------------------------------
    10 | forest landscape (50,60,61,62,70,71,72,81,82,90,100)
    20 | savanna landscape (110,121,122)
    25 | grass landscape (120,130)
    30 | Artificial landscape (190)
    40 | Cropland (10,11,12,20,30,40) 
    50 | Bare (140,150,151,152,153,200,201,202)
    60 | Water_WetLands (160,170,180,210,220)
    """
    if 'KEN' in options.config or 'VNM' in options.config:
        dict_DLCT = {50:10,60:10,61:10,62:10,70:10,71:10,72:10,81:10,82:10,90:10,100:10, \
                 110:20,121:20,122:20,120:25,130:25,190:30,10:40,11:40,12:40,30:40,40:40, \
                 140:50,150:50,151:50,152:50,153:50,200:50,201:50,202:50, \
                 160:60,170:60,180:60,210:60,220:60}
        #dictionary keeping the majority thresholds for DLCT
        dict_majority = {10: 0.5, 20: 0.5, 25:0.4 ,30:0.25, 40:0.5, 50:0.5, 60:0.5 }
        rule_order = [40, 10, 60, 20, 25, 50]

    path_out_root = pm.leac.leacOut.root_leac
    if not os.path.exists(os.path.join(path_out_root,'SELU')):
        os.makedirs(os.path.join(path_out_root,'SELU'))

    #calculate Dominant Land Cover Type (DLCT) and Socio-Economic Landscape Unit (SELU)
    for year in pm.run.yearsL:
        print('Processing year {}'.format(year))
        path_out = os.path.join(path_out_root,'temp')
        if 'DLCT'+str(year) in pm.leac.leacOut.__dict__:
            print('DLCT already available {}'.format(pm.leac.leacOut.__dict__['DLCT'+str(year)]))
            ishape = get_extent(pm.leac.leacIn.__dict__['aoi'])
        else:
            ishape = calc_DLCT(pm, year, dict_DLCT, path_out)
        path_out = os.path.join(path_out_root,'temp')
        calc_SELU(pm, year, ishape, dict_DLCT, dict_majority, rule_order, path_out)

    path_out = os.path.join(path_out_root,'SELU')
    merge_SELU(pm, pm.run.yearsL, path_out)

    print("Processing finished for %s" % pm.run.region_long)

#######################################################################################################################
if __name__ == '__main__':

    try:
        # check if right Python version is available.
        assert sys.version_info[0:2] >= (3,5), "You need at minimum python 3.5 to execute this script."
        start_time = time.time()
        # ini the Option Parser
        parser = optparse.OptionParser(formatter=optparse.TitledHelpFormatter(), usage=globals()['__doc__'], version="%prog v2.0")
        parser.add_option ('-v', '--verbose', action='store_true', default=False, help='verbose output')
        parser.add_option ('-e', '--config', help='Path to the config.ini file. Needed.')
        parser.add_option ('-r', '--overwrite', action= 'store_true', default=False, help='Reprocess all data through overwriting. Optional')
        # parse the given system arguments
        (options, args) = parser.parse_args()
        # do checks on the parsed options
        if (not options.config) or (not os.path.isfile(os.path.normpath(options.config))):
            parser.error ("the -e argument for the config file is missing or the given path doesn't exist!!!")
        if len(args) != 0:
            parser.error ('too many arguments')

        if options.verbose: print('START OF MODULE: ENCA LEAC SELU')
        if options.verbose: print(time.asctime())
        # call main function - "options" object is set to global object
        main()
        #Pparameter, sSuccess = main()
        if options.verbose: print('END OF MODULE: ENCA LEAC SELU')
        if options.verbose: print(time.asctime())
    except:
        traceback.print_stack()