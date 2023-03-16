# -*- coding: utf-8 -*-
'''
RESTRICTIONS
This software is property of VITO and free-of-use. Any results originating from this
script should be referenced with:
"Smets, Buchhorn (2021), Ecosystem Capability Accounting (ENCA) software package, initially
developed in the framework of the PAPBIO project (FED/2018/399-509)."

VITO has no liability for any errors or omissions in the contents of this script.

SYNOPSIS
python3.6 leac.py -e path_to_yaml -v

DESCRIPTION
Processor to create Land Cover Account (LEAC), as part of Ecosystem Capability Accounting
(see http://ecosystemaccounting.net).

PREREQUISITES
Python >= 3.0
SAGA library (The script uses SAGA commands to generate the requested accounts).

MANDATORY INPUTS
   * land cover maps : 2 different dates with same classes
   * administrative polygons : country (L0) and region (L1) level
   * leac look-up table (translate LC classes into consecutive numbers)

AUTHORS
Bruno Smets <bruno.smets@vito.be>

VERSION
5.0 (2021-07-05)
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
import geopandas as gpd
import rasterio
import rasterio.mask
import shapefile
from shapely.geometry import shape
from tqdm import tqdm


os.environ['GDAL_DATA'] = r'/usr/share/gdal'
sys.path.append("./")   # added by cvdh 7/10/2022 to include path to general

from general.params import Parameters
import general.process as process
from general.prepare_web import Raster
from general.prepare_web import Shape

######################################################################################################################
def format_LCC_table(pm, table, path_out):
    #add land cover names, transform to ha, move noChange, calculate % of area, total formation and consumption of land
    table_out = os.path.join(path_out, os.path.splitext(os.path.basename(table))[0] + '_final.csv')
    try:
        df = pd.read_csv(table, sep=',')
        classes = len(df.columns) - 1  #exclude Total column

        #transform to pixels to hectares
        pix2ha = 100/int(pm.run.__dict__['resolution']) *  100/int(pm.run.__dict__['resolution'])
        df = df * 1/pix2ha

        #move no change to separate col/row and sum formation and consumption
        df['No change'] = 0
        df = df.append(pd.Series(name='No change'))
        for i in range (classes-1):
            df.loc[i,'No change'] = df.iloc[i,i]
            df.loc['No change',str(i+1)] = df.iloc[i,i]
            df.iloc[i,i] = 0

        #sum formation and consumption
        c = [x + 1 for x in list(range(classes))]
        c_str = [str(x) for x in c]
        r = list(range(classes))
        df['Total consumption'] = df.loc[:, c_str].sum(axis=1)
        df = df.append(pd.Series(df.loc[r, c_str].sum(axis=0), name='Total formation'))

        #move 'order' : Total consumption/formation -> No change -> Total
        df.index.values[classes] = 'Total'
        c_str.extend(['Total consumption', 'No change', 'Total'])
        df = df[c_str]
        r.extend(['Total formation', 'No change', 'Total'])
        df = df.reindex(r)

        #add percentage of area
        total_area = df['Total'].sum()
        df['% of area'] = df['Total'] / total_area * 100
        df['% of area changed'] = df['Total consumption'] / total_area * 100
        df = df.append(pd.Series(df.loc['Total', :] / total_area * 100, name='% of area'))
        df = df.append(pd.Series(df.loc['Total formation', :] / total_area * 100, name='% of area changed'))

        #TODO replace numbers by class-names and PSCLC codes

        df.to_csv(table_out, sep=',')
        return table_out

    except Exception as e:
        print('Error in formating land cover change table %s' %table)
        print(e)
        raise

######################################################################################################################
def format_LCF_table(pm, table_consumption, table_formation):

    #join consumption & formation flow tables and format
    table_out = os.path.splitext(table_consumption.replace('consumption', 'LCF'))[0]+ '_final.csv'
    try:
        df_c = pd.read_csv(table_consumption, sep=',')
        df_f = pd.read_csv(table_formation, sep=',')
        classes = len(df_c.columns) - 1  #exclude Total column
        #TODO read from lut ?
        flows = 9   #actually 1-7 + 9 is no_change

        #transform to pixels to hectares
        pix2ha = 100/int(pm.run.__dict__['resolution']) *  100/int(pm.run.__dict__['resolution'])
        df_c = df_c * 1/pix2ha
        df_f = df_f * 1/pix2ha

        c = [x + 1 for x in list(range(classes))]
        c_str = [str(x) for x in c]
        r = list(range(flows))

        #create LCF dataframe with number of flows (saga creates empty flow rows to match number of LC lcasses)
        df1 = df_c.iloc[:flows,:]
        #add total consumption (losses) and initial stock
        df1 = df1.append(pd.Series(df1.loc[r[:-1], c_str].sum(axis=0), name='Total consumption of land cover (losses)'))
        df1 = df1.append(pd.Series(df1.loc[r, c_str].sum(axis=0), name='Stock Land Cover yr1'))
        dict_lcf_c={}
        for i in r:
            dict_lcf_c[i] = 'lcf'+str(i+1)+'_c'
        df1 = df1.rename(index=dict_lcf_c)

        df2 = df_f.iloc[:flows, :]
        # add total consumption (losses) and initial stock
        df2 = df2.append(pd.Series(df2.loc[r[:-1], c_str].sum(axis=0), name='Total formation of land cover (gains)'))
        df2 = df2.append(pd.Series(df2.loc[r, c_str].sum(axis=0), name='Stock Land Cover yr2'))
        dict_lcf_f = {}
        for i in r:
            dict_lcf_f[i] = 'lcf' + str(i+1) + '_f'
        df2 = df2.rename(index=dict_lcf_f)

        #merge both flows and add Net change, turnover
        df = pd.concat([df1,df2])

        df.to_csv(table_out, sep=',')
        return table_out

    except Exception as e:
        print('Error in formating land cover flow table %s' %table_out)
        print(e)
        raise

######################################################################################################################
def calc_AREA(pm):
    #function to calculate area per adminstrative polygon and add to vector
    
    keys_available = 'adm0' in pm.leac.leacOut.__dict__ and 'adm1' in pm.leac.leacOut.__dict__ and 'adm_ws12' in pm.leac.leacOut.__dict__
    if (not options.overwrite) and keys_available and os.path.exists(pm.leac.leacOut.adm0) and os.path.exists(pm.leac.leacOut.adm1) and os.path.exists(pm.leac.leacOut.adm_ws12):
        print ("Skip step to calculate admin area sizes, data exists")
        return

    try:
        #copy vector to avoid overwriting input with area extension
        for idx,file_path in enumerate([pm.leac.leacIn.adm0, pm.leac.leacIn.adm1, pm.leac.leacIn.adm_ws12]):
            file = os.path.split(file_path)[1]
            if options.verbose: print("Copying shapefile %s" %file)
            destination = os.path.join(pm.leac.leacOut.root_leac,'temp')
            #we need to copy over all shp shx, mdx, ...
            #for filename in pathlib.Path(os.path.split(file_path)[0]).glob('*'+pm.run.region_short+'_'+str(idx)+'*'):
            found = False
            for filename in pathlib.Path(os.path.split(file_path)[0]).glob('*'+os.path.splitext(file)[0]+'*'):
                #print("Copying %s " % str(filename))
                shutil.copy(str(filename), destination)     #PosixPath transfered in string
                found = True
            if found: input_polygon = os.path.join(destination, file)
            else:
                print('Shapefile {} not found'.format(pm.leac.leacIn.adm_ws12))
                raise
            
            cmd = "saga_cmd shapes_polygons 2"
            cmd = cmd + " -POLYGONS " + input_polygon
            cmd = cmd + " -SCALING " + str(pm.process.scale2ha)
            
            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)
            
            #clean-up the shapefile
            data = gpd.read_file(input_polygon)
            #clean up the shapefile
            cols_to_drop = ["PERIMETER"]
            for colname in cols_to_drop:
                if colname in data.columns:
                    data = data.drop([colname], axis=1)
            cols_to_rename = [{"AREA":"AREA_HA"}]
            for col in cols_to_rename:
                if list(col.keys())[0] in data.columns:
                    data = data.rename(index=str, columns={list(col.keys())[0]:list(col.values())[0]})
            #write out new cleaned MEFF shapefile
            data.to_file(input_polygon, drivers='ESRI Shapefile')
            
            if idx == 0:
                pm.leac.leacOut.adm0 = input_polygon
            elif idx == 1:
                pm.leac.leacOut.adm1 = input_polygon
            elif idx == 2:
                pm.leac.leacOut.adm_ws12 = input_polygon
            #area in ha
        
        #update yaml configuration file
        pm.update(options.config)       #update yaml file to skip re-processing next time if not required
        print("** Administrative polygons prepared ...")
        
        return
    
    except Exception as e:
        print("Erro {}".format(e))
        print(traceback.format_exc())
        sys.exit(-1)

def clip_reclassify(pm):
    #function to clip the land cover maps to Area of Interest (region) and reclassify classes to subsequent numbering scheme

    keys_available = True
    for idx, year in enumerate(pm.run.yearsL):
        lc = 'lc'+str(year)+'_reclass'
        if not (lc in pm.leac.leacOut.__dict__) or not (os.path.exists(pm.leac.leacOut.__dict__[lc])):
            keys_available = keys_available and False

    if (not options.overwrite) and keys_available:
        print ("Skip step to clip & reclassify land cover, data exists")
        return

    web = Raster(pm, options)

    try:
        for idx, year in enumerate(pm.run.yearsL):

            lc = 'lc'+str(year)
            grid_in = pm.leac.leacIn.__dict__[lc]
            file = os.path.splitext(os.path.split(grid_in)[1])[0].replace(pm.run.region_in,pm.run.region_short)
            file = file.replace('.','_')    #SAGA is not able to deal with too many dots in the filename

            #1- clip to AOI

            if 'lut_lc2psclc' in pm.leac.leacIn.__dict__:
                grid_out = os.path.join(pm.leac.leacOut.root_leac,'temp',file)
            else:
                grid_out = os.path.join(pm.leac.leacOut.root_leac,'temp',file)


            cmd = "saga_cmd shapes_grid 7"
            cmd = cmd + " -OUTPUT " + grid_out
            cmd = cmd + " -INPUT " + grid_in
            cmd = cmd + " -POLYGONS " + pm.leac.leacIn.aoi
            '''
            #TODO READ extent from AOI.tif & CHECK INPUT_PROJECTION fits YAML config
            (ulx, uly, lrx, lry) = (-1339545.844, 1421037.780, -1175745.844, 1202857.780)
            cmd = "gdal_translate -of SAGA"
            cmd = cmd + " -projwin " + str(ulx) + " " + str(uly) + " " + str(lrx) + " " + str(lry)
            cmd = cmd + " -a_nodata " + str(0)
            cmd = cmd + " " + grid_in
            cmd = cmd + " " + grid_out+".sdat"
            '''
            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

            #let's now translate to colored geotiff
            ctfile = pm.leac.leacIn.__dict__['lut_ct_lc']  #'/data/nca_vol1/qgis/legend_CGLOPS_NCA_L2-fr.txt'
            scale = [0, 255, 0, 255]  #scale from values to values
            tiffile = web.add_color(os.path.join(pm.leac.leacOut.root_leac,'temp',file+'.sdat'), ctfile, os.path.join(pm.leac.leacOut.root_leac,'stock'), 'Byte', 0, scale)

            #2 - reclassify

            if 'lut_lc2psclc' in pm.leac.leacIn.__dict__:
                #perform first a reclassification to LCEU (PS-CLC) if data source not yet prepared
                grid_out_temp = grid_out
                grid_out = os.path.join(pm.leac.leacOut.root_leac,'temp',file+'_PSCLC')
                cmd = "saga_cmd grid_tools 15"
                cmd = cmd + " -INPUT " + grid_out_temp+'.sdat'
                cmd = cmd + " -RESULT " + grid_out
                cmd = cmd + " -METHOD 3"
                cmd = cmd + " -RETAB_2 " + pm.leac.leacIn.lut_lc2psclc
                cmd = cmd + " -TOPERATOR 1 -F_MIN PSCLC_CD -F_MAX PSCLC_CD -F_CODE PSCLC_RANK"
                
                #run it
                if options.verbose: print("Running command %s" % cmd )
                subprocess.check_call(cmd, shell=True)
            
            grid_out_reclassified = os.path.join(pm.leac.leacOut.root_leac,'temp',file+'_reclassified')
            
            cmd = "saga_cmd grid_tools 15"
            cmd = cmd + " -INPUT " + grid_out+'.sdat'
            cmd = cmd + " -RESULT " + grid_out_reclassified
            cmd = cmd + " -METHOD 3"
            cmd = cmd + " -RETAB_2 " + pm.leac.leacIn.lut_lc
            cmd = cmd + " -TOPERATOR 1 -F_MIN PSCLC_CD -F_MAX PSCLC_CD -F_CODE PSCLC_RANK"
            
            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)
            
            #SAGA sometimes rounds the XMIN or YMIN position in the last digits differently. Need to patch the rounding
            process.fix_GridMeta(grid_out+'.sgrd', grid_out_reclassified+'.sgrd')
            
            '''if idx == 0:
                pm.leac.leacOut.lc1 = grid_out + '.sdat'
                pm.leac.leacOut.lc1_reclass = grid_out_reclassified + '.sdat'
            elif idx == 1:
                pm.leac.leacOut.lc2 = grid_out + '.sdat'
                pm.leac.leacOut.lc2_reclass = grid_out_reclassified + '.sdat'
            '''
            pm.leac.leacOut.__dict__[lc] = grid_out + '.sdat'
            pm.leac.leacOut.__dict__[lc+'_reclass'] = grid_out_reclassified + '.sdat'

        #update yaml configuration file
        pm.update(options.config)       #update yaml file to skip re-processing next time if not required
        print("** Land cover clipped and reclassified ...")
        
        return 

    except Exception as e:
        print("Erro {}".format(e))
        print(traceback.format_exc())
        sys.exit(-1)

def calc_lc_changes(pm):
    #function to calculate the land cover changes by creating tabular output and change map

    keys_available = True
    for idx, year in enumerate(pm.run.yearsL[:-1]):  #minus 1 as change maps require tuples
        lc = 'lcc' + str(year)+'-'+str(pm.run.yearsL[idx+1])
        if not (lc in pm.leac.leacOut.__dict__) or not (os.path.exists(pm.leac.leacOut.__dict__[lc])):
            keys_available = keys_available and False
        lc_tab = 'tab_lcc' + str(year)+'-'+str(pm.run.yearsL[idx+1])
        if not (lc_tab in pm.leac.leacOut.__dict__) or not (os.path.exists(pm.leac.leacOut.__dict__[lc_tab])):
            keys_available = keys_available and False

    if (not options.overwrite) and keys_available:
        print ("Skip step to calculate land cover changes, data exists")
        return

    web = Raster(pm, options)

    for idx, year in enumerate(pm.run.yearsL[:-1]):
        lcc = 'lcc' + str(year)+'-'+str(pm.run.yearsL[idx+1])
        tab_lcc = 'tab_lcc' + str(year)+'-'+str(pm.run.yearsL[idx+1])
        lc1_reclass = 'lc'+str(pm.run.yearsL[idx])+'_reclass'
        lc2_reclass = 'lc'+str(pm.run.yearsL[idx+1])+'_reclass'
        try:
            grid_out = os.path.join(pm.leac.leacOut.root_leac,'temp','LEAC-change_'+pm.run.region_short+'_'+str(pm.run.yearsL[idx])+'-'+str(pm.run.yearsL[idx+1])+'_'+str(pm.run.resolution)+'m_EPSG'+str(pm.run.projection))
            table_out = grid_out +'.csv'

            cmd = "saga_cmd grid_analysis 13"
            cmd = cmd + " -INPUT " + pm.leac.leacOut.__dict__[lc1_reclass]
            cmd = cmd + " -INPUT2 " + pm.leac.leacOut.__dict__[lc2_reclass]
            cmd = cmd + " -RESULTGRID " + grid_out
            cmd = cmd + " -RESULTTABLE " + table_out
            cmd = cmd + " -MAXNUMCLASS " + str(pm.process.maxLcClasses)

            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

            #add output to yaml
            pm.leac.leacOut.__dict__[lcc] = grid_out + '.sdat'
            pm.leac.leacOut.__dict__[tab_lcc] = table_out

            #post-process output data
            # let's now translate to colored geotiff
            ctfile = None
            scale = [0, 255, 0, 255]  # scale from values to values
            tiffile = web.add_color(os.path.join(pm.leac.leacOut.root_leac, 'temp', grid_out + '.sdat'), ctfile, os.path.join(pm.leac.leacOut.root_leac, 'flow'), 'Byte', 0, scale)

            #format table : convert pixels to ha & TODO move no_change in separate col/row
            table_out_formatted = format_LCC_table(pm, table_out, path_out = os.path.join(pm.leac.leacOut.root_leac, 'flow'))
            pm.leac.leacOut.__dict__[tab_lcc] = table_out_formatted

            #update yaml configuration file
            pm.update(options.config)       #update yaml file to skip re-processing next time if not required
            print("** LEAC change matrix ready ...")

        except Exception as e:
            print("Erro {}".format(e))
            print(traceback.format_exc())
            sys.exit(-1)

    return

def calc_lc_flows(pm):
    #function to calculate the land cover change flows (consumption and formation)
    keys_available = True
    for idx, year in enumerate(pm.run.yearsL[:-1]):  # minus 1 as change maps require tuples
        lc = 'lcc_' + str(year)+'-'+str(pm.run.yearsL[idx+1]) + '_4digit'
        if not (lc in pm.leac.leacOut.__dict__) or not (os.path.exists(pm.leac.leacOut.__dict__[lc])):
            keys_available = keys_available and False
        lcf = 'lcf_' + str(year)+'-'+str(pm.run.yearsL[idx+1])
        if not (lcf in pm.leac.leacOut.__dict__) or not (os.path.exists(pm.leac.leacOut.__dict__[lcf])):
            keys_available = keys_available and False

    if (not options.overwrite) and keys_available:
        print ("Skip calculate land cover flow, data exists")
        return   #TODO do not' return but goto step C

    web = Raster(pm, options)

    for idx, year in enumerate(pm.run.yearsL[:-1]):
        try:
            #A. combine the 2 input grids into 4-digit number (temporary step)
            lc1 = 'lc' + str(pm.run.yearsL[idx])
            lc2 = 'lc' + str(pm.run.yearsL[idx + 1])
            lcc = 'lcc_' + str(pm.run.yearsL[idx]) + '-' + str(pm.run.yearsL[idx + 1])
            lcf = 'lcf_' + str(pm.run.yearsL[idx]) + '-' + str(pm.run.yearsL[idx + 1])
            grid_4digits = os.path.join(pm.leac.leacOut.root_leac,'temp', 'LEAC-change_'+pm.run.region_short+'_'+str(pm.run.yearsL[idx])+'-'+str(pm.run.yearsL[idx+1])+'_'+str(pm.run.resolution)+'m_EPSG'+str(pm.run.projection)+'_4digits')
            cmd = 'saga_cmd grid_calculus 1'
            if pm.run.level == 2:
                cmd = cmd + ' -FORMULA="(g1*1000)+g2"'   #level-1 * 100, but level-2 * 1000
                cmd = cmd + ' -TYPE 5'                   #need type-5 to cope with * 1000 (=unsigned 4 bit integer)
            else:
                cmd = cmd + ' -FORMULA="(g1*100)+g2"'  # level-1 * 100, but level-2 * 1000
                cmd = cmd + ' -TYPE 3'  # need type-5 to cope with * 1000 (=unsigned 4 bit integer)
            cmd = cmd + ' -GRIDS "' + pm.leac.leacOut.__dict__[lc1] +';'+ pm.leac.leacOut.__dict__[lc2] + '"'
            cmd = cmd + ' -RESULT ' + grid_4digits

            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

            pm.leac.leacOut.__dict__[lcc] = grid_4digits + '.sdat'

            #B. reclassify the 4-digit number into a land cover flow number

            lcflow_1digits = os.path.join(pm.leac.leacOut.root_leac,'temp', 'LEAC-flow_'+pm.run.region_short+'_'+str(pm.run.yearsL[idx])+'-'+str(pm.run.yearsL[idx+1])+'_'+str(pm.run.resolution)+'m_EPSG'+str(pm.run.projection)+'_1digits')
            cmd = 'saga_cmd grid_tools 15'
            cmd = cmd + ' -INPUT ' + pm.leac.leacOut.__dict__[lcc]
            cmd = cmd + ' -RESULT ' + lcflow_1digits
            cmd = cmd + ' -METHOD 3'
            cmd = cmd + ' -RETAB_2 ' + pm.leac.leacIn.lut_lcflows
            cmd = cmd + ' -TOPERATOR 1 -F_MIN LC_CHANGE -F_MAX LC_CHANGE -F_CODE ID_lcflows'

            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

            #SAGA sometimes rounds the XMIN or YMIN position in the last digits differently. Need to patch the rounding
            process.fix_GridMeta(os.path.splitext(pm.leac.leacOut.__dict__[lc1])[0]+'.sgrd', lcflow_1digits+'.sgrd')

            pm.leac.leacOut.__dict__[lcf] = lcflow_1digits + '.sdat'

            # post-process output data
            # let's now translate to colored geotiff
            ctfile = pm.leac.leacIn.__dict__['lut_ct_lcf']
            scale = [0, 255, 0, 255]  # scale from values to values
            tiffile = web.add_color(lcflow_1digits+'.sdat', ctfile, os.path.join(pm.leac.leacOut.root_leac, 'flow'), 'Byte', 0, scale)

        except Exception as e:
            print("Erro {}".format(e))
            print(traceback.format_exc())
            sys.exit(-1)
    
    #C. Calculate the consumption (ref year) and formation (new year) raster + table
    keys_available = True
    for idx, year in enumerate(pm.run.yearsL[:-1]):  # minus 1 as change maps require tuples
        lc_consumption = 'lc' + str(year)+'-'+str(pm.run.yearsL[idx+1]) + '_consumption'
        if not (lc_consumption in pm.leac.leacOut.__dict__) or not (
        os.path.exists(pm.leac.leacOut.__dict__[lc_consumption])):
            keys_available = keys_available and False
        lc_formation = 'lc' + str(year)+'-'+str(pm.run.yearsL[idx+1]) + '_formation'
        if not (lc_formation in pm.leac.leacOut.__dict__) or not (
        os.path.exists(pm.leac.leacOut.__dict__[lc_formation])):
            keys_available = keys_available and False

    if (not options.overwrite) and keys_available:
        print ("Skip calculate leac consumption & formation flows, data exists")
        return

    for idx, year in enumerate(pm.run.yearsL[:-1]):
        try:
            lcf = 'lcf_' + str(pm.run.yearsL[idx]) + '-' + str(pm.run.yearsL[idx + 1])
            for idy, grid_in in enumerate([pm.leac.leacOut.__dict__['lc'+str(pm.run.yearsL[idx])], pm.leac.leacOut.__dict__['lc'+str(pm.run.yearsL[idx+1])]]):

                if idy == 0:
                    account = '_'+str(pm.run.yearsL[idx])+'_consumption'
                else:
                    account = '_'+str(pm.run.yearsL[idx+1])+'_formation'
                grid_out = os.path.join(pm.leac.leacOut.root_leac,'temp', 'LCF'+account+'_'+pm.run.region_short+'_'+str(pm.run.yearsL[idx])+'-'+str(pm.run.yearsL[idx+1])+'_'+str(pm.run.resolution)+'m_EPSG'+str(pm.run.projection))
                cmd = 'saga_cmd grid_calculus 1'
                cmd = cmd + ' -FORMULA="(g1*10000)+g2"'
                cmd = cmd + ' -TYPE 5'
                cmd = cmd + ' -GRIDS "' + pm.leac.leacOut.__dict__['lcf_' + str(pm.run.yearsL[idx]) + '-' + str(pm.run.yearsL[idx + 1])] +';'+ grid_in +'"'
                cmd = cmd + ' -RESULT ' + grid_out

                #run it
                if options.verbose: print("Running command %s" % cmd )
                subprocess.check_call(cmd, shell=True)

                pm.leac.leacOut.__dict__[lcf+account] = grid_out + '.sdat'
        except Exception as e:
            print("Erro {}".format(e))
            print(traceback.format_exc())
            sys.exit(-1)
                
    #D. Calculate cross-table stock-flows for consumption and formation
    keys_available = True
    for idx, year in enumerate(pm.run.yearsL[:-1]):  # minus 1 as change maps require tuples
        lc_consumption = 'tab_lcf' + str(idx + 1) + '_consumption'
        if not (lc_consumption in pm.leac.leacOut.__dict__) or not (
                os.path.exists(pm.leac.leacOut.__dict__[lc_consumption])):
            keys_available = keys_available and False
        lc_formation = 'tab_lcf' + str(idx + 1) + '_formation'
        if not (lc_formation in pm.leac.leacOut.__dict__) or not (
                os.path.exists(pm.leac.leacOut.__dict__[lc_formation])):
            keys_available = keys_available and False

    if (not options.overwrite) and keys_available and os.path.exists(pm.leac.leacOut.consumption_lcf):
        print ("Skip step D to calculate land cover cross tables, data exists")
        return

    for idx, year in enumerate(pm.run.yearsL[:-1]):
        try:
            for idy, grid_in in enumerate([pm.leac.leacOut.__dict__['lc'+str(pm.run.yearsL[idx])+'_reclass'], pm.leac.leacOut.__dict__['lc'+str(pm.run.yearsL[idx+1])+'_reclass']]):

                if idy == 0:
                    account = 'LEAC_consumption_'+str(pm.run.yearsL[idx])
                else:
                    account = 'LEAC_formation_'+str(pm.run.yearsL[idx+1])
                grid_out = os.path.join(pm.leac.leacOut.root_leac,'flow', account+'_'+pm.run.region_short+'_'+str(pm.run.yearsL[idx])+'-'+str(pm.run.yearsL[idx+1])+'_'+str(pm.run.resolution)+'m_EPSG'+str(pm.run.projection))
                table_out = grid_out+'.csv'

                cmd = 'saga_cmd grid_analysis 13'
                cmd = cmd + ' -INPUT ' + pm.leac.leacOut.__dict__['lcf_' + str(pm.run.yearsL[idx]) + '-' + str(pm.run.yearsL[idx + 1])]
                cmd = cmd + ' -INPUT2 ' + grid_in
                cmd = cmd + ' -RESULTGRID ' + grid_out
                cmd = cmd + ' -RESULTTABLE ' + table_out
                cmd = cmd + ' -MAXNUMCLASS ' + str(pm.process.maxLcClasses)

                #run it
                if options.verbose: print("Running command %s" % cmd )
                subprocess.check_call(cmd, shell=True)

                pm.leac.leacOut.__dict__[account] = grid_out + '.sdat'
                pm.leac.leacOut.__dict__['tab_'+account] = table_out

            #format table and convert to hectares
            format_LCF_table(pm, pm.leac.leacOut.__dict__['tab_'+'LEAC_consumption_'+str(pm.run.yearsL[idx])], pm.leac.leacOut.__dict__['tab_'+'LEAC_formation_'+str(pm.run.yearsL[idx+1])])
            #TODO ADD ENTRY IN YML

            #update yaml configuration file
            pm.update(options.config)       #update yaml file to skip re-processing next time if not require            print("** LEAC flows on level-0 ready ...")

        except Exception as e:
            print("Erro {}".format(e))
            print(traceback.format_exc())
            sys.exit(-1)

    return

'''
def calc_lc_flows_region_new(pm, path_admin, ID_FIELD='NAME_0'):

    # 1. create an empty dataframe for statistic results
    LC_classes = [0,11,12,13,14,21,31,40,41,50,60,61,70,80,90,200]
    df = pd.DataFrame(columns=['id','name']+LC_classes)

    # 2. calculate statistics per admin
    with shapefile.Reader(path_admin) as oVector, rasterio.open(path_raster_vrt) as src_raster:
        # get field names for shapeile attributes
        fieldnames = [f[0] for f in oVector.fields]
        # filter out deletion flag
        while 'DeletionFlag' in fieldnames: fieldnames.remove('DeletionFlag')
        # check that the required files names for this shapefiles are in
        if ID_FIELD in fieldnames:
            pass
        else:
            raise RuntimeError('!!! The needed attribute columns for this shapefile: ' + str(path_admin) + ' are not present.')

        # check resolution
        if int(src_raster.transform.a) == 100:
            m2_2ha = 1 / (100. * 100.)
            pix2ha = 1 / (1 * 1)
        else:
            raise RuntimeError('SHAPE statistics is only supported for rasters in 100x100m = 1ha')

        #some stuff for progress bar
        max_counter = len(oVector)
        #ini tqm
        t = tqdm(total = int(max_counter))

        #create empty dataframe for statistical units
        row_data = []
        count_shapes = 0

        # iterate over SELU polygons and extract raster data
        for shapeRec in oVector.iterShapeRecords():
            count_shapes += 1
            # first generate the record_dic
            dRecord = dict(zip(fieldnames, shapeRec.record))
            dFeature = shapeRec.shape

            #read out the raster data for this polygon bbox and automatically create mask array
            path_raster = pm.leac.leacOut.__dict__['lc'+str(2005)]
            with rasterio.open(path_raster) as src:
                mask_bounds = tuple(dFeature.bbox)
                src_bounds = src.bounds
                if rasterio.coords.disjoint_bounds(src_bounds, mask_bounds):
                    raise RuntimeError('vector file is outside the area of the raster file')
                window = src.window(*mask_bounds)
                aData = src.read(1, window=window, masked=True)
                aData_transform = src.window_transform(window)
                aData_bounds = aData_transform * (0, aData.shape[0]) + aData_transform * (aData.shape[1], 0)
                aData_profile = src.profile
                
                dStatistics = dict(zip(*np.unique(aData, return_counts=True)))
                del dstatistics[255]
                #TODO check if pixel is 100m to get area in ha
                dStatistics['id'] = dRecord['id']
                dStatistics['name'] = dRecrods['name']
                df = df.append(dStatistics, ignore_index=True)
                
            #create PNG image
            #cmd = """gdal_translate -of PNG -expand rgba -co 'WORLDFILE=YES" """
            #cmd = cmd + '{} {}'.format(path_raster_masked, path_out)
        
        df['total'] = df.sum(axis=1, numeric_only=True)
        csv_path = os.path.join(pm.leac.leacOut.root_leac, 'report', 'LEAC_ADM1_'+pm.run.region_short+'_2005.csv')
        df.to_csv(csv_path, index_label = 'NAME')
