# -*- coding: utf-8 -*-
'''
RESTRICTIONS
This software is property of VITO and free-of-use. Any results originating from this
script should be referenced with:
"Smets, Buchhorn (2021), Ecosystem Capability Accounting (ENCA) software package, initially
developed in the framework of the PAPBIO project (FED/2018/399-509)."

VITO has no liability for any errors or omissions in the contents of this script.

SYNOPSIS
python3.6 leac_report.py -e path_to_yaml -r path_to_shapefile -v

DESCRIPTION
Processor to create Land Cover Account (LEAC), as part of Ecosystem Capability Accounting
(see http://ecosystemaccounting.net). This script generates reports from lEAC account.

PREREQUISITES
Python >= 3.0

MANDATORY INPUTS
* yaml configuration file
* leac account output
* administrative polygons for reporting

AUTHORS
Bruno Smets <bruno.smets@vito.be>

VERSION
5.0 (2021-07-09, initial version 2020-05-12)
'''

import os
import sys
import time
import traceback
import subprocess
import optparse

import shapefile
import rasterio
import rasterio.mask
import pandas as pd
import scipy.stats as stats
import geopandas as gpd
from shapely.geometry import shape
from tqdm import tqdm
import warnings
import numpy as np
import matplotlib.pyplot as plt

from general.params import Parameters
#from general.prepare_web import Raster
#from general.prepare_web import Shape
import general.process as process

def create_leac_VRT(pm, path_out):

    path_temp = os.path.join(path_out, 'temp')
    if not os.path.exists(path_temp):
        os.makedirs(path_temp)

    lPaths = []
    lColumns = []

    for year in pm.run.yearsL:
        lPaths.append(pm.leac.leacOut.__dict__['lc'+str(year)+'_reclass'])

    path_list = os.path.join(path_temp, 'paths_leac' + '.txt')
    vrt_nodata = 0
    try:
        with open(path_list, "w") as outfile:
            outfile.write("\n".join(lPaths))
        path_raster_vrt = os.path.join(path_temp, 'paths_leac' + '.vrt')
        cmd = 'gdalbuildvrt -vrtnodata {} -separate -input_file_list {} -overwrite {}'.format(vrt_nodata, path_list, path_raster_vrt)
        subprocess.check_call(cmd, shell=True)
    except Exception as e:
        print(e)
        raise ('Error in generating datacube')

    return path_raster_vrt

