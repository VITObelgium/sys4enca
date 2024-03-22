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
import logging

import rasterio
from rasterio.windows import Window
import numpy as np

from enca.infra.gbli import GBLI
from enca.infra.naturalis import NATURALIS
from enca.infra.lfi import LFI, Catchment, OSM
from enca.framework.errors import Error
from enca.framework.geoprocessing import block_window_generator, number_blocks

logger = logging.getLogger(__name__)


######################################################################################################################
def create_gbli(gbli):

    # keys_available = True

    #for easy typing
    years = gbli.years


    try:
        for year in years:
            if os.path.exists(gbli.leac_gbli_sm[year]):
                continue
            logger.info("rate each land cover class on greeness")
            gbli.reclassify_PSCLC(year, nodata = 0)
            # "account for tree density"
            r = gbli.apply_treecover(year, nodata = 0)
            if r == 0:
                logger.info("no tree cover correction done for GBLI")
            # "smooth greeness maps"
            gbli.gaussian_smooth(year)

            # post-process output data
            # let's now translate to colored geotiff
            #ctfile = '/data/nca_vol1/qgis/legend_gbli.txt'
            #scale = [0, 255, 0, 255]  # scale from values to values
            #tiffile = web.add_color(gbli_sm , ctfile, os.path.join(pm.nlep.nlepOut.root_nlep, 'stock'),'Byte', 0, scale)


        #now calculate the GBLI difference between two consequetive years (doesn't seem to be necessary to be consec)
        if gbli.ref_year:
            for year in years:
                if os.path.exists(gbli.leac_gbli_diff[year]):
                    continue
                logger.info("calculate difference map")
                gbli.diff_gbli(year)

                # post-process output data
                # let's now translate to colored geotiff
                # ctfile = '/data/nca_vol1/qgis/legend_gbli_change_2.txt'
                # scale = [0, 255, 0, 255]  # scale from values to values
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
    naturalis.smooth_PA()


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
                    if aData.shape[0] == 1:
                        aOut = aData[0,:,:]*1.0
                    elif aData.shape[0] == 2:
                        aOut = aData[0,:,:]*0.3 + aData[1,:,:]*0.7
                    elif aData.shape[0] == 3:
                        aOut = aData[0,:,:]*0.2 + aData[1,:,:]*0.3 + aData[2,:,:]*0.5
                    else:
                        raise("Error : Not able to join LFI, check catchment levels")
                    ds_out.write(aOut.astype(rasterio.float32), 1, window=window)

                    # free
                    aData = None


        except Error as e:
            os.unlink(lfi.lfi_meff[year])
            raise Error(e)

##################
def calc_nlep(runObject):

    # keys_available = True
    block_shape = (4096, 4096)

    for idx, year in enumerate(runObject.years):
        if os.path.exists(runObject.nlep[year]):
            print (f"Skip NLEP calculation for {year}, data exists")
            continue

        try:
            #first create NLEP per year
            #if change calc gbli nieeds to be calculated twice
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
    if runObject.config['infra']['ref_year']:
        for year in runObject.years:
            if os.path.exists(runObject.clep[year]):
                print (f"Skip CLEP calculation for {year}-{runObject.config['infra']['ref_year']} , data exists")
                continue
            try:
                grid_out = runObject.clep[year]
                ref_year = runObject.config['infra']['ref_year']

                with rasterio.open(runObject.nlep[ref_year], 'r') as A_open , \
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

####################################################################################################
# workflow to create NLEP account
#From a runObject
def create_NLEP(runObject):

    try:
        #1. Generate the Green Background Landscape potential Index (GBLI)
        #3 min for 2 year
        gbli = GBLI(runObject)
        create_gbli(gbli)
        logger.info("** GBLI ready ...\n\n")

        #2. Generate the nature conservation value index
        #45sec for 2 jaar
        naturalis = NATURALIS(runObject)
        create_naturalis(naturalis)
        logger.info("** NATURALIS ready ...\n\n")

        #3. Generate the landscape fragmentation indicator (effective mesh size)
        #4min
        fragm = LFI(runObject)
        create_lfi(fragm)   #3rd parameter is basin level to use

        #15sec
        join_lfi(fragm)   #join fragmentation on multiple basin levels
        logger.info("** FRAGMENTATION indicator ready ...\n\n")

        #4. Calculate NLEP and NLEP change
        #50sec
        calc_nlep(runObject)
        print("** NLEP indicator ready ...\n\n")

    except Error as e:
        raise Error(e)