'''

def calc_lc_flows_region(pm, admin):
    #function to calculate LEAC per administrative zone
    
    try:
        #A. Copy GAUL shapes to prepare ingestion of statistics
        for filename in pathlib.Path(os.path.split(admin)[0]).glob(os.path.splitext(os.path.split(admin)[1])[0]+'*'):
            shutil.copy(str(filename), os.path.join(pm.leac.leacOut.root_leac, 'report',os.path.basename(filename).replace('LEAC','LEAC_stock')))    #PosixPath transfered in string for LC stocks
            shutil.copy(str(filename), os.path.join(pm.leac.leacOut.root_leac, 'report',os.path.basename(filename).replace('LEAC','LEAC_consumption')))     #PosixPath transfered in string for LC consumption
            shutil.copy(str(filename), os.path.join(pm.leac.leacOut.root_leac, 'report',os.path.basename(filename).replace('LEAC','LEAC_formation')))     #PosixPath transfered in string for LC formation
            if os.path.splitext(os.path.basename(filename))[1] == '.shp':
                stocks = os.path.join(pm.leac.leacOut.root_leac, 'report',os.path.basename(filename).replace('LEAC','LEAC_stock'))
                consumption = os.path.join(pm.leac.leacOut.root_leac, 'report',os.path.basename(filename).replace('LEAC','LEAC_consumption'))
                formation = os.path.join(pm.leac.leacOut.root_leac, 'report',os.path.basename(filename).replace('LEAC','LEAC_formation'))
    except Exception as e:
        print("Erro {}".format(e))
        print(traceback.format_exc())
        sys.exit(-1)
    
    for idx, year in enumerate(pm.run.yearsL):
        try:
            lut_lcc = pm.leac.leacIn.lut_lcc.replace('2000',year).replace('2015',pm.run.yearsL[idx+1])  #TODO optimize to avoid too many lut tables
            #B. Calculate stocks per admin
            cmd = 'saga_cmd shapes_grid 18'
            cmd = cmd + ' -POLYGONS ' + stocks
            cmd = cmd + ' -GRID ' + pm.leac.leacOut.__dict__['lc'+str(year)]
            cmd = cmd + ' -METHOD 0 -GRID_VALUES 1'
            cmd = cmd + ' -GRID_LUT ' + lut_lcc
            cmd = cmd + ' -GRID_LUT_MIN PSCLC_Code -GRID_LUT_MAX PSCLC_Code -GRID_LUT_NAM LC'+str(year)

            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

        except Exception as e:
            print("Erro {}".format(e))
            print(traceback.format_exc())
            sys.exit(-1)

    for idx, year in enumerate(pm.run.yearsL[:-1]):
        try:
            print('Calculate flow for year {}'.format(year))
            lut_lcc = pm.leac.leacIn.lut_lcc.replace('2000',year).replace('2015',pm.run.yearsL[idx+1])  #TODO optimize to avoid too many lut tables
            # C. Calculate flows per admin
            cmd = 'saga_cmd shapes_grid 18'
            cmd = cmd + ' -POLYGONS ' + stocks
            cmd = cmd + ' -GRID ' + pm.leac.leacOut.__dict__['lcf_'+str(year)+'-'+str(pm.run.yearsL[idx+1])]
            cmd = cmd + ' -METHOD 0 -GRID_VALUES 1'
            cmd = cmd + ' -GRID_LUT ' + lut_lcc
            cmd = cmd + ' -GRID_LUT_MIN lcf_ID -GRID_LUT_MAX lcf_ID -GRID_LUT_NAM lcf_Code'

            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

            #no need to add in yaml - all calculates stored in (copied shapes)

        except Exception as e:
            print("Erro {}".format(e))
            print(traceback.format_exc())
            sys.exit(-1)

    for idx, year in enumerate(pm.run.yearsL[:-1]):
        try:
            print('Calculate consumption & formation flow for year {}'.format(year))
            #D. Calculate flows for consumption and formation

            cmd = 'saga_cmd shapes_grid 18'
            cmd = cmd + ' -POLYGONS ' + consumption
            cmd = cmd + ' -GRID ' + pm.leac.leacOut.__dict__['lcf_'+str(year)+'-'+str(pm.run.yearsL[idx+1])+'_'+str(year)+'_consumption']
            cmd = cmd + ' -METHOD 0 -GRID_VALUES 1'
            cmd = cmd + ' -GRID_LUT ' + pm.leac.leacIn.lut_lcflow_C
            cmd = cmd + ' -GRID_LUT_MIN CONSUMPTIO -GRID_LUT_MAX CONSUMPTIO -GRID_LUT_NAM CD_CONSO_long'

            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

            cmd = 'saga_cmd shapes_grid 18'
            cmd = cmd + ' -POLYGONS ' + formation
            cmd = cmd + ' -GRID ' + pm.leac.leacOut.__dict__['lcf_'+str(year)+'-'+str(pm.run.yearsL[idx+1])+'_'+str(pm.run.yearsL[idx+1])+'_formation']
            cmd = cmd + ' -METHOD 0 -GRID_VALUES 1'
            cmd = cmd + ' -GRID_LUT ' + pm.leac.leacIn.lut_lcflow_F
            cmd = cmd + ' -GRID_LUT_MIN FORMATION -GRID_LUT_MAX FORMATION -GRID_LUT_NAM CD_FORMA_long'

            #run it
            if options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)

            #update yaml configuration file
            pm.update(options.config)       #update yaml file to skip re-processing next time if not required
            print("** LEAC flows on level-1 ready ...")

        except Exception as e:
            print("Erro {}".format(e))
            print(traceback.format_exc())
            sys.exit(-1)

####################################################################################################
# workflow to create LEAC account
def create_LEAC(pm):
    
    try:
        #'''
        #1. Calculate the area in administrative polygons for later use
        calc_AREA(pm)
        print("** ADMIN preparation ready ...\n\n")
        
        #2. Clip land cover maps for region and reclassify
        clip_reclassify(pm)
        print("** LANDCOVER clipped ready ...\n\n")
        
        #3. Calculate land cover change in ha
        #options.overwrite = True
        calc_lc_changes(pm)
        print("** LANDCOVER changes calculated  ...\n\n")
        
        #4. Calculate land cover stocks and flows on total area_of_interest
        calc_lc_flows(pm)
        print("** LANDCOVER flows calculated ...\n\n")
        
    except Exception as e:
        print("Erro {}".format(e))
        print(traceback.format_exc())

######################################################################################################################
def main():
    
    #read yaml configuration file
    pm = Parameters(options.config)  
            
    #create leac account
    create_LEAC(pm)
    
    print("LEAC account ready for %s" % pm.run.region_long)

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
            
        if options.verbose: print('START OF MODULE: ENCA LEAC')
        if options.verbose: print(time.asctime())
        # call main function - "options" object is set to global object
        main()
        #Pparameter, sSuccess = main()
        if options.verbose: print('START OF MODULE: ENCA LEAC')
        if options.verbose: print(time.asctime())
    except:
        traceback.print_stack()