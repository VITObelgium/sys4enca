'''
Created on Oct 23, 2019

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
import gdal
import glob

class Raster(object):
    '''
    classdocs
    '''


    def __init__(self, params, options):
        '''
        Constructor
        '''
        self.options = options
    
    def create_cog(self, file, path, mode='average'):
        
        os.rename(file, file+'.tmp')
        webfile = os.path.join(path,os.path.basename(file))
        
        cmd = "gdaladdo -r " + str(mode)
        cmd = cmd + " " + file+'.tmp'
        cmd = cmd + " 2 4 8 16 32"
        #run it
        if self.options.verbose: print("Running command %s" % cmd )
        subprocess.check_call(cmd, shell=True)
        
        cmd = "gdal_translate"
        cmd = cmd + " -co COMPRESS=LZW -co TILED=YES -co COPY_SRC_OVERVIEWS=YES"
        cmd = cmd + " " + file+'.tmp'
        cmd = cmd + " " + webfile
        
        #run it
        if self.options.verbose: print("Running command %s" % cmd )
        subprocess.check_call(cmd, shell=True)

        os.remove(file+'.tmp')

        return webfile
    
    '''
    def create_colorramp(self, vrtfile):

        #add color table
        ds = gdal.Open(vrtfile, gdal.GA_Update)
        band = ds.GetRasterBand(1)     #single band VRT mosaics
        #band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
        ct = gdal.ColorTable()  #gdal.GCI_PaletteIndex)
        
        if index == 0:  #NDVI band
            n1 =(140,92,8,255)
            m1 =(255,255,30,255)
            ct_mid = 125
            n2 =m1
            m2 =(0,77,0,255)
            
        ct.CreateColorRamp(0,n1,ct_mid,m1)     #first half of table
        ct.CreateColorRamp(ct_mid,n2,250,m2)   #second half of table
        
        #band overwrites
        if index == 0:  #NDVI band
            ct.SetColorEntry(251,(0,0,0,255))    #not existing
            ct.SetColorEntry(252,(0,0,0,255))    #not existing
            ct.SetColorEntry(253,(0,0,0,255))    #not existing
            ct.SetColorEntry(254,(0,50,255,255))         #sea

        #common overwrites
        ct.SetColorEntry(255,(228,228,228,255))    #missing
        
        band.SetColorTable(ct)
        band.FlushCache()
        band = None
        ds = None         #push to file
    '''
    
    def add_color(self, file, ctable, path, type, nodata, scale, offset=0):

        if scale is None:
            scale = [0,1,0,1]

        if type == 'Byte':
            max = 255
        else:
            max = 65535

        #1. transform SDAT into VRT => TODO MOVE TO webtemp dir
        vrtfile = os.path.join(path,os.path.splitext(os.path.basename(file))[0]+".vrt")
        tiffile = os.path.splitext(vrtfile)[0]+".tiff"
        
        cmd = "gdal_translate"
        cmd = cmd + " -of vrt"
        cmd = cmd + " -ot " + type
        cmd = cmd + " -scale " + ' '.join(str(x) for x in scale)
        cmd = cmd + " " + file
        cmd = cmd + " " + vrtfile
        
        #run it
        try:
            if self.options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)
        except:
            print("Error creating vrt file for %s " %file)
            raise
        
        if not ctable is None:
            #2. add color table
            try:
                if not ctable is None:
                    ds = gdal.Open(vrtfile, gdal.GA_Update)
                    band = ds.GetRasterBand(1)     #single band VRT mosaics
                    ct = gdal.ColorTable()         #gdal.GCI_PaletteIndex)
                    
                    #create dummy table
                    for i in range(0,max):
                        ct.SetColorEntry(i,(255,255,255,255))
                    try:
                        with open(ctable) as f:
                            next(f)
                            next(f)
                            for line in f:
                                #overwrite values
                                ct.SetColorEntry(int(line.split(',')[0]),(int(line.split(',')[1]),int(line.split(',')[2]),int(line.split(',')[3]),int(line.split(',')[4])))    #(value,(R,G,B,alpha))
                    except:
                        print("Not able to open color table file %s" % ctable)
                        sys.exit(-1)
                    band.SetColorTable(ct)
                    band.FlushCache()
                    band = None
                    ds = None         #push to file
            except:
                print("Error adding color table %s " % ctable)
                raise
        
        #3. transform to colored geotiff
        cmd = "gdal_translate"
        cmd = cmd + " -co COMPRESS=LZW -co TILED=YES"
        cmd = cmd + " -a_nodata " + str(nodata)
        cmd = cmd + " " + vrtfile
        cmd = cmd + " " + tiffile
        
        #run it
        try:
            if self.options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)
        except:
            print("Error creating colored geotiff %s " %tiffile)
        
        return tiffile
    
class Shape(object):
    def __init__(self, params, options):
        '''
        Constructor
        '''
        self.options = options
        
    def Shape2GeoJson(self, file, path):
        
        jsonfile = os.path.join(path, os.path.splitext(os.path.basename(file))[0]+".geojson")
        
        cmd = "ogr2ogr"
        cmd = cmd + " -f GeoJSON"
        cmd = cmd + " -t_srs crs:84 "
        cmd = cmd + " " + jsonfile
        cmd = cmd + " " + file
        
        #run it
        try:
            if self.options.verbose: print("Running command %s" % cmd )
            subprocess.check_call(cmd, shell=True)
        except:
            print("Error creating colored geojson %s " %jsonfile)
        
        return jsonfile
    
    def Shape(self, file, path):
        
        destfile= ''
        
        try:
            for fileG in glob.glob(os.path.splitext(file)[0]+'.*'):
                print('... copy file %s to %s', str(fileG), path)
                shutil.copy(str(fileG), path)
        except:
            print ("Error copying shape file %s " %file)
            
            return destfile