def extract_leac_stats(pm,path_SELU,path_raster_vrt,path_out,lut_table,ID_FIELD='HYBAS_ID'):

    if not os.path.exists(os.path.join(path_out,'maps')):
        os.makedirs(os.path.join(path_out,'maps'))

    year_ref = str(pm.run.yearsL[0])

    with shapefile.Reader(path_SELU) as oVector, rasterio.open(path_raster_vrt) as src_raster:
        # get field names for shapeile attributes
        fieldnames = [f[0] for f in oVector.fields]
        # filter out deletion flag
        while 'DeletionFlag' in fieldnames: fieldnames.remove('DeletionFlag')
        # check that the required files names for this shapefiles are in
        if ID_FIELD in fieldnames:
            pass
        else:
            print('!!! The needed attribute columns for this shapefile: ' + str(path_SELU) + ' are not present.')
            raise

        # check resolution
        if int(src_raster.transform.a) == 100:
            area2ha = 1/ (100. * 100.)
            pix2ha = 1 / (1. * 1.)
        elif int(src_raster.transform.a) == 10:
            area2ha = 1 / (100. * 100.)
            pix2ha = 1 / (10. * 10.)
        elif (int(pm.run.projection == 4326)) and (1/src_raster.transform.a >= 300):
            area2ha = 1 / (100. * 100.)
            pix2ha  = ((1/src_raster.transform.a) * (1/src_raster.transform.a) )/ (100*100)
        elif int(src_raster.transform.a) == 30:
            area2ha = 1 / (100. * 100.)
            pix2ha  = 1 / (3.3 * 3.3)
        else:
            raise RuntimeError('SELU statistics is only supported for rasters in 10x10, 30x30, 100x100 or 300x300m')

        # some stuff for progress bar
        max_counter = len(oVector)
        # ini tqm
        t = tqdm(total=int(max_counter))

        # create empty dataframe for statistical units
        row_data = []
        count_shapes = 0

        # iterate over SELU polygons and extract raster data
        for shapeRec in oVector.iterShapeRecords():
            count_shapes += 1
            # first generate the record_dic
            dRecord = dict(zip(fieldnames, shapeRec.record))
            if options.verbose: print("... processing {}".format(dRecord[ID_FIELD]))

            # load polygon shape - secure due to Multi-polygons
            pFeature = shape(shapeRec.shape.__geo_interface__).buffer(0)

            # secure avoid memory overflow for too big polygons
            if pFeature.area / 1000000. > 75000:
                raise RuntimeError('SELU statistics is not supported for this big polygon ' + str(pFeature.area))

            # use rasterio to cut out raster and mask to SELU polygon
            aData, _ = rasterio.mask.mask(src_raster, [pFeature], crop=True, filled=False)

            #calculate stock per year
            FIELD = 'reclass'

            #TODO check if columns exist in dataframe
            df = pd.read_csv(lut_table)
            dLC = dict()
            colors = []
            '''
            for idx,row in df.iterrows():
                dLC[idx] = row.PSCLC_DESC
                colors.append('#'+str(row.PSCLC_COLOR))
            '''
            #english dict
            '''
            dLC[0] = 'Novalue'
            dLC[1] = 'Urban'  #100
            dLC[2] = 'Mines'  #131
            dLC[3] = 'Agriculture' #210
            dLC[4] = 'Mangrove' #214, 4
            dLC[5] = 'AgroForest' #240, 5
            dLC[6] = 'ClosedForest' #311, 6
            dLC[7] = 'OpenForest' #312, 7
            dLC[8] = 'GalleryForest' #313, 8
            dLC[9] = 'SavannaHerbaceous' #321, 9
            dLC[10]= 'SavannaShrub'  #322, 10
            dLC[11]= 'SavannaTree' #323, 11
            dLC[12]= 'BareSoil' #330, 12
            dLC[13]= 'Wetland' #410, 13
            dLC[14]= 'WaterBody' #510, 14
            #dLC[15] = 'Sea' #524, 15
            '''
            #'''
            #french dict
            dLC[0] = 'NoValue'
            dLC[1] = 'Urbain'
            dLC[2] = 'Mines et carrieres'  # 131
            dLC[3] = 'Cultures pluviales'  # 210
            dLC[4] = 'Mangroves'  # 214, 4
            dLC[5] = 'Agrofôret'  # 240, 5
            dLC[6] = 'Fôret dense'  # 311, 6
            dLC[7] = 'Fôret claire'  # 312, 7
            dLC[8] = 'Fôret galeries'  # 313, 8
            dLC[9] = 'Savane herbeuse'  # 321, 9
            dLC[10] = 'Savane arbustive'  # 322, 10
            dLC[11] = 'Savane boisée'  # 323, 11
            dLC[12] = 'Roche et sol nue'  # 330, 12
            dLC[13] = 'Prairie humide'  # 410, 13
            dLC[14] = "Plan d'eau"  # 510, 14
            # dLC[15] = 'Mer' #524, 15
            colors=['#404040','#F90000','#63653F','#F096FF','#BA00BA','#FF00FF','#006400','#008C00','#00FFBF','#DCFF02','#FFEA00','#84FF7B','#B4B4B4','#0096A0','#0032C8'] #,'#000080']
            #'''

            df_stats = pd.DataFrame(index=np.arange(pm.process.maxLcClasses))
            for idx, year in enumerate(pm.run.yearsL):
                for c in np.arange(pm.process.maxLcClasses):
                    df_stats.at[c, year] = np.count_nonzero(aData[idx] == c)
            #add descriptive classes
            s = df_stats.index.to_series()
            df_stats.index = s.map(dLC).fillna(s)
            #save as stock
            file_out_stock = os.path.join(path_out,'HYBAS_' + str(dRecord[ID_FIELD]) + '_LEAC_stock'+'.csv')
            df_stats.to_csv(file_out_stock,sep=";")

            #calculate change per subsequent year
            df_changeYr = pd.DataFrame(index=np.arange(pm.process.maxLcClasses),columns=pm.run.yearsL)
            for idx, year in enumerate(pm.run.yearsL[:-1]):
                for c in np.arange(pm.process.maxLcClasses):
                    df_changeYr.at[c,pm.run.yearsL[idx+1]] = (np.count_nonzero(aData[idx+1]==c) - np.count_nonzero(aData[idx]==c))
                #save dataframe to csv
                #file_out = os.path.join(path_out, 'HYBAS_' + str(dRecord[ID_FIELD]) + '_LEAC_change_'+str(year)+'-'+str(pm.run.yearsL[idx+1])+'.csv')
                #df_temp.to_csv(file_out, sep=";")
            # replace index by descriptive classes
            s = df_changeYr.index.to_series()
            df_changeYr.index = s.map(dLC).fillna(s)

            #calculate change over series
            sankey_row = []
            df_change = pd.DataFrame(index=np.arange(pm.process.maxLcClasses),columns=np.arange(pm.process.maxLcClasses))
            for c in np.arange(pm.process.maxLcClasses):
                for d in np.arange(pm.process.maxLcClasses):
                    df_change.at[c, d] = np.count_nonzero(np.bitwise_and(aData[0] == c, aData[-1] == d))
                    sankey_row.append([c, d, np.count_nonzero(np.bitwise_and(aData[0] == c, aData[-1] == d))])

            # save dataframe to csv
            file_out_change = os.path.join(path_out,'HYBAS_' + str(dRecord[ID_FIELD]) + '_LEAC_change_' + str(pm.run.yearsL[0]) + '-' + str(pm.run.yearsL[-1]) + '.csv')
            df_change.to_csv(file_out_change, sep=";")

            if c in np.arange(pm.process.maxLcClasses):
                df_change.at[c, c] = 0.  # push no change pixels to zero

            #plot as piechart
            #could add explode = (0,0,0.1,....) for i.e. urban
            plt.clf()
            fig = plt.figure(figsize=(15,7))
            #fig, (ax1,ax2) = plt.subplots(1,2,figsize=(10,5))
            if int(pm.run.projection == 4326):
                size = pFeature.area *110*110 * 100 #110 to assume 1° is 110km
            else:
                size = pFeature.area/10000
            fig.suptitle('LEAC for SELU '+str(dRecord[ID_FIELD]) + ' V' + str(pm.run.version)+'rc'+str(pm.run.run)+'\n'+str(int(size))+' ha')
            ax1 = fig.add_subplot(121)
            se = pd.Series(colors)
            df_stats['color'] = se.values
            df_to_plot = df_stats.loc[lambda df_stats: df_stats[year_ref] > 0]
            patches, text, _ = ax1.pie(df_to_plot[year_ref].values,labels=df_to_plot.index,autopct='%1.1f%%',textprops={'color':"black",'fontsize':8},colors=df_to_plot.color.tolist())
            if ID_FIELD == 'HYBAS_ID':
                ax1.set_title('Stock '+year_ref+' (' + str(dRecord['DLCT_'+year_ref])+')')
            #ax1.legend(patches, labels=df_stats.index, fontsize=8, loc='lower left',bbox_to_anchor=(-0.1, -0.1, 0.5, 0.5), ncol=3)
            #patches, text, _ = ax2.pie(df_stats['2018'].values, autopct='%1.1f%%',textprops={'color':"black"},colors=colors)
            #ax2.set_title('2018 (' + str(dRecord['DLCT_2000'])+')')
            #ax2.legend(patches, labels=df_stats.index, fontsize=8, loc='lower left',bbox_to_anchor=(-0.1, -0.1, 0.5, 0.5), ncol=3)
            ax2 = fig.add_subplot(122)
            df_changeYr.plot.barh(ax=ax2,stacked=True,fontsize=8)  #linewidth=1.0
            ax2.grid(zorder=0)
            ax2.axvline(color='black')
            ax2.set_title('Change per class (ha' + ')')
            #pie = df_stats.plot.pie(y='2000', title="LEAC stock for SELU "+ str(dRecord[ID_FIELD]), labels=None, autopct='%1.1f%%', shadow=True, startangle=0)
            #fig = pie.get_figure()
            fig.savefig(os.path.join(path_out, 'maps','HYBAS_' + str(dRecord[ID_FIELD]) + '_LEAC' + '.png'))
            plt.close()

            change_ha = df_change.sum().sum()
            row_data.append([dRecord[ID_FIELD],change_ha,change_ha/(pFeature.area/10000)*100])

            # update progress bar
            t.update()
            #df_sankey = pd.DataFrame(sankey_row, columns=['2000', '2018', 'value'])

        # close progress bar
        t.close()

        '''
        #TODO add SANKEY diagrams for class (or flow) changes
        import plotly.graph_objects as go
        df_sankey = pd.DataFrame(sankey_row, columns=['2000', '2018', 'value'])
        fig = go.Figure(data=[go.Sankey(link=dict(source=df_sankey['2000'], target=df_sankey['2018'], value=df_sankey['value']))])
        '''

        #save change overview in csv
        df_change_overview = pd.DataFrame(row_data, columns=[dRecord[ID_FIELD],'CHANGE_HA','CHANGE_PRCT'])
        df_change_overview.to_csv(os.path.join(path_out,'OVERVIEW_HYBAS_LEAC_change.csv'))

        return

