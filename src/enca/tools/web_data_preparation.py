#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PapBIO

data preparation for ingesting into webserver

version 0.1
2021-06-24


Note: this snippet is used to prepare the raster and vector datasets for ingestion in webserver
      a) cut to AOI
      b) re-scale to Byte if needed
      c) add color tables
      d) add overviews
      e) add metadata
      f) adapt the file name
      g) add the file to the GeoJSON for ingestion

"""

import os
import time
import traceback
import web_helper_functions as helper
import json
import pandas as pd
import geopandas as gpd
import numpy as np
import rasterio
import rasterio.mask
import fiona
import shutil

##### SETUP area - could be outsourced in YAML file ###
#AOI - adapt this to your area!
continent = 'africa'
root_out = os.path.normpath(r'/data/nca_vol2/web')
ID_FIELD = 'HYBAS_ID'

#'''
#specifics
version = 5
rc = '09'
lYears = [2000,2005,2010,2015,2018]
aoi_name = 'NKL'
tier = 2
aoi_extent_shp = os.path.normpath(r'/data/nca_vol2/aux_data/local/processed/Africa/NKL/aoi/NKL_AOI_3857_V4.shp')
aoi_hybas_shp   = os.path.normpath(r'/data/nca_vol2/aux_data/local/processed/Africa/NKL/selu/SELU_HYDROlevel-12_NKL_EPSG3857.shp')
#files --> tuple of (web-theme, final_name_pattern (adds _AOI_year_version), type, folder/file path, annual_datasets [False, True], 
#                    colorbar [None, defined_path_to_txt, color_string], scale [None, [min, max], vector sld file [None, path])
filelist = [
    #('CARBON-INPUT', 'C2_3_NPP', 'raster', '/data/nca_vol2/enca/africa/C5/Version5/Tier1/carbon_rc01/2_NPP/output/', True, 'Greens', [0, 20], None),
    ('CARBON-STATS', 'CECN_CARBON', 'vector', '/data/nca_vol2/enca/africa/NKL/Version5/Tier2/carbon_rc06/9_INDEX_cal', True, None, None, None),
    ('TOTAL-TREND', 'CECN_TOTAL-TREND', 'vector','/data/nca_vol2/enca/africa/NKL/Version5/Tier2/total_rc09/10_TREND_cal/NCA_TEC-trend_Indices_SELU_2000-2018.shp', False, None, None, '/data/nca_vol2/qgis/Style_templates/TEC_total_trend.sld'),
    #('TOTAL-TREND', 'CECN_TEC-TREND', 'raster', '/data/nca_vol2/test/bruno/Niokolo/TEC-trend_NKL.tif',False,'/data/nca_vol2/qgis/legents/TREND_raster.txt',None,None),
    ('TOTAL-STATS', 'CECN_TOTAL', 'vector', '/data/nca_vol2/enca/africa/NKL/Version5/Tier2/total_rc09/9_INDEX_cal', True, None, None, None),
    ('WATER-STATS', 'CECN_WATER', 'vector', '/data/nca_vol2/enca/africa/NKL/Version5/Tier2/water_rc04/INDEX_cal', True, None, None, None),
    ('INFRA-STATS', 'CECN_INFRA', 'vector', '/data/nca_vol2/enca/africa/NKL/Version5/Tier2/infra_rc06/infra', True, None, None, None),
    ('LEAC-INPUT', 'CECN_LEAC_LC100', 'raster', '/data/nca_vol2/enca/africa/NKL/Version5/Tier2/leac_rc05/stock/', True, '/data/nca_vol1/qgis/legend_CGLOPS_NCA_L2-en.txt', None, None),
    ('LEAC-ADMIN', 'CECN_LEAC_ADMIN', 'vector', '/data/nca_vol2/aux_data/local/processed/Africa/NKL/reporting/NKL_reporting_V4.shp', False, None, None, None)
    ]

#################
# MAIN PROCESSING ####
#################

print('WELCOME TO DATA PREPARATION FOR WEB-SERVICES SNIPPET')
batch_time = time.time()

#make sure the outputfolder exists
root_web_folder = os.path.join(root_out,
                               continent,
                               aoi_name,
                               'Version{}'.format(version),
                               'Tier{}'.format(tier),
                               'rc{}'.format(rc))
try:
    if not os.path.exists(root_web_folder):
        os.makedirs(root_web_folder)
except:
    raise OSError('output folder for the web data could not be generated')

#webservice
domain = 'CECN_{}_Tier{}_v{}rc{}'.format(aoi_name, tier, version, rc)

#init the list to hold all information for GEOJASON to run ingest snippet
lGEOJASON = {}
#now an important step since the ingest script needs a JSON where theme dics are stored in a list for the dic entry "layers
lGEOJASON['layers'] = []
path_out_geojason = os.path.join(root_web_folder, 'config_{}_V{}rc{}.json'.format(aoi_name, version, rc))

#get the AOI shape as geometry for later raster processing
with fiona.open(aoi_extent_shp, 'r') as shpfile:
    shapes = [feature['geometry'] for feature in shpfile]

#now we run over the file list to process
for element in filelist:
    print('\nProcessing theme {} for {}'.format(element[0], domain))

    #processing differs if it is a vector or raster
    ##### VECTOR
    if element[2] == 'vector':
        print('* processing vector files...')
        
        #check if we have a single shapefile or several
        if (element[4] == False) and (os.path.isfile(element[3]) == True):
            #we have a single file
            df = pd.DataFrame([[element[3],str(lYears[0])+'-'+str(lYears[-1])]], columns = ['path', 'year'])   #was universal
        elif (element[4] == True) and (os.path.isdir(element[3]) == True):
            #we have several file so scan for results
            try:
                df = helper.createDataFrame(element[3],'.shp')
            except:
                print('no annual files were found. check settings.')
                continue
            #now we only let in entries with years from our list
            testpattern = [str(x) for x in lYears]
            df = df[df['year'].isin(testpattern)]
            
            if df.shape[0] != len(testpattern):
                print('For some processing years no theme data is available. Check if annual data was provided that the datasets were stored standalone in a subfolder.')
            
            if df.shape[0] == 0:
                print('No matching files for this theme. Check your settings')
                continue
        else:
            print('your settings for this theme are wrong. if file is given it can not be annual data sets.')
            continue
        
        #now we process all files - extract needed data, rename and save to disk
        for row in df.itertuples():
            print('** run processing for year: {}'.format(row.year))
        
            #create final file name
            file_path = os.path.join(root_web_folder,
                                     '{}_{}_Tier{}_{}_V{}rc{}.shp'.format(element[1],aoi_name, tier,row.year,version,rc ))
            
            #here I had to add a bypass for ADMIN shapefiles we wnat to pass thru without filter
            if element[0] == 'LEAC-ADMIN':
                #just a pure copy
                gdf = gpd.read_file(row.path)
                gdf.to_file(file_path)
                del gdf
            else:
                #read in base shape file
                gdf_base = gpd.read_file(aoi_hybas_shp)
                gdf_base.drop(gdf_base.columns.difference([ID_FIELD, 'geometry']), 1, inplace=True)
                
                #now read the theme dataset
                gdf = gpd.read_file(row.path)
                gdf.drop('geometry',1,inplace=True)
                
                gdf_base = gdf_base.merge(gdf, on=ID_FIELD)
                #dump to disk
                gdf_base.to_file(file_path)
                #clean up
                del gdf_base
                del gdf
            
            
            #if we have a Style then copy and rename to file_name
            if element[7] is not None:
                src_file = os.path.normpath(element[7])
                dst_file = file_path.split('.shp')[0] + r'.sld'
                try:
                    shutil.copy(src_file, dst_file)
                except:
                    print('Could not copy the style file. Do it manual before ingest.')
                del src_file
                del dst_file
            
            #add entry to json dict
            lGEOJASON['layers'].append({
                'theme': '', #element[0],
                'workspace': domain,
                'file': (os.path.basename(file_path)).split('.')[0],
                'path': os.path.dirname(file_path)
                })
            
            #clean up
            del file_path
        #clean up
        del df

    #### RASTER
    elif element[2] == 'raster':
        print('* processing raster files...')
        
        #check if we have a single raster file or several
        if (element[4] == False) and (os.path.isfile(element[3]) == True):
            #we have a single file
            df = pd.DataFrame([[element[3],'universal']], columns = ['path', 'year'])
        elif (element[4] == True) and (os.path.isdir(element[3]) == True):
            #we have several file so scan for results
            try:
                df = helper.createDataFrame(element[3],'.tif', second_pattern = '.tiff')
            except:
                print('no annual files were found. check settings.')
                continue
            #now we only let in entries with years from our list
            testpattern = [str(x) for x in lYears]
            df = df[df['year'].isin(testpattern)]
            
            if df.shape[0] != len(testpattern):
                print('For some processing years no theme data is available. Check if annual data was provided that the datasets were stored standalone in a subfolder.')
            
            if df.shape[0] == 0:
                print('No matching files for this theme. Check your settings')
                continue
        else:
            print('your settings for this theme are wrong. if file is given it can not be annual data sets.')
            continue
        
        #now we process all files - cut to AOI, if colorbar rescale, add colorbar, add overview, add metadata
        for row in df.itertuples():
            print('** run processing for year: {}'.format(row.year))
            
            #check if colorbar  is needed - if yes rescale data if needed and set nodata value to 0
            if element[5] != None:
                #check if we have to create a colorbar or if a txt file was provide(meaning already discrete dataset)
                if os.path.isfile(element[5]):
                    fRescale = False
                    colortable = helper.ReadColorTable(element[5])
                    fRescaled = False
                else:
                    fRescale = True
                    #here we create an own colortable with nodata set to 0
                    colortable = helper.CreateColorTable(element[5])
                    fRescaled = False
            else:
                fRescale = False
                colortable = None
                fRescaled = False
            
            #create final file name and location
            file_path = os.path.join(root_web_folder,
                                     '{}_{}_Tier{}_{}_V{}rc{}.tif'.format(element[1],aoi_name, tier,row.year,version,rc ))
            
            #now combine all in the new file
            try:
                #open as masked array to cut out only AOI
                with rasterio.open(row.path) as src:
                    src_data, src_transform = rasterio.mask.mask(src, shapes, crop=True)
                    profile = src.profile
                    profile.update({'height': src_data.shape[1],
                                    'width': src_data.shape[2],
                                    'transform': src_transform})
    
                src_data = src_data.squeeze()
                
                #do we have to rescale
                if (fRescale == True) and (profile['dtype'] != 'uint8'):
                    #scale all value to 1 - 255 --> reserve 0 for nodata
                    fRescaled = True
                    #convert to masked array
                    src_data = np.ma.masked_equal(src_data, profile['nodata'])
                    #check if we have own limits for min and max
                    if element[6] is not None:
                        #clamp the data to given min and max
                        src_data = np.ma.clip(src_data, float(element[6][0]), float(element[6][1]))
                        cmin = float(element[6][0])
                        cmax = float(element[6][1])
                    else:
                        cmin = src_data.min()
                        cmax = src_data.max()
                    
                    #scale factor
                    scale_factor = 1.0 /((255.0 - 1) / (cmax - cmin))
                    #scale
                    src_data = ((255 - 1) * ((src_data - cmin) / (cmax - cmin)) + 1).filled(0).astype(np.uint8)

                    profile.update({'nodata': 0,
                                    'dtype': np.uint8})
    
                #put all together in a new file
                profile.update({'tiled': True,
                                'blockxsize': 256,
                                'blockysize': 256,
                                'interleave': 'band'})
                
                with rasterio.open(file_path, 'w', **profile) as dst:
                    #add metadata
                    if fRescaled:
                        dst.update_tags(scale = scale_factor)
                    dst.update_tags(creator = 'VITO',
                                    version = 'V{}rc{}'.format(version,rc ),
                                    creation_time = time.asctime())
                    
                    #write data
                    dst.write(src_data,1)
                    
                    #add colorbar if needed
                    if colortable is not None:
                        dst.write_colormap(1, colortable)
                    
                    #add overviews
                    factors = [2, 4, 8, 16, 32]
                    dst.build_overviews(factors)
            except:
                print('error during raster processing. try again')
                if os.path.exists(file_path):
                    os.remove(file_path)
                continue

            #add entry to json dict
            lGEOJASON['layers'].append({
                'theme': '', #element[0],
                'workspace': domain,
                'file': (os.path.basename(file_path)).split('.')[0],
                'path': os.path.dirname(file_path)
                })
            
            del fRescale
            del colortable
            src_data = None
            del file_path
            del profile
            del fRescaled
        #clean up
        del df
    
    else:
        print('this datatype for a data theme is not forseen. Adapt request to rerun this theme')
        continue
    
# generate final GEOJASON and write to disk
if len(lGEOJASON) != 0:
    #yeah
    with open(path_out_geojason, 'w') as outfile:
        json.dump(lGEOJASON, outfile, indent = 4)

    print('\nAll successful. Time needed: {:10.4f}'.format((time.time() - batch_time)/60))    

else:
    print('no data available to convert to a JSON file')
    print('\nRerun with new specifications. Time needed: {:10.4f}'.format((time.time() - batch_time)/60)) 
