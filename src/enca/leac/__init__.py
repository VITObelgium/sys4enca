import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

import enca
from enca.classification import CSV_2_dict, reclassification
from enca.framework.config_check import YEARLY, ConfigItem, ConfigRaster, ConfigShape
from enca.framework.geoprocessing import add_color, block_window_generator

logger = logging.getLogger(__name__)
REF_YEAR = 'ref_year'
REF_LANDCOVER = 'ref_landcover'

class Leac(enca.ENCARun):

    run_type = enca.RunType.ENCA
    component = 'leac'

    def __init__(self, config):
        super().__init__(config)

        self.config_template.update({
            self.component: {
                REF_YEAR: ConfigItem(default = None, optional=True),
                REF_LANDCOVER: ConfigRaster(default = None, optional=True),
                "lut_ct_lc": ConfigItem(),
                "lut_ct_lcf": ConfigItem(),
                "lut_lc": ConfigItem(),
                "lut_lcflows": ConfigItem()
                },
        })


    def _start(self):
        logger.debug('Hello from ENCA Leac')
        self.make_output_filenames()
        #calc max_lc_classes
        df = pd.read_csv(self.config['leac']['lut_lc'], comment='#')
        self.config['leac']['max_lc_classes'] = df['RANK'].max()

        #1. reclassify land cover maps
        self.reclassify()
        logger.debug("** LANDCOVER reclassification ready ...\n\n")

        if self.config['leac'][REF_YEAR]:
            #2. Calculate land cover change in ha
            #options.overwrite = True
            self.calc_lc_changes()
            logger.debug("** LANDCOVER changes calculated  ...\n\n")

            #3. Calculate land cover stocks and flows on total area_of_interest
            self.calc_lc_flows()
            logger.debug("** LANDCOVER flows calculated ...\n\n")

        ######################################################################################################################
    def format_LCC_table(self, df, path_out):

        #add land cover names, transform to ha, move noChange, calculate % of area, total formation and consumption of land
        table_out = path_out
        column_classes = df.columns  #exclude Total column
        index_classes = df.index
        #transform to pixels to hectares
        pix2ha = self.accord.pixel_area_m2() / 10000 #m2 to hectares
        df = df * pix2ha

        #add total
        df['Total'] = df.loc[index_classes, column_classes].sum(axis=1)
        #df = df.append(pd.Series(df.loc[classes, classes].sum(axis=0), name='Total'))
        df.loc['Total'] = df.sum()

        #move no change to separate col/row and sum formation and consumption
        df['No change'] = 0
        #df = df.append(pd.Series(name='No change'))
        df.loc['No change'] = 0
        for idx,lc_code_column in enumerate(column_classes):
            for idy, lc_code_index in enumerate(index_classes):
                if lc_code_column.rsplit(' ',1)[0] != lc_code_index.rsplit(' ',1)[0] :
                    continue
                df.loc[lc_code_index,f'No change'] = df.iloc[idx,idx]
                df.loc[f'No change',lc_code_column] = df.iloc[idx,idx]
                df.iloc[idx,idx] = 0

        #sum formation and consumption
        df['Total consumption'] = df.loc[index_classes, column_classes].sum(axis=1)
        #df = df.append(pd.Series(df.loc[classes, classes].sum(axis=0), name='Total formation'))
        df.loc['Total formation'] = df.loc[index_classes, column_classes].sum(axis=0)

        #move 'order' : Total consumption/formation -> No change -> Total
        classes1 = list(column_classes)
        classes2 = list(index_classes)
        classes1.extend(['Total consumption', 'No change', 'Total'])
        df = df[classes1]
        classes2.extend(['Total formation', 'No change', 'Total'])
        df = df.reindex(classes2)

        #add percentage of area
        total_area = df.loc['Total'].sum() / 2
        df['% of area'] = df['Total'] / total_area * 100
        df['% of area changed'] = df['Total consumption'] / total_area * 100
        #df = df.append(pd.Series(df.loc['Total', :] / total_area * 100, name='% of area'))
        df.loc['% of area'] = df.loc['Total',:] / total_area*100
        #df = df.append(pd.Series(df.loc['Total formation', :] / total_area * 100, name='% of area changed'))
        df.loc['% of area changed'] = df.loc['Total formation',:] / total_area*100

        df.to_csv(table_out, sep=',')
        return table_out

    ######################################################################################################################
    def format_LCF_table(self, table_consumption, table_formation, table_out, year, ref_year):

        #join consumption & formation flow tables and format
        df_c = table_consumption
        df_f = table_formation

        #transform to pixels to hectares
        pix2ha = self.accord.pixel_area_m2() / 10000 #m2 to hectares
        df_c = df_c * pix2ha
        df_f = df_f * pix2ha

        classes = df_c.columns
        r = df_c.index

        #add total consumption (losses) and initial stock
        #df_c = df_c.append(pd.Series(df_c.loc[r[:-1], classes].sum(axis=0), name='Total consumption of land cover (losses)')) #classes 9 excluded since no change
        #df_c = df_c.append(pd.Series(df_c.loc[r, classes].sum(axis=0), name='Stock Land Cover yr1'))
        df_c['Total consumption of land cover (losses)'] = df_c.iloc[:-1].sum(axis=1)
        df_c[f'Stock land cover {str(year)}'] = df_c.sum(axis=1)
        dict_lcf_c={}
        for i in r:
            dict_lcf_c[i] = 'lcf'+str(i+1)+'_c'
        df_c = df_c.rename(index=dict_lcf_c)

        classes = df_f.columns
        r = df_f.index
        # add total consumption (losses) and initial stock
        #df_f = df_f.append(pd.Series(df_f.loc[r[:-1], classes].sum(axis=0), name='Total formation of land cover (gains)'))
        #df_f = df_f.append(pd.Series(df_f.loc[r, classes].sum(axis=0), name='Stock Land Cover yr2'))
        df_f['Total formation of land cover (gains)'] = df_f.iloc[:-1].sum(axis=0)
        df_f[f'stock land cover {str(ref_year)}'] = df_f.sum(axis=0)
        dict_lcf_f = {}
        for i in r:
            dict_lcf_f[i] = 'lcf' + str(i+1) + '_f'
        df_f = df_f.rename(index=dict_lcf_f)

        #merge both flows and add Net change, turnover
        df = pd.concat([df_c,df_f])

        df.to_csv(table_out, sep=',')

    def reclassify(self):

        for year in self.years:
            if os.path.exists(self.leac_recl[year]):
                pass

            reclass_dict = CSV_2_dict(self.config['leac']['lut_lc'], old_class='CD', new_class='RANK')

            with rasterio.open(self.config["land_cover"][year], 'r') as ds_open:
                profile = ds_open.profile
                #from here driver should allways be gtiff
                profile['driver'] = 'GTiff'
                if profile["nodata"]:
                    nodata = profile["nodata"]
                else: nodata = 0

                profile2 = profile.copy()
                profile2['dtype'] = np.uint16
                with rasterio.open(self.leac_recl[year], 'w', **dict(profile, nodata = nodata)) as ds_out:
                    for _, window in block_window_generator((2048,2048), ds_open.height, ds_open.width):
                        aBlock = ds_open.read(1, window=window, masked=True)
                        #Doesn't seem a nodata value was set
                        reclassified, dict_classes  = reclassification(aBlock, reclass_dict, nodata, nodata)
                        ds_out.write(reclassified, window=window, indexes=1)
                add_color(self.leac_recl[year], self.config[self.component]['lut_ct_lc'], 'Byte')

        logger.debug("** Land cover reclassified ...")

    def calc_lc_changes(self):
        ref_year = self.ref_year
        #function to calculate the land cover changes by creating tabular output and change map

        for idx, year in enumerate(self.years):  #minus 1 as change maps require tuples
            if os.path.exists(self.leac_change[year]):
                pass

            lc1_reclass = self.leac_recl[year]
            lc2_reclass = self.leac_recl[ref_year]

            profile = self.accord.ref_profile
            count = pd.DataFrame()
            with rasterio.open(lc1_reclass, 'r') as ds_open1, rasterio.open(lc2_reclass, 'r') as ds_open2,\
                    rasterio.open(self.reporting_raster, 'r') as ds_mask,\
                    rasterio.open(self.leac_change[year], 'w', **dict(profile)) as ds_out:
                    for _, window in block_window_generator((2048,2048), ds_open1.height, ds_open1.width):
                        aBlock1 = ds_open1.read(1, window=window, masked=True)
                        aBlock2 = ds_open2.read(1, window=window, masked=True)
                        aMask   = ds_mask.read(1, window=window, masked=False)
                        #mask any area outside reporting area
                        aBlock1 = np.ma.masked_where(aMask == 0,aBlock1)
                        aBlock2 = np.ma.masked_array(aBlock2, aBlock1.mask)
                        change = (aBlock1-1) + ((aBlock2-1)*self.config['leac']['max_lc_classes'])

                        ds_out.write(change, window=window, indexes=1)

                        count = count.add(pd.DataFrame(pd.Series(change.flatten()).value_counts(), columns=['count']), fill_value = 0)

            count['year'] = count.index % self.config['leac']['max_lc_classes'] +1
            count['ref_year'] = count.index // self.config['leac']['max_lc_classes'] +1

            pivot_count = count.pivot(index ='ref_year',columns='year', values='count').fillna(0)
            reclass_dict = CSV_2_dict(self.config['leac']['lut_lc'], old_class='RANK', new_class='DESC')
            pivot_count.index = [reclass_dict[index]+ f' {ref_year}' for index in pivot_count.index]
            pivot_count.columns = [reclass_dict[column] + f' {year}' for column in pivot_count.columns]
            #post-process output data
            #format table : convert pixels to ha & TODO move no_change in separate col/row
            table_out_formatted = self.format_LCC_table(pivot_count, self.final_tab[year])

            logger.debug("** LEAC change matrix ready ...")

        return

    def calc_lc_flows(self):
        #function to calculate the land cover change flows (consumption and formation)
        ref_year = self.ref_year
        for idx, year in enumerate(self.years):

            #A. combine the 2 input grids into 4-digit number (temporary step) #seems to be not necessary we have the data? needs to be for the reclass
            multi = 1000
            dtype = np.uint32

            reclass_dict = CSV_2_dict(self.config['leac']['lut_lcflows'], old_class='LC_CHANGE', new_class='ID_lcflows')

            with rasterio.open(self.leac_recl[year], 'r') as ds_open1, \
                    rasterio.open(self.leac_recl[ref_year],'r') as ds_open2:
                profile = ds_open1.profile
                if profile["nodata"]:
                    nodata = profile["nodata"]
                else: nodata = 0
                with rasterio.open(self.lcc[year], 'w', **dict(profile, nodata = nodata, dtype=dtype)) as ds_out_4digit, \
                        rasterio.open(self.lcf[year], 'w', **dict(profile, nodata = nodata, dtype=np.uint8)) as ds_out_1digit:
                    for _, window in block_window_generator((2048,2048), ds_open1.height, ds_open1.width):
                        aBlock1 = ds_open1.read(1, window=window, masked=True).astype(dtype)
                        aBlock2 = ds_open2.read(1, window=window, masked=True).astype(dtype)
                        four_digit = aBlock1*multi + aBlock2
                        ds_out_4digit.write(four_digit, window=window, indexes=1)

                        reclassified, dict_classes  = reclassification(four_digit, reclass_dict, nodata, nodata)
                        ds_out_1digit.write(reclassified, window=window, indexes=1)


            # post-process output data
            # let's now translate to colored geotiff
            ctfile = self.config['leac']['lut_ct_lcf']
            add_color(self.lcf[year], ctfile, 'Byte')



        #C. Calculate the consumption (ref year) and formation (new year) raster + table
        for idx, year in enumerate(self.years):
            for idy, grid_in in enumerate([self.leac_recl[year], self.leac_recl[ref_year]]):
                if idy == 0:
                    account = self.lcf_cons[year]
                else:
                    account = self.lcf_form[year]

                with rasterio.open(grid_in, 'r') as ds_open2, \
                        rasterio.open(self.lcf[year],'r') as ds_open1:
                    profile = ds_open1.profile
                    if profile["nodata"]:
                        nodata = profile["nodata"]
                    else: nodata = 0
                    dtype = np.uint32
                    with rasterio.open(account, 'w', **dict(profile, nodata = nodata, dtype=dtype)) as ds_out:
                        for _, window in block_window_generator((2048,2048), ds_open1.height, ds_open1.width):
                            aBlock1 = ds_open1.read(1, window=window, masked=True).astype(dtype)
                            aBlock2 = ds_open2.read(1, window=window, masked=True).astype(dtype)

                            ds_out.write(aBlock1*10000+aBlock2, window=window, indexes=1)



        #D. Calculate cross-table stock-flows for consumption and formation
        for idx, year in enumerate(self.years):
            for idy, grid_in in enumerate([self.leac_recl[year], self.leac_recl[ref_year]]):

                if idy == 0:
                    grid_out = self.lc_cons[year]
                else:
                    grid_out = self.lc_form[year]

                profile = self.accord.ref_profile
                count = pd.DataFrame()
                with rasterio.open(self.lcf[year], 'r') as ds_open1, rasterio.open(grid_in, 'r') as ds_open2, \
                        rasterio.open(self.reporting_raster, 'r') as ds_mask, \
                        rasterio.open(grid_out, 'w', **dict(profile, dtype=np.uint32)) as ds_out:
                    for _, window in block_window_generator((2048,2048), ds_open1.height, ds_open1.width):
                        aBlock1 = ds_open1.read(1, window=window, masked=True).astype(np.uint32)
                        aBlock2 = ds_open2.read(1, window=window, masked=True).astype(np.uint32)
                        aMask   = ds_mask.read(1, window=window, masked=False)
                        #mask any area outside reporting area
                        aBlock1 = np.ma.masked_where(aMask == 0,aBlock1)
                        aBlock2 = np.ma.masked_array(aBlock2, aBlock1.mask)
                        change = (aBlock1-1) + ((aBlock2-1)*self.config['leac']['max_lc_classes'])

                        ds_out.write(change, window=window, indexes=1)

                        count = count.add(pd.DataFrame(pd.Series(change.flatten()).value_counts(),columns=['count']), fill_value = 0)

                count['year'] = count.index % self.config['leac']['max_lc_classes'] +1 #block1 year lcf[year]
                count['ref_year'] = count.index // self.config['leac']['max_lc_classes'] +1 #block2 ref_year (grid_in)

                pivot_count = count.pivot(index ='ref_year',columns='year', values='count').fillna(0)

                if idy == 0:
                    cons = pivot_count.copy()
                else:
                    form = pivot_count.copy()


            #format table and convert to hectares
            self.format_LCF_table(cons, form, self.lc_lcf_tab[year], ref_year, year)


    def make_output_filenames(self):
        self.leac_recl = {}
        self.leac_change = {}
        self.final_tab = {}
        self.lcc = {}
        self.lcf = {}
        self.lcf_cons = {}
        self.lcf_form = {}
        self.lc_cons = {}
        self.lc_form = {}
        self.lc_lcf_tab = {}


        for idx,year in enumerate(self.years):
            if REF_YEAR in self.config['leac']:
                self.ref_year = self.config['leac'][REF_YEAR]
                ref_year = self.ref_year
                self.leac_change[year] = os.path.join(self.temp_dir(),f'LEAC-change_{self.aoi_name}_{year}-{ref_year}.tif')
                self.final_tab[year] = os.path.join(self.reports,f'LEAC-change_{self.aoi_name}_{year}-{ref_year}_final.csv')
                self.lcc[year] = self.leac_change[year].replace('.tif','_4digits.tif')
                self.lcf[year] = os.path.join(self.maps,f'LEAC-flow_{self.aoi_name}_{year}-{ref_year}.tif')
                self.lcf_cons[year] = os.path.join(self.temp_dir(),f'LCF_{str(year)}_consumption_{self.aoi_name}_{year}-{ref_year}.tif')
                self.lcf_form[year] = os.path.join(self.temp_dir(),f'LCF_{str(year)}_formation_{self.aoi_name}_{year}-{ref_year}.tif')
                self.lc_cons[year] = os.path.join(self.maps, f'LEAC_consumption_{str(year)}_{self.aoi_name}_{year}-{ref_year}.tif')
                self.lc_form[year] = os.path.join(self.maps, f'LEAC_formation_{str(year)}_{self.aoi_name}_{year}-{ref_year}.tif')
                self.lc_lcf_tab[year] = os.path.join(self.reports,f'LEAC_consumption_{str(year)}_{self.aoi_name}_{year}-{ref_year}.csv')
                self.config["land_cover"][ref_year] = self.config['leac'][REF_LANDCOVER]
                years = self.years + [ref_year]
            else : years = self.years

        for idx,year in enumerate(years):
            self.leac_recl[year] = os.path.join(self.maps,f'LEAC_{self.aoi_name}_{year}.tif')



