'''
Prototype script to create NREP account.

Required inputs:
   * gloric database
   * hybas level(s) for AOI
   * naturalis (non-smoothed)
   * (in future) lut river_categories
   * dams


Created on Oct 26 2020

@author: smetsb
'''

import os
import logging

logger = logging.getLogger(__name__)

from enca.infra.rawi import RAWI
from enca.infra.natriv import NATRIV
from enca.infra.fragriv import FRAGRIV

######################################################################################################################
def create_rawi(rawi):

    #TODO prepare data directly from Gloric. Now intersect with Area-of-Interest is done in ArcGIS Geoprocessing CLIP.
    #TODO cut Gloric-AOI with requested hybas level, now done in ArcGIS Geoprocessing INTERSECT.

    #TODO remove rawi upper level to reprocess all years

    if not os.path.exists(rawi.riverSRMU):
        # Assign RELU (river categories)
        #TODO check if rawi.rawi exists
        logger.info('** Assign river categories (RELU)')
        rawi.assign_RELU()
        rawi.rasterize_rawi(rawi.rawi_shape, rawi.riverSRMU, ID_FIELD='log_SRMU')
    else:
        logger.info("Skip RAWI calculation, base rawi data exists")
        #return


    # Group Standardized River Measurement Unit per SELU
    logger.info('** Calculate Standardized River Measurement Unit (SRMU)')
    if not os.path.exists(rawi.rawi_selu):
        rawi.group_SRMUperSELU()

    # Rasterize river & blend with land cover to calculate RS (river system area units count in ha) and RAWI
    if not os.path.exists(rawi.rawi_mask):
        rawi.rasterize_rivers()

    for idx, year in enumerate(rawi.years):
        logger.info('** calculating rawi for year {}'.format(year))
        if os.path.exists(rawi.rawi[year]):
            continue

        joinedMask = rawi.join(year)
        rawi.calc_rawi(joinedMask, year)
        rawi.rasterize_rawi(rawi.rawi_selu, rawi.rawi[year] ,'RAWI_'+str(year))


    return

######################################################################################################################
def create_natriv(natriv,rawi_mask):

    if os.path.exists(natriv.natriv):
        logger.warning("Skip NATRIV calculation, data exists")
        return


    natriv.create_natriv(rawi_mask)


######################################################################################################################
def create_fragriv(oFragriv, catchlevel):

    if os.path.exists(oFragriv.fragriv_hybas[catchlevel]):
        logger.warning("Skip FRAGRIV calculation for level {}, data exists".format(catchlevel))
        return

    logger.info('Calculate river fragmentation at level {}'.format(catchlevel))
    oFragriv.count_dams_perHybas(catchlevel)


######################################################################################################################
# workflow to create NREP account
def create_NREP(runObject):

        print('\n')
        #options.overwrite = True
        #1. Generate the River Accessibility Weighted Index (RAWI)
        logger.info('* Calculate River Accessibility Weighted Index (RAWI)')
        #1.5min
        rawi = RAWI(runObject)
        #what's this 12 removing it, putting it hard coded?
        create_rawi(rawi)
        logger.info("** RAWI ready ...\n\n")
        #options.overwrite = False

        #2. Generate the Rivers High Nature Value (NATRIV)
        #1.5min
        logger.info('* Calculate Rivers High Nature Value (NATRIV)')
        natriv = NATRIV(runObject)
        create_natriv(natriv,rawi.rawi_mask)
        logger.info("** NATRIV ready ...\n\n")

        #3. Calculate River fragmentation (FRAGRIV)
        #4min
        logger.info('* Calculate River fragmentation (FRAGRIV)')
        fragriv = FRAGRIV(runObject)
        for basin in fragriv.hybas.keys():
            create_fragriv(fragriv, basin)  #latest parameter is hybas level

        #join the three levels

        if not (os.path.exists(fragriv.fragriv)):
            fragriv.join()
        logger.info('** FRAGRIV ready ... \n\n')
