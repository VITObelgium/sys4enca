'''
Prototype script to create NLEP account.

Required inputs:
   * land cover maps : 2 different dates with same classes
   * administrative polygons : country (L0) and region (L1) level
   * leac look-up table (translate LC classes into consecutive numbers)

The script uses SAGA commands to generate the requested accounts.

Created on Oct 17, 2019

@author: smetsb
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
import logging

os.environ['GDAL_DATA'] = r'/usr/share/gdal'
import rasterio
from rasterio.windows import Window
import numpy as np
import geopandas as gpd
import pandas as pd

from enca.infra.gbli import GBLI
from enca.infra.naturalis import NATURALIS
from enca.infra.lfi import LFI, Catchment, OSM
from enca.framework.errors import Error
#from general.params import Parameters
from enca.framework.geoprocessing import block_window_generator, number_blocks #, adding_stats, count

logger = logging.getLogger(__name__)


######################################################################################################################
def create_gbli(gbli):

    keys_available = True

    #for easy typing
    years = gbli.years


    try:
        for year in years:
            if os.path.exists(gbli.leac_gbli_sm[year]):
                continue
            logger.info("rate each land cover class on greeness")
            gbli.reclassify_PSCLC(year, nodata = 0)
            # "smooth greeness maps"
            gbli.gaussian_smooth(year)

            # post-process output data
            # let's now translate to colored geotiff
            #ctfile = '/data/nca_vol1/qgis/legend_gbli.txt'
            #scale = [0, 255, 0, 255]  # scale from values to values
            #tiffile = web.add_color(gbli_sm , ctfile, os.path.join(pm.nlep.nlepOut.root_nlep, 'stock'),'Byte', 0, scale)


        #now calculate the GBLI difference between two consequetive years (doesn't seem to be necessary to be consec)
        for year in years[1:]:
            if os.path.exists(gbli.leac_gbli_diff[year]):
                continue
            logger.info("calculate difference map")
            gbli.diff_gbli(year)

            # post-process output data
            # let's now translate to colored geotiff
            ctfile = '/data/nca_vol1/qgis/legend_gbli_change_2.txt'
            scale = [0, 255, 0, 255]  # scale from values to values
            #tiffile = web.add_color(gbli_diff_sm + '.sdat', ctfile, os.path.join(pm.nlep.nlepOut.root_nlep, 'flow'),'Byte', 0, scale)

        logger.info("GBLI calculated")
        return

    except Error as e:
        raise Error(e)

def create_naturalis(naturalis):

    if os.path.exists(naturalis.sm):
        logger.info("Skip NATURALIS calculation, data exists")
        return
    elif os.path.exists(naturalis.nosm):
        logger.info("Naturalis raster already prepared.")
    else:
        #"rasterize protected area map"
        #can't seem to fnd original naturalis
        naturalis.nosm = naturalis.naturalis
        #naturalis.grid_PA()
        pass

    #"smooth naturalis map"  #TODO remove limit of mandating sdat input (needed for smooth via saga)
    naturalis_sm = naturalis.smooth_PA()


def create_lfi(lfi):

    #lcclass = 10 #urban
    #lcname = 'NoUrb'  #inverse, so NoUrb (an) - limit to 5 chars to add 4 chars for year plus underscore

    #options.overwrite = False
    #1. prepare roads&railways (OSM) layer -> check if input OK, no need to level here
    osm = OSM(lfi.runObject)
    if os.path.exists(osm.merged_RR_inversed):
        logger.info("Skip preparation of merged roads & railways: %s exists" %osm.merged_RR_inversed)
    else:
        if not os.path.exists(osm.merged_trunkroads_railways_inv):
            #TODO if not yet available, merge roads and rails
            osm.inverse_RR()
        osm.vectorize_RR()


    for basin in lfi.catchments.keys():
        #2 create lfi catchment per basin
        if os.path.exists(lfi.catchments_processed[basin]):
            logger.info("Skip preparation of catchment: %s exists" %lfi.catchments_processed[basin])
        else:
            catch = Catchment(lfi.runObject, basin)
            catch.addArea()
            logger.info("catchment imported %s" %catch.catchment)


        #"3. intersect catchment_level with OSM -> output is mesh fragmentation layer for basin level"
        if os.path.exists(lfi.lfi_mesh[basin]):
            logger.info("Skip preparation of mesh_intersect: %s exists" %lfi.lfi_mesh[basin])
        else:
            lfi.intersect_Catchment_OSM(basin,osm.merged_RR_inversed)

        #"4. calculate the fragmentation statistics for given land cover class per year for basin level"

        for idx, year in enumerate(lfi.years):
            if (os.path.exists(lfi.lfi_meff_hybas[basin][year])):
                logger.info(f"Skip FRAGMEFF calculation for {year}, data exists")
                continue

            try:

                #now calculate fragmentation per year
                lc = lfi.lcname+'_'+str(year)

                #create inverse (exclusion) raster mask for given class
                lfi.intersect_LCclass(year,lcName=lc)

                #calculate the meshes (sum of masked pixels), results stored in lfi_mesth_level shapefile
                lfi.calc_mesh(year, basin)

                #calculate the fragmentation index
                lfi.calc_meff(year, basin, lc)

                #rasterize fragmentation index, need pixel based for NLEP (frag mef data is in catchemtn shape
                lfi_frag_meff_raster = lfi.rasterize_MEFF(year,basin)

            except Error as e:
                raise Error(e)

##################
def join_lfi(lfi):


    for idx, year in enumerate(lfi.years):
        if os.path.exists(lfi.lfi_meff[year]):
            print (f"Skip FRAGMEFF join, {lfi.lfi_meff[year]} exists")
            continue

        try:

            print('\n*** Processing year ' + str(year) + ' : ' + str(idx+1) + '/' + str(len(lfi.years)))

            with rasterio.open(lfi.lfi_meff_hybas[lfi.basins[-1]][year]) as ds_open:
                src_profile =ds_open.profile
                new_profile = src_profile.copy()

            new_profile.update(driver='GTiff', dtype=np.float32, nodata=-1, compress='lzw')
            block_shape =(2048,2048)
            nbr_blocks = number_blocks(src_profile, block_shape)

            with rasterio.open(lfi.lfi_meff[year], 'w', **dict(new_profile)) as ds_out:
                for idx, (_, window) in enumerate(block_window_generator(block_shape, ds_open.height, ds_open.width)):
                    print ('***** Processing block: ' + str(idx + 1) + '/' + str(nbr_blocks))
                    #calc shape of block
                    window = Window.from_slices(rows=window[0],cols=window[1])
                    aData = np.zeros((len(lfi.basins), window.height, window.width),dtype=float)

                    for i, basin in enumerate(lfi.basins):
                        # merge fragmeff levels, 50% hybas_12, 30% hybas_8, 20% hybas_6
                        with rasterio.open(lfi.lfi_meff_hybas[basin][year]) as src:
                            aData[i,:,:] = src.read(window=window)

                    aData[aData==src_profile['nodata']]= 0.0  #urban only area's should be set to highest fragmentation
                    aOut = aData[0,:,:]*0.2 + aData[1,:,:]*0.3 + aData[2,:,:]*0.5
                    ds_out.write(aOut.astype(rasterio.float32), 1, window=window)

                    # free
                    aData = None


        except Error as e:
            os.unlink(lfi.lfi_meff[year])
            raise Error(e)

##################
def calc_nlep(runObject):

    keys_available = True
    block_shape = (4096, 4096)

    for idx, year in enumerate(runObject.years):
        if os.path.exists(runObject.nlep[year]):
            print (f"Skip NLEP calculation for {year}, data exists")
            continue

        try:
            #first create NLEP per year
            gbli_in = runObject.leac_gbli_sm[year]
            frag_meff_in = runObject.lfi_meff[year]
            naturalis_in = runObject.naturalis_sm

            #calculate NLEP
            grid_out = runObject.nlep[year]   #limit of 6 characters, SAGA will attribute ' (xxx' to limit of 10

            with rasterio.open(gbli_in, 'r') as gbli_in_open , \
                    rasterio.open(naturalis_in, 'r') as naturalis_open, \
                    rasterio.open(frag_meff_in, 'r') as meff_open:
                profile = gbli_in_open.profile
                with rasterio.open(grid_out, 'w', **dict(profile, driver='GTiff', dtype=np.float32)) as ds_out:
                    for _, window in block_window_generator(block_shape, gbli_in_open.height, gbli_in_open.width):
                        aGblu = gbli_in_open.read(1, window=window, masked=True)
                        aNaturalis = naturalis_open.read(1, window=window, masked=True)
                        aMeff = meff_open.read(1, window=window, masked=True)

                        output  = aGblu * aNaturalis * aMeff /100. #TODO CHECK JLW GBLI 1-100 or in%
                        ds_out.write(output, window=window, indexes=1)
        except Error as e:
            raise Error(e)

    #now create NLEP CHANGE map
    for idx, year in enumerate(runObject.years[1:]):
        if os.path.exists(runObject.clep[year]):
            print (f"Skip CLEP calculation for {year}-{runObject.years[idx]} , data exists")
            continue
        try:
            grid_out = runObject.clep[year]
            prev_year = runObject.years[idx]

            with rasterio.open(runObject.nlep[prev_year], 'r') as A_open , \
                    rasterio.open(runObject.nlep[year], 'r') as B_open:
                profile = A_open.profile
                with rasterio.open(grid_out, 'w', **dict(profile, driver='GTiff')) as ds_out:
                    for _, window in block_window_generator(block_shape, A_open.height, A_open.width):
                        A = A_open.read(1, window=window, masked=True)
                        B = B_open.read(1, window=window, masked=True)
                        #seems te me a bit strange that this is A- B and not B-A?
                        ds_out.write(A-B, window=window, indexes=1)



        except Error as e:
            raise Error(e)

##################
#Not used anymore?, to be removed?
def calc_nlep_admin(pm, admin, region):
    #calculate NLEP per region inside level-0

    keys_available = True
    for idx, year in enumerate(pm.run.yearsL[:-1]):
        nlep_stats = 'nlep_stats_' +str(region) + '_' + str(idx)
        if not (nlep_stats in pm.nlep.nlepOut.__dict__) or not (os.path.exists(pm.nlep.nlepOut.__dict__[nlep_stats])):
            keys_available = keys_available and False

    if (not options.overwrite) and keys_available:
        logger.info("Skip NLEP statistics, data exists")
        return

    for idx, year in enumerate(pm.run.yearsL[:-1]):
        nlep_stats = 'nlep_stats_' + str(region) + '_' + str(idx)
        try:
            pm.nlep.nlepOut.__dict__[nlep_stats] = None   #ensure a clean shapefile is copied
            #A. Copy shapefile to keep source clean
            for filename in pathlib.Path(os.path.split(admin)[0]).glob(os.path.splitext(os.path.split(admin)[1])[0]+'*'):
                shutil.copy(str(filename), os.path.join(pm.nlep.nlepOut.root_nlep_admin,'NLEP_'+ (os.path.basename(os.path.splitext(str(filename))[0])+ '_'+str(idx+1))+( os.path.splitext(str(filename))[1])) )     #PosixPath transfered in string for LC stocks
                if os.path.splitext(str(filename))[1] == '.shp':
                    pm.nlep.nlepOut.__dict__[nlep_stats] = os.path.join(pm.nlep.nlepOut.root_nlep_admin,'NLEP_'+ (os.path.basename(os.path.splitext(str(filename))[0])+ '_'+str(idx+1))+ (os.path.splitext(str(filename))[1]) )
        except:
            print("Error preparing regional shape files" )
            sys.exit(-1)

        try:
            if pm.nlep.nlepOut.__dict__[nlep_stats] is None:
                print("ERROR: no shapefile found for {}".format(admin))
                sys.exit(-1)

            #B. Calculate NLEP per admin (nlep2000, nlep2015 and nlep_change)
            rasters = [pm.nlep.nlepOut.__dict__['nlep'+str(idx+1)],
                       pm.nlep.nlepOut.__dict__['nlep'+str(idx+2)],
                       pm.nlep.nlepOut.__dict__['nlep'+str(idx+1)+'_change']]
            shapes = pm.nlep.nlepOut.__dict__[nlep_stats]
            stats = [count,np.sum, np.mean]

            adding_stats(rasters, shapes, shapes, stats)


            #clean up the shapefile
            # (CEL = count and provides area in ha (if pix2ha = 1), (MEA = mean value so the NLEP index, (SUM = sum so NLEP value
            data = gpd.read_file(pm.nlep.nlepOut.__dict__[nlep_stats])
            data['AREA_HA'] = data['geometry'].area/10**4   #convert from meters to hectares
            data['SAGA_HA'] = data['NLEP'+str(pm.run.yearsL[idx][-2:])+' (CEL']*pm.process.pix2ha
            data['NLEP'+str(pm.run.yearsL[idx][-2:])+'_HA'] = data['NLEP'+str(pm.run.yearsL[idx][-2:])+' (SUM']*pm.process.pix2ha
            #data['NLEP'+str(pm.run.yearsL[0][-2:])+'_IDX'] = data['NLEP'+str(pm.run.yearsL[0][-2:])+'_HA']/data['AREA_HA']
            data['NLEP' + str(pm.run.yearsL[idx][-2:]) + '_IDX'] = data['NLEP'+str(pm.run.yearsL[idx][-2:])+' (MEA']
            data['NLEP'+str(pm.run.yearsL[idx+1][-2:])+'_HA'] = data['NLEP'+str(pm.run.yearsL[idx+1][-2:])+' (SUM']*pm.process.pix2ha
            #data['NLEP'+str(pm.run.yearsL[1][-2:])+'_IDX'] = data['NLEP'+str(pm.run.yearsL[1][-2:])+'_HA'] / data['AREA_HA']
            data['NLEP'+str(pm.run.yearsL[idx+1][-2:])+'_IDX'] = data['NLEP'+str(pm.run.yearsL[idx+1][-2:])+' (MEA']
            #data['NLEC_HA'] = data['NLEP-CHANGE']*pm.process.pix2ha
            data['C' + str(pm.run.yearsL[idx][-2:]) + '-' + str(pm.run.yearsL[idx+1][-2:]) + '_HA']= data['NLEP'+str(pm.run.yearsL[idx+1][-2:])+'_HA'] - data['NLEP'+str(pm.run.yearsL[idx][-2:])+'_HA']
            data['C' + str(pm.run.yearsL[idx][-2:]) + '-' + str(pm.run.yearsL[idx+1][-2:]) + '_IDX'] = data['NLEP'+str(pm.run.yearsL[idx+1][-2:])+'_IDX'] - data['NLEP' + str(pm.run.yearsL[idx][-2:]) + '_IDX']
            cols_to_drop = ["NEXT_DOWN","NEXT_SINK","DIST_SINK","DIST_MAIN","UP_AREA","gridcode","SIDE","LAKE","ENDO","COAST","ORDER_", \
                            "SORT","Id","gridcode", \
                            "CC","VARNAME_1","HASC", "ENGTYPE","Shape_Leng","Shape_Area", \
                            "NLEP"+str(pm.run.yearsL[idx][-2:])+" (CEL", "NLEP"+str(pm.run.yearsL[idx][-2:])+" (SUM", "NLEP"+str(pm.run.yearsL[idx][-2:])+" (MEA", \
                            "NLEP"+str(pm.run.yearsL[idx+1][-2:])+" (CEL", "NLEP"+str(pm.run.yearsL[idx+1][-2:])+" (SUM", "NLEP"+str(pm.run.yearsL[idx+1][-2:])+" (MEA", \
                            "NLEC"+str(pm.run.yearsL[idx+1][-2:])+" (CEL", "NLEC"+str(pm.run.yearsL[idx+1][-2:])+ " (SUM", "NLEC"+str(pm.run.yearsL[idx+1][-2:])+" (MEA"]

            for colname in cols_to_drop:
                if colname in data.columns:
                    data = data.drop([colname], axis=1)

            #write out new cleaned NLEP shapefile
            data.to_file(pm.nlep.nlepOut.__dict__[nlep_stats], drivers='ESRI Shapefile')

            #TODO check to combine in one single csv (also above shapes)
            data_DLCT = data.groupby('DLCT_'+str(year)).mean()
            data_DLCT.to_csv(os.path.splitext(pm.nlep.nlepOut.__dict__[nlep_stats])[0] + '_DLCT_mean_'+str(pm.run.yearsL[idx+1])+'.csv')
            data_DLCT = data.groupby('DLCT_'+str(year)).sum()
            data_DLCT.to_csv(os.path.splitext(pm.nlep.nlepOut.__dict__[nlep_stats])[0] + '_DLCT_sum_'+str(pm.run.yearsL[idx+1])+'.csv')

        except Exception as e:
            print("Error preparing regional shape files {}".format(e) )
            sys.exit(-1)

    #now add reference to nlep_statistics in yaml
    pm.update(options.config)
    return


##################
#Not used anymore?, to be removed?
def publish_toWeb(pm):

    #install web class for raster
    web = Raster(pm, options)
    #loop over files to be published to webservice
    pmkeys = pm.web.getKeys('nlep_webraster')
    ctfiles = ['/data/nca_vol1/lut_input/legend_gbli_frange.txt','/data/nca_vol1/lut_input/legend_gbli_frange.txt',None]
    cttype = ['Byte','Byte','Int16']
    #TODO change is now grey with only positive values -> need color with neg/pos indication
    for idx, file in enumerate([pm.nlep.nlepOut.gbli1_sm, pm.nlep.nlepOut.gbli2_sm, pm.nlep.nlepOut.gbli_change]):
        tempfile = web.add_color(file, ctfiles[idx],pm.web.root_web_nlep_temp,type=cttype[idx])
        webfile = web.create_cog(tempfile,  os.path.join(pm.web.root_web,'NLEP','GBLI'))
        setattr(pm.web,pmkeys[idx],webfile)


    ctfiles = ['/data/nca_vol1/lut_input/legend_naturalis_frange.txt']
    for idx, file in enumerate([pm.nlep.nlepOut.naturalis]):
        file = '/data/nca_vol1/CECN/FDH/RUN7/NLEP/NATURILIS_FINAL.sdat'
        tempfile = web.add_color(file, ctfiles[idx], pm.web.root_web_nlep_temp)
        webfile = web.create_cog(tempfile, os.path.join(pm.web.root_web,'NLEP','NATURALIS'))
        setattr(pm.web,pmkeys[idx],webfile)

    '''
    ctfiles = [None]
    cttype = ['Byte']
    for idx, file in enumerate([pm.nlep.nlepOut.fmi]):
        tempfile = web.add_color(file, ctfiles[idx], pm.web.root_web_nlep_temp,type=cttype[idx])
        webfile = web.create_cog(tempfile, os.path.join(pm.web.root_web,'NLEP','LFI'))
        setattr(pm.web,pmkeys[idx],webfile)
    '''

    ctfiles = ['/data/nca_vol1/lut_input/legend_nlep_frange.txt','/data/nca_vol1/lut_input/legend_nlep_frange.txt',None]
    cttype = ['Byte','Byte','Int16']
    for idx, file in enumerate([pm.nlep.nlepOut.nlep1,pm.nlep.nlepOut.nlep2,pm.nlep.nlepOut.nlep_change]):
        tempfile = web.add_color(file, ctfiles[idx], pm.web.root_web_nlep_temp,type=cttype[idx])
        webfile = web.create_cog(tempfile, os.path.join(pm.web.root_web,'NLEP','NLEP'))
        setattr(pm.web,pmkeys[idx],webfile)


    #pm.update(options.config)

    #install web class for shapes
    web = Shape(pm, options)
    #loop over files to be published to webservice
    pmkeys = pm.web.getKeys('nlep_webshape')
    for idx, file in enumerate([pm.nlep.nlepOut.lfi_mesh]):
        shapefile = web.Shape(file, os.path.join(pm.web.root_web,'NLEP','LFI'))
        setattr(pm.web,pmkeys[idx],shapefile)

    #TODO: only one stats in yaml - need to split to multiple stats
    for idx, file in enumerate([pm.nlep.nlepOut.nlep_stats, \
                                '/data/nca_vol1/CECN/FDH/RUN7/NLEP/admin/NLEP_gadm36_FDH_1_EPSG3857.shp', \
                                '/data/nca_vol1/CECN/FDH/RUN7/NLEP/admin/NLEP_hybas_lake_FDH_level7_EPSG3857.shp']):
        shapefile = web.Shape(file, os.path.join(pm.web.root_web,'NLEP','NLEP'))
        #setattr(pm.web,pmkeys[idx],shapefile)

    #pm.update(options.config)

    return


####################################################################################################
# workflow to create NLEP account
#From a runObject
def create_NLEP(runObject):

    try:
        #1. Generate the Green Background Landscape potential Index (GBLI)
        gbli = GBLI(runObject)
        create_gbli(gbli)
        logger.info("** GBLI ready ...\n\n")

        #2. Generate the nature conservation value index
        naturalis = NATURALIS(runObject)
        create_naturalis(naturalis)
        logger.info("** NATURALIS ready ...\n\n")

        #3. Generate the landscape fragmentation indicator (effective mesh size)
        fragm = LFI(runObject)
        create_lfi(fragm)   #3rd parameter is basin level to use


        join_lfi(fragm)   #join fragmentation on multiple basin levels
        logger.info("** FRAGMENTATION indicator ready ...\n\n")

        #4. Calculate NLEP and NLEP change
        calc_nlep(runObject)
        print("** NLEP indicator ready ...\n\n")

    except Error as e:
        raise Error(e)


