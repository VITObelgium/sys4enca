#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper Functions web services

version: 0.1
2021-06-25

"""

import rasterio
import rasterio.warp as warp
from tqdm import tqdm
import math
import numpy as np
import subprocess
import pandas as pd
import geopandas as gpd
import time
import os
import traceback
import psutil
import matplotlib.pyplot as plt
import re
import seaborn

################333
def createDataFrame(path, pattern, fsymlinks=False, second_pattern = None):
    """Function of find all specified files (by pattern)  in a given
       directory and get there path into a Pandas database.
       fsymlinks=True would list also all linked files"""
    # ini empty list to hold the file info
    data = []

    # scan the input folder and populate a list with data
    if second_pattern is None:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=fsymlinks) and entry.name.lower().endswith(pattern):
                data.append(os.path.normpath(entry.path))
    else:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=fsymlinks) and (entry.name.lower().endswith(pattern) or entry.name.lower().endswith(second_pattern)):
                data.append(os.path.normpath(entry.path))  

    if len(data) != 0:
        df = pd.DataFrame(data, columns=['path'])
        df['basename'] = df['path'].apply(lambda x: os.path.basename(x))

        #search for years in file names and extract
        lYears = []
        unknown_counter = 0
        for row in df.itertuples():
            match = re.match(r'.*([1-2][0-9]{3})', row.basename)
            if match is not None:
                lYears.append([row.basename, match.group(1)])
            else:
                lYears.append([row.basename, 'unknown'])
                unknown_counter += 1

        if df.shape[0] == unknown_counter:
            df['year'] = 'universal'
        else:
            df_results = pd.DataFrame.from_records(lYears, columns=['basename','year'])
            df = df.merge(df_results, how='left', left_on='basename', right_on='basename')

        return df
    else:
        print(
            'scantree-dataframe creation error: There was no valid files with the chosen pattern found during the file search and no data extracted.')
        raise

def Cut2AOI(path_in, path_out, file_extent):
    """ cut raster to AOI when in same coordinate system """
    #get extent, resolution and projection from AOI raster file
    with rasterio.open(file_extent) as src:
        bbox = src.bounds
    


    cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=LZW -projwin {} {} {} {} {} {}'.format(
                                                                  bbox[0],bbox[3],bbox[2], bbox[1],
                                                                  path_in, path_out)
    
    try:
        subprocess.check_call(cmd, shell=True)
    except:
        raise

def ReadColorTable(datafile):
    """ creates a rasterio conform dict from a given color table """
    
    #define Lookup dic
    Lookup = {}
    #fill it with standard value (white)
    for i in range(0,256):
        #Lookup[i] = [0,0,0,0]
        Lookup[i] = (0,0,0,0)
    
    with open(datafile, 'r') as fp:
        for line in fp:
            #convert line into list
            data = line.split(',')
            try:
                valid = int(float(data[0]))
            except:
                continue
            else:
                try:
                    #Lookup[int(data[0])] = [int(data[1]), int(data[2]), int(data[3]), int(data[4])]
                    Lookup[int(data[0])] = (int(data[1]), int(data[2]), int(data[3]), int(data[4]))
                except:
                    continue
        
    return Lookup

def CreateColorTable(palette_name):
    """ creates a color table for a byte dataset, nodata is set to 0 and gets white.
    
    Note: all matplotlib colorbars names are accepted"""
    
    try:
        dicPal = seaborn.color_palette(palette_name, 255)
    except:
        #looks like the name was wrong to we use standard "Greens"
        dicPal = seaborn.color_palette('Greens', 255)
        
    #define Lookup dic
    Lookup = {}
    #fill it with standard value (white)
    for i in range(0,256):
        if i == 0:
            #Lookup[i] = [0,0,0,0]
            Lookup[i] = (0,0,0,0)
        else:
            #Lookup[i] = [int(dicPal[i-1][0] * 255),int(dicPal[i-1][1] * 255),int(dicPal[i-1][2] * 255),255]
            Lookup[i] = (int(dicPal[i-1][0] * 255),int(dicPal[i-1][1] * 255),int(dicPal[i-1][2] * 255),255)
        
    return Lookup