######################################################################################################################
def main(options):
    # read yaml configuration file
    pm = Parameters(options.config, mode='leac')
    region = pm.run.region_short

    path_out = os.path.join(pm.leac.leacOut.__dict__['root_leac'],'stats')
    path_vrt = create_leac_VRT(pm, path_out)

    #lut_table = '/data/nca_vol3/lookup_tables/Lookup_PSCLC_Rank_en.csv'
    #lut_table = '/data/nca_vol1/lut_input/Lookup_PSCLC-level2_Rank_V1.csv'
    lut_table = pm.leac.leacIn.__dict__['lut_lc']

    print('Create report at SELU level')
    path_SELU = pm.leac.leacOut.__dict__['SELU']  #pm.leac.leacIn.selu.split(',')[1]
    #extract_leac_stats(pm,path_SELU, path_vrt, path_out, lut_table)

    print('Create report at ADMIN level')
    #path_ADMIN = "/data/nca_vol2/aux_data/local/processed/Africa/NKL/reporting/NKL_reporting_V4.shp"
    path_ADMIN = pm.leac.leacIn.__dict__['adm0']
    #extract_leac_stats(pm,path_ADMIN, path_vrt, path_out, lut_table, ID_FIELD='GID_0')
    path_ADMIN = pm.leac.leacIn.__dict__['adm1']
    extract_leac_stats(pm,path_ADMIN, path_vrt, path_out, lut_table, ID_FIELD='GID_1')

    #path_ADMIN = '/data/nca_vol2/test/bruno/Limite_PNMB_3857.shp'
    #extract_leac_stats(pm,path_ADMIN,path_vrt,path_out, lut_table, ID_FIELD='Label')

    return


#######################################################################################################################
if __name__ == '__main__':

    try:
        # check if right Python version is available.
        assert sys.version_info[0:2] >= (3, 5), "You need at minimum python 3.5 to execute this script."
        start_time = time.time()
        # ini the Option Parser
        parser = optparse.OptionParser(formatter=optparse.TitledHelpFormatter(), usage=globals()['__doc__'],
                                       version="%prog v2.0")
        parser.add_option('-v', '--verbose', action='store_true', default=False, help='verbose output')
        parser.add_option('-e', '--config', help='Path to the config.ini file. Needed.')
        parser.add_option('-r', '--overwrite', action='store_true', default=False, help='Reprocess all data through overwriting. Optional')
        # parse the given system arguments
        (options, args) = parser.parse_args()
        # do checks on the parsed options
        if (not options.config) or (not os.path.isfile(os.path.normpath(options.config))):
            parser.error("the -e argument for the config file is missing or the given path doesn't exist!!!")
        if len(args) != 0:
            parser.error('too many arguments')

        if options.verbose: print('START OF MODULE: ENCA LEAC REPORTING')
        if options.verbose: print(time.asctime())
        # call main function - "options" object is set to global object
        main(options)
        # Pparameter, sSuccess = main()
        if options.verbose: print('END OF MODULE: ENCA LEAC REPORTING')
        if options.verbose: print(time.asctime())
    except:
        traceback.print_stack()