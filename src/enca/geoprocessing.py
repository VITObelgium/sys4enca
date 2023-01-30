"""Geographic raster and vector data processing utilities."""

import datetime
import logging
import math
import os
import re
import subprocess
import sys
from enum import Enum
from os.path import splitext, basename, normpath

import affine
import numpy as np
import geopandas as gpd
import pandas as pd
import rasterio
import rasterio.mask
import shapely.geometry
from osgeo import __version__ as GDALversion

from .ecosystem import SHAPE_ID, ECOTYPE, ECO_ID
from .errors import Error

if sys.version_info[:2] >= (3, 8):
    from importlib.metadata import version
    # TODO: Import directly (no need for conditional) when `python_requires = >= 3.8`
else:
    from importlib_metadata import version

logger = logging.getLogger(__name__)

GEO_ID = 'GEO_ID'  #: Column label for unique region identifier in GeoDataFrames

LIST_UNITS = ['German legal metre', 'm', 'metre', 'Meter']
DIC_KNOWN_WKT_STRINGS = {'ETRS_1989_LAEA': 3035,
                         'ETRS89-extended / LAEA Europe': 3035,
                         'World_Mollweide': 54009}  # Note: 54009 is an ESRI identifier so add to ESRI id CONSTANT
ESRI_IDENTIFIER = [54009]
MINIMUM_RESOLUTION = 100.
POLY_MIN_SIZE = 0.1  # size in square metre for minimum area for valid statistics vector to be used
EARTH_CIRCUMFERENCE_METRE = 40075000

_GDAL_FILLNODATA = 'gdal_fillnodata.bat' if os.name == 'nt' else 'gdal_fillnodata.py'
_GDAL_EDIT = 'gdal_edit.bat' if os.name == 'nt' else 'gdal_edit.py'
_GDAL_CALC = 'gdal_calc.bat' if os.name == 'nt' else 'gdal_calc.py'
_GDAL_POLY = 'gdal_polygonize.bat' if os.name == 'nt' else 'gdal_polygonize.py'

SUM = 'sum'
COUNT = 'px_count'

# Map rasterio dtypes to GDAL command line dtypes names:
_dtype_map = {
    rasterio.uint8: 'Byte',
    rasterio.uint16: 'UInt16',
    rasterio.int16: 'Int16',
    rasterio.uint32: 'Uint32',
    rasterio.int32: 'Int32',
    rasterio.float32: 'Float32',
    rasterio.float64: 'Float64',
    # rasterio.complex_: CInt32,
    # rasterio.complex64: 'CFloat32',
    # rasterio.complex128: 'CFloat64',
    # rasterio.complex_int16: 'Cint16'
}


class RasterType(Enum):
    """When rescaling / reprojecting rasters, we need to take into account the type of data contained in a raster.
    """
    CATEGORICAL = 0  # Discrete values (map data), to be resampled with nearest neighbour method
    RELATIVE = 1  # Quantities expressed in a unit relative to an area (e.g tonne / ha.)
    ABSOLUTE_POINT = 2  # Point data such as height, slope, wind speed
    ABSOLUTE_VOLUME = 3  # Quantities contained in pixel, e.g. population, precipitation. Must preserve total volume in geographical area.


class Metadata(object):
    """ This class handles all metadata"""

    def __init__(self, creator, seaa_model):
        """ during the ini all master metadata of the project are added"""
        self.module = seaa_model
        self.master_tags = {"creator": creator,
                            "SEAA-Module": self.module,
                            "ENCA-version": version('sys4enca'),
                            "software_raster_processing": "rasterio {} (on GDAL {}); "
                                                          "GDAL binary {}".format(rasterio.__version__,
                                                                                  rasterio.__gdal_version__,
                                                                                  GDALversion),
                            "software_vector_processing": "geopandas {}".format(gpd.__version__),
                            "ENCA_run_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "TIFFTAG_SOFTWARE": 'ENCA version {}'.format(version('sys4enca'))
                            }
        self.raster_tags = {}

    def read_raster_tags(self, path_list):
        """ Function reads tags from raster data and stores them in raster_tags"""
        self.raster_tags = {}
        counter = 1
        # now we refill with all files used to process the current file
        for path in path_list:
            history_counter = 1
            # get tags of raster file
            with rasterio.open(path, 'r') as src:
                src_tags = src.tags()
                src_tags.update(name=src.name)
            # now we create the current file history line
            if "file_creation" not in src_tags.keys():
                src_tags['file_creation'] = 'unknown'
            if "creator" not in src_tags.keys():
                src_tags['creator'] = 'unknown'
            if "processing" not in src_tags.keys():
                src_tags['processing'] = 'unknown'

            self.raster_tags.update({"input-file{}".format(counter):
                "file-name: {}, created on: {}, by: {}, info: {}".format(
                    basename(src_tags['name']),
                    src_tags['file_creation'],
                    src_tags['creator'],
                    src_tags['processing'])
            })
            # deal with existing 1st child history lines of input files
            for key in src_tags.keys():
                if re.match("^(input-file\\d*)$", key):
                    # now we add this tag line to the history of the specific file
                    self.raster_tags.update({"input-file{}-history{}".format(counter, history_counter): src_tags[key]})
                    history_counter += 1
            counter += 1

    def prepare_raster_tags(self, processing_info, unit_info='N/A', output_file_name=None, output_profile=None):
        """Prepare the tags to write out a raster via rasterio"""
        # first we fill the main tags for the new file
        out_tags = {"file_creation": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "processing": processing_info,
                    "unit": unit_info}
        # now we add the master tags
        out_tags.update(self.master_tags)
        # now we add the raster tags prepared from the input files if exist
        if not self.raster_tags:
            pass
        else:
            out_tags.update(self.raster_tags)
            # reset raster_tags since it is a one-time use
            self.raster_tags = {}
        # add some standard TIFFTAGS
        if output_file_name is not None:
            out_tags.update(TIFFTAG_DOCUMENTNAME=os.path.splitext(os.path.basename(output_file_name))[0],
                            TIFFTAG_IMAGEDESCRIPTION=processing_info,
                            TIFFTAG_DATETIME=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if output_profile is not None:
            try:
                unit = output_profile['crs'].linear_units
                res = (float(output_profile['transform'].a), float(abs(output_profile['transform'].e)))
            except:
                pass
            else:
                out_tags.update(TIFFTAG_XRESOLUTION=res[0],
                                TIFFTAG_YRESOLUTION=res[1],
                                TIFFTAG_RESOLUTIONUNIT=unit)
        return out_tags


class GeoProcessing(object):
    """ class that handles the geoprocessing of raster and vector files towards given profile of reference raster"""

    def __init__(self, creator, seaa_model, temp_dir, ProjectExtentTiff=None):
        self.metadata = Metadata(creator, seaa_model)
        self.ref_profile = None  # in ENCA mainly the profile of the statistical raster processing file
        self.ref_extent = None  # in ENCA mainly the extent of the statistical raster processing file
        if ProjectExtentTiff is not None:
            # get key parameter of input file
            param = self._load_profile(ProjectExtentTiff)
            # run checks (that we have valid EPSG number, is projected coordinate system, unit is metre
            # Important: otherwise certain class functions will not work
            # note: if really Geographical projection needed then init the ref_profile and ref_extent manually
            try:
                self._epsg_check(param['epsg'], ProjectExtentTiff)
            except:
                raise ValueError('the provided raster file to init the GeoProcessing object has no valid EPSG.')

            if param['epsg'] in ESRI_IDENTIFIER:
                raise RuntimeError('ESRI projections without EPSG conversion are currently not supported as '
                                   'reference file for the GeoProcessing object.')

            if param['projected'] is False:
                raise Error(f'Currently the GeoProcessing object only supports reference files with projected '
                            f'coordinate systems.')
            if param['unit'] not in LIST_UNITS:
                raise Error('Currently the GeoProcessing object only supports reference files with projected '
                            'coordinate systems in the following units: ' + ', '.join(LIST_UNITS))

            self.ref_profile = param['profile']
            self.ref_extent = param['bbox']  # tuple: (lower left x, lower left y, upper right x, upper right y)

        self.reporting_profile = None  # in ENCA mainly the profile of the reporting raster processing file
        self.reporting_extent = None
        self.temp_dir = temp_dir
        self.src_parameters = {}  # all the parameters of the raster file which has to be geoprocessed

    def _check(self):
        """ checks if profile and extent for reference file is existing"""
        if self.ref_profile is None:
            raise ValueError("The AccoRD object was not correctly initialized. "
                             "The rasterio profile for the reference file is missing")
        if self.ref_extent is None:
            raise ValueError("The AccoRD object was not correctly initialized. "
                             "The rasterio extent for the reference file is missing")

    def _check2(self):
        """ checks if profile and extent of reporting shapefile is existing"""
        if self.reporting_profile is None:
            raise ValueError("The AccoRD object was not completely initialized. "
                             "The rasterio profile for the reporting raster file is missing")
        if self.reporting_extent is None:
            raise ValueError("The AccoRD object was not completely initialized. "
                             "The rasterio BoundingBox for the reporting raster file is missing")

    def _check3(self, filename):
        """ checks if we have projected raster AND the projection unit is in the list we accept"""
        if self.src_parameters['projected'] is False:
            raise Error(f'Currently the ENCA tool only supports raster maps with absolute volume-based data '
                        f' ({filename}) with a projected coordinate system.')
        if self.src_parameters['unit'] not in LIST_UNITS:
            raise Error(f'Currently the ENCA tool only supports raster maps with absolute volume-based data'
                        f' ({filename}) with the following projection units: ' + ', '.join(LIST_UNITS))

    def _epsg_check(self, epsg, filename):
        """ check for the main current limitation of theGeoProcessing class - processing of datasets with valid EPSG"""
        # TODO make this method part of _load_profile since we always call it after _load_profile()  anyway?
        if (epsg == 'no_epsg_code') or (epsg is None):
            raise Error(f'The raster map {filename} does not have a valid EPSG projection or know WKT-string. '
                        f'Please adapt your raster map or insert a EPSG number in DIC_KNOWN_WKT_STRINGS dictionary.')

    def _full_pre_check(self, path_in, clean_src=False):
        """ execute the _check() function PLUS make sure the key parameter of the to process file are loaded

        :param path_in: raster path to file for which is checked that all profile info is available
        """
        self._check()
        # delete src info if needed
        if clean_src:
            self.src_parameters = {}
        # check if all parameters of input files are existing - when run in stand-alone mode
        if not self.src_parameters:
            self.src_parameters = self._load_profile(path_in)
        # check for main current geoprocessing limitation
        self._epsg_check(self.src_parameters['epsg'], path_in)

    def vectorize(self, raster_path, root_out):
        """Wrapper for `gdal_polygonize`."""

        # create output file name
        out_path = os.path.join(root_out, 'vector_{}.shp'.format(splitext(basename(raster_path))[0]))

        # setup cmd command
        cmd = '{} -8 "{}" "{}" vectorized ID'.format(_GDAL_POLY, normpath(raster_path), out_path)

        if not os.path.exists(out_path):
            try:
                subprocess.check_call(cmd, shell=True)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not polygonize needed raster file: {e}')
            else:
                logger.debug('* Raster file (%s) was successfully polygonized.', raster_path)
        else:
            logger.debug('* Vector file %s already exists, skipping.', out_path)
            pass
        return out_path

    def rasterize(self, gdb: gpd.GeoDataFrame, gdb_column_name: str, path_out: str, nodata_value=0, dtype='Float32',
                  guess_dtype=False, mode='statistical'):
        """Rasterize a given geopandas dataframe with a specified `column_name`.

        :param gdb: GeoDataFrame to rasterize.
        :param gdb_column_name: column to rasterize.
        :param path_out: directory for output file.
        :param nodata_value: nodata value for the raster.
        :param dtype: dtype as a string
        :param guess_dtype: guess dtype from the data (only for integer data)
        :param mode: if to rasterize to the statistical or reporting extent and resolution (statistical, reporting)
        :return: None
        """
        self._check()
        # check if we have nodata values in the to rasterize vectors
        if nodata_value in gdb[gdb_column_name].values:
            logger.warning('rasterizing shape: nodata value %s appears in column we want to rasterize.', nodata_value)

        # guess the dtype
        if guess_dtype:
            max_id = gdb[gdb_column_name].max()
            if max_id <= 255:
                dtype = 'Byte'
            else:
                dtype = 'UInt16'

        # write the geodatabase to file
        temp_out = os.path.join(self.temp_dir, 'vector_{}.gpkg'.format(splitext(basename(path_out))[0]))

        if mode == 'statistical':
            pextent = self.ref_extent
            res = (float(self.ref_profile['transform'].a), float(abs(self.ref_profile['transform'].e)))
            out_crs = self.ref_profile['crs'].to_epsg()
        elif mode == 'reporting':
            self._check2()
            pextent = self.reporting_extent
            res = (float(self.reporting_profile['transform'].a), float(abs(self.reporting_profile['transform'].e)))
            out_crs = self.reporting_profile['crs'].to_epsg()
        else:
            raise RuntimeError('this mode was not forseen in rasterization function.')

        cmd = 'gdal_rasterize -a "{}" -l "{}" -a_nodata {} -co COMPRESS=DEFLATE -co TILED=YES -co INTERLEAVE=BAND ' \
              '-ot {} -te {} {} {} {} -tr {} {} -a_SRS "EPSG:{}" ' \
              '"{}" "{}"'.format(gdb_column_name,
                                 splitext(basename(temp_out))[0],
                                 nodata_value,
                                 dtype,
                                 pextent.left,
                                 pextent.bottom,
                                 pextent.right,
                                 pextent.top,
                                 res[0],
                                 res[1],
                                 out_crs,
                                 temp_out,
                                 path_out)

        if not os.path.exists(path_out):
            try:
                gdb[[gdb_column_name, 'geometry']].to_file(temp_out, driver='GPKG')
                subprocess.check_call(cmd, shell=True)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not rasterize needed vector file: {e}')
            else:
                tags = self.metadata.prepare_raster_tags(
                    'Vector file was rasterized to reference file extent.', '')
                with rasterio.open(path_out, 'r+') as dst:
                    dst.update_tags(**tags)
                logger.debug('* Vector file was successfully rasterized.')
            finally:
                # remove temp files
                if os.path.exists(temp_out):
                    os.remove(temp_out)
        else:
            logger.debug('* Rasterized file %s already exists, skipping.', path_out)
            pass

    def FillHoles(self, path_in, path_out, maxdistance=25, smooth=0):
        """Fill nodata holes.

        :param path_in: input filename
        :param path_out: output filename
        :param maxdistance: distance in pixels in which valid pixels are searched
        :param smooth: smoothing iterations
        """
        path_temp = os.path.join(self.temp_dir, 'fillHoles_temp.tif')

        cmd1 = '{} -md {} -si {} "{}" "{}"'.format(_GDAL_FILLNODATA, maxdistance, smooth, path_in, path_temp)

        # now we bring back the nodata value in the file
        with rasterio.open(path_in) as src:
            nodata = src.nodata
        cmd2 = '{} -a_nodata {} "{}"'.format(_GDAL_EDIT, nodata, path_temp)

        # and compress it
        cmd3 = 'gdal_translate -co COMPRESS=DEFLATE -co TILED=YES -co INTERLEAVE=BAND ' \
               '"{}" "{}"'.format(path_temp, path_out)

        # run
        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd1, shell=True)
                subprocess.check_call(cmd2, shell=True)
                subprocess.check_call(cmd3, shell=True)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not fill the holes in the input raster: {e}')
            else:
                logger.debug('* Raster file (%s) was successfully filled.', path_in)
            finally:
                # remove temp files
                if os.path.exists(path_temp):
                    os.remove(path_temp)
        else:
            logger.debug('* File with filled data (%s) already exists, skipping.', path_out)
            pass

    def pixel_area_m2(self):
        """ gives the area of one pixel in square meter for the AccoRD reference file"""
        self._check()
        if self.ref_profile['crs'].is_projected:
            return pixel_area(self.ref_profile['crs'], self.ref_profile['transform'])
        else:
            return None

    def _load_profile(self, raster_path):
        """ function to extract key variables of the given raster file

        :param raster_path: absolute file path to raster file for which to extract the profile
        :return: dictionary containing the key raster profile parameters
        """
        dFile = {}
        dFile['overwrite_s_srs'] = False  # flag that EPSG code of raster file was not valid and extracted from WKT
        try:
            with rasterio.open(raster_path) as src:
                # EPSG
                if src.crs.is_epsg_code:
                    dFile['epsg'] = src.crs.to_epsg()
                else:
                    # we have no EPSG or the VRT problem which shows only the WKT instead of the EPSG code
                    dFile['epsg'] = 'no_epsg_code'
                    # check if we have a know wkt and can overwrite to the right epsg integer number
                    for key, value in DIC_KNOWN_WKT_STRINGS.items():
                        if key in src.crs.wkt:
                            dFile['epsg'] = value
                            # flag that s_srs is overwritten
                            dFile['overwrite_s_srs'] = True
                            break
                # raster extent
                dFile['bbox'] = src.bounds
                # resolution tuple
                dFile['res'] = src.res
                # datatype
                dFile['dtype'] = src.dtypes[0]
                # raster profile
                dFile['profile'] = src.profile
                # raster height and width
                dFile['width'] = src.width
                dFile['height'] = src.height
                # nodata value
                dFile['nodata'] = src.nodata
                # dataset name
                dFile['name'] = src.name
                # dataset file tags (no band tags)
                dFile['tags'] = src.tags
                # pixel area in square metre
                if src.crs.is_projected:
                    dFile['px_area_m2'] = pixel_area(src.crs, src.transform)
                    _, dFile['unit_factor'] = src.crs.linear_units_factor
                else:
                    dFile['px_area_m2'] = None
                    dFile['unit_factor'] = None
                # projected and units
                dFile['projected'] = src.crs.is_projected
                dFile['geographic'] = src.crs.is_geographic
                dFile['unit'] = src.crs.linear_units
        except rasterio.errors.RasterioIOError as e:
            raise Error(f'Failed to open raster file "{raster_path}": {e}')
        return dFile

    def _check_raster_processing_needed(self):
        """ checks if profile of a given raster matches already the reference profile and if any processing is needed

        :return: boolean
        """
        if not self.src_parameters:
            raise ValueError('the _check_raster_processing_needed function can be only called when the profile '
                             'of the input raster file is loaded (use _load_profile() function).')

        if (self.src_parameters['epsg'] == self.ref_profile['crs'].to_epsg()) \
                and (self.src_parameters['bbox'] == self.ref_extent) \
                and (self.src_parameters['res'] == (float(self.ref_profile['transform'].a),
                                                    float(abs(self.ref_profile['transform'].e)))):
            return True
        else:
            return False

    def AutomaticBring2AOI(self, path_in, raster_type=RasterType.CATEGORICAL, wOT=None, path_out=None,
                           secure_run=False):
        """Wrapper around :func:`Bring2AOI` which checks if the raster needs to be adjusted, and if yes,
           generates all settings automatically PLUS it returns the absolute filename of the adapted raster file.

        :param path_in: input file path (absolute) of raster file to be processed to reference file specifications
        :param raster_type: type of raster data used to define the geoprocessing method
        :param wOT: overwriting the input raster data type
        :param path_out: output filename (a filename is derived from the input filename if not provided)
        :param secure_run: self.src_parameters is reset before function execution
        :return: the file path of the adapted file is changes were needed, else the input file name
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # test if we even have to process the input raster file
        if self._check_raster_processing_needed():
            logger.debug('* raster file {} OK'.format(os.path.basename(path_in)))
            # reset the src_parameter before close to definitely clean (One Time Use)
            self.src_parameters = {}
            return path_in
        else:
            if path_out is None:
                # now we have to prepare a generic output raster file name
                path_out = os.path.join(self.temp_dir,
                                        '{}_ENCA_{}m_EPSG{}.tif'.format(splitext(basename(path_in))[0],
                                                                        int(self.ref_profile['transform'].a),
                                                                        self.ref_profile['crs'].to_epsg()))
            # run the standard Bring2AOI function
            self.Bring2AOI(path_in, path_out, raster_type=raster_type, wOT=wOT)
            return path_out

    def Bring2AOI(self, path_in, path_out, raster_type=RasterType.CATEGORICAL, wOT=None, secure_run=False):
        """Wrapper to automatically determine which geoprocessing mode has to applied to get the input file
           to the raster configurations of the reference file (extent, resolution, projection)."""
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # make sure the output folder is existing
        os.makedirs(os.path.dirname(path_out), exist_ok=True)

        # overwrite the output dataformat if not given
        if wOT is None:
            wOT = _dtype_map[self.src_parameters['dtype']]

        # decision tree for geoprocessing method based on:
        # processing case: crop, resample, or warp
        # resolution change: same, up-sampling, or down-sampling
        # data type: discrete data, continuous_relative unit, continuous_absolute_point, continuous_absolute_volume
        res_case, processing_case = self._get_case_parameters()

        # now get the name of correct GeoProcessing function to run and settings
        processing_mode, resampling_mode = self._query_raster_processing_table(res_case, processing_case, raster_type)

        # apply the decision
        logger.warning(f'- raster ({os.path.basename(path_in)}) will be adjusted ({processing_mode}) '
                       f'with {resampling_mode} filter.')
        if processing_mode == 'Crop2AOI':
            self.Crop2AOI(path_in, path_out, wResampling=resampling_mode, wOT=wOT)
        elif processing_mode == 'Translate2AOI':
            self.Translate2AOI(path_in, path_out, wResampling=resampling_mode, wOT=wOT, mode=res_case)
        elif processing_mode == 'Warp2AOI':
            self.Warp2AOI(path_in, path_out, wResampling=resampling_mode, wOT=wOT)
        elif processing_mode == 'VolumeWarp2AOI':
            self.VolumeWarp2AOI(path_in, path_out, wOT=wOT, oversampling_factor=10)
        else:
            raise RuntimeError(f'the processing mode {processing_mode} is currently not implemented. '
                               f'Adapt your input dataset manually to full-fill the ENCA-tool input data '
                               f'specifications (see manual)')

    def _query_raster_processing_table(self, res_case, processing_case, raster_type):
        """ evaluate which GEoProcessing function and settings have to be used based on given parameters"""

        # possible processing_modes: Crop2AOI, Translate2AOI, Warp2AOI, VolumeWarp2AOI, VolumeTranslate2AOI
        # possible resampling_modes: nearest, sum, bilinear, mode, average
        decision_tree = {
            'crop': {RasterType.CATEGORICAL: {'same': ('Crop2AOI', 'nearest')},
                     RasterType.RELATIVE: {'same': ('Crop2AOI', 'bilinear')},
                     RasterType.ABSOLUTE_POINT: {'same': ('Crop2AOI', 'nearest')},
                     RasterType.ABSOLUTE_VOLUME: {'same': ('Crop2AOI', 'nearest')}},
            'resample': {RasterType.CATEGORICAL: {'up-sampling': ('Translate2AOI', 'nearest'),
                                                  'down-sampling': ('Translate2AOI', 'mode')},
                         RasterType.RELATIVE: {'up-sampling': ('Translate2AOI', 'bilinear'),
                                               'down-sampling': ('Translate2AOI', 'average')},
                         RasterType.ABSOLUTE_POINT: {'up-sampling': ('Translate2AOI', 'bilinear'),
                                                     'down-sampling': ('Translate2AOI', 'average')},
                         RasterType.ABSOLUTE_VOLUME: {'up-sampling': ('VolumeWarp2AOI', '3-step'),
                                                      'down-sampling': ('VolumeWarp2AOI', '3-step')}},
            'warp': {RasterType.CATEGORICAL: {'same': ('Warp2AOI', 'nearest'),
                                              'up-sampling': ('Warp2AOI', 'nearest'),
                                              'down-sampling': ('Warp2AOI', 'mode')},
                     RasterType.RELATIVE: {'same': ('Warp2AOI', 'near'),
                                           'up-sampling': ('Warp2AOI', 'bilinear'),
                                           'down-sampling': ('Warp2AOI', 'average')},
                     RasterType.ABSOLUTE_POINT: {'same': ('Warp2AOI', 'near'),
                                                 'up-sampling': ('Warp2AOI', 'bilinear'),
                                                 'down-sampling': ('Warp2AOI', 'average')},
                     RasterType.ABSOLUTE_VOLUME: {'same': ('VolumeWarp2AOI', '3-step'),
                                                  'up-sampling': ('VolumeWarp2AOI', '3-step'),
                                                  'down-sampling': ('VolumeWarp2AOI', '3-step')}}}
        try:
            processing_mode, resampling_mode = decision_tree[processing_case][raster_type][res_case]
        except:
            raise ValueError(f'the given data ({processing_case, raster_type, res_case}) is not in the GeoProcessing '
                             f'decision tree to be evaluated. No geoprocessing mode and resampling mode can be '
                             f'determined.')

        return processing_mode, resampling_mode

    def _get_case_parameters(self):
        """ run some tests to get the connection between src_dataset and reference dataset in terms of
            spatial resolution and geoprocessing method"""
        # First, check resolution change
        if self.src_parameters['projected']:
            if self.src_parameters['unit_factor'] != 1:
                # multiply the resolution with unit factor to get resolution in metre
                src_res = (self.src_parameters['res'][0] * self.src_parameters['unit_factor'],
                           self.src_parameters['res'][1] * self.src_parameters['unit_factor'])
            else:
                src_res = self.src_parameters['res']
        elif self.src_parameters['geographic']:
            # way more complicated to get resolution in metre (we estimate for center of raster)
            center_lat = self.src_parameters['bbox'].top - \
                         (self.src_parameters['height'] / 2 * self.src_parameters['res'][1])
            src_res = (EARTH_CIRCUMFERENCE_METRE * np.cos(np.radians(center_lat)) / 360 * self.src_parameters['res'][0],
                       np.round(EARTH_CIRCUMFERENCE_METRE / 360, 0) * self.src_parameters['res'][1])
        else:
            raise RuntimeError('Input dataset neither projected nor geographic coordinate system')

        if src_res == (float(self.ref_profile['transform'].a),
                       float(abs(self.ref_profile['transform'].e))):
            res_case = 'same'
        elif (src_res[0] * src_res[1]) > (float(self.ref_profile['transform'].a) *
                                          float(abs(self.ref_profile['transform'].e))):
            res_case = 'up-sampling'  # higher resolution of the image is needed to get reference image specs
        else:
            res_case = 'down-sampling'  # lower resolution of the image is needed to get reference image specs

        # Second, check processing method
        if self.src_parameters['epsg'] == self.ref_profile['crs'].to_epsg():
            if res_case == 'same':
                processing_case = 'crop'
            else:
                processing_case = 'resample'
        else:
            processing_case = 'warp'

        return res_case, processing_case

    def VolumeWarp2AOI(self, path_in, path_out, wOT='Float64', oversampling_factor=10, secure_run=False):
        """ Special case for warping raster with absolute volume based data
            Note: still based on gdalwarp but in a 3 step approach which works for up-sampling and down-sampling
            IMPORTANT: since gdal_translate has no "sum" resample method we also use this approach
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)
        # yet another check - if we have an absolute_volume datatype then we need a check that we have projected
        # coordinate system and a known unit --> otherwise we got issues with the pixel area needed
        self._check3(path_in)
        # TODO: implement Volume-based processing for raster with geographic coordinate systems
        # create a raster in src file resolution and extent which holds the px area as value for each pixel
        # also warp this file with oversampling --> so we know for each warped and oversampled pixel the
        # original value and original pixel area --> each pixel value can now correctly scaled
        # TODO: implement volume-based processing for projected raster with non-metre units
        # first translate src dataset into raster with metre unit in original resolution and scale the values

        # resolve ESRI / EPSG issue
        if self.src_parameters['epsg'] in ESRI_IDENTIFIER:
            s_srs_string = 'ESRI:{}'.format(self.src_parameters['epsg'])
        else:
            s_srs_string = 'EPSG:{}'.format(self.src_parameters['epsg'])

        # 1. warp to target EPSG with oversampled resolution (nearest neighbor) (crop to ref file with buffer of 10 px)
        buffer_x = 10 * self.ref_profile['transform'].a
        buffer_y = 10 * abs(self.ref_profile['transform'].e)

        path_temp1 = os.path.join(self.temp_dir, 'VolumeData_helper_file1.tif')
        cmd1 = 'gdalwarp --config GDAL_CACHEMAX 256 -s_srs "{}" -t_srs "EPSG:{}" -te {} {} {} {} -tr {} {} ' \
               '-r {} -et 0 -ot {} -ovr None ' \
               '-wo SAMPLE_STEPS=50 -wo SOURCE_EXTRA=5 -wo SAMPLE_GRID=YES ' \
               '-co COMPRESS=DEFLATE -co INTERLEAVE=BAND -co BIGTIFF=YES -multi -co TILED=YES ' \
               '-overwrite "{}" "{}"'.format(s_srs_string,
                                             self.ref_profile['crs'].to_epsg(),
                                             self.ref_extent.left - buffer_x,
                                             self.ref_extent.bottom - buffer_y,
                                             self.ref_extent.right + buffer_x,
                                             self.ref_extent.top + buffer_y,
                                             float(self.ref_profile['transform'].a) / oversampling_factor,
                                             float(abs(self.ref_profile['transform'].e)) / oversampling_factor,
                                             'near',
                                             wOT,
                                             path_in,
                                             path_temp1)

        # 2. adjust raster values to oversampling rate (gdal_calc)
        path_temp2 = os.path.join(self.temp_dir, 'VolumeData_helper_file2.tif')
        cmd2 = '{} -A "{}" --outfile="{}" --calc="A/({}*{})" --overwrite --quiet ' \
               '--co="INTERLEAVE=BAND" --co="COMPRESS=DEFLATE" ' \
               '--co="TILED=YES"'.format(_GDAL_CALC, path_temp1, path_temp2,
                                         self.src_parameters['res'][0] / (float(self.ref_profile['transform'].a) /
                                                                          oversampling_factor),
                                         self.src_parameters['res'][1] / (float(abs(self.ref_profile['transform'].e)) /
                                                                          oversampling_factor))

        # 3. use 'sum' resampling mode in warp method to get final results (gdalwarp)
        cmd3 = 'gdalwarp --config GDAL_CACHEMAX 256 -t_srs "EPSG:{}" -te {} {} {} {} -tr {} {} ' \
               '-r {} -et 0 -ot {} -ovr None ' \
               '-wo SAMPLE_STEPS=50 -wo SOURCE_EXTRA=5 -wo SAMPLE_GRID=YES ' \
               '-co COMPRESS=DEFLATE -co INTERLEAVE=BAND -co BIGTIFF=YES -multi -co TILED=YES ' \
               '-overwrite "{}" "{}"'.format(self.ref_profile['crs'].to_epsg(),
                                             self.ref_extent.left,
                                             self.ref_extent.bottom,
                                             self.ref_extent.right,
                                             self.ref_extent.top,
                                             float(self.ref_profile['transform'].a),
                                             float(abs(self.ref_profile['transform'].e)),
                                             'sum',
                                             wOT,
                                             path_temp2,
                                             path_out)

        log_message = '* file {} was warped to AOI and resampled to target resolution'.format(os.path.basename(path_in))

        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd1, shell=True)
                subprocess.check_call(cmd2, shell=True)
                subprocess.check_call(cmd3, shell=True)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                raise OSError('Could not warp the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
                if os.path.exists(path_temp1):
                    os.remove(path_temp1)
                if os.path.exists(path_temp2):
                    os.remove(path_temp2)
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def Crop2AOI(self, path_in, path_out, wResampling='nearest', wOT='Float64', secure_run=False):
        """Crop raster to AOI when in same coordinate system and same resolution. no checks are made

        Note: Nodata value is kept but dtype of output can be adjusted."""
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # first resolve gdal_translate and gdalwarp language issue
        if wResampling == 'near':
            wResampling = 'nearest'

        # Note: the resampling method was added to allow switches to different projection units even when theoretically
        #       no resampling is done (e.g. image in projection with kilometer unit and resolution 1 is same as
        #       reference in metre projection and resolution of 1000)
        if self.src_parameters['overwrite_s_srs']:
            # that case is needed since when input raster had no valid EPSG then gdal command gives wired results
            cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                  '-co INTERLEAVE=BAND -ot {} -projwin {} {} {} {} -projwin_srs "EPSG:{}" -a_srs "EPSG:{}" ' \
                  '-r {} -tr {} {} "{}" "{}"'.format(wOT,
                                                     self.ref_extent.left,
                                                     self.ref_extent.top,
                                                     self.ref_extent.right,
                                                     self.ref_extent.bottom,
                                                     self.ref_profile['crs'].to_epsg(),
                                                     self.ref_profile['crs'].to_epsg(),
                                                     wResampling,
                                                     float(self.ref_profile['transform'].a),
                                                     float(abs(self.ref_profile['transform'].e)),
                                                     path_in,
                                                     path_out)
        else:
            cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                  '-co INTERLEAVE=BAND -ot {} -projwin {} {} {} {} -r {} -tr {} {} ' \
                  '"{}" "{}"'.format(wOT,
                                     self.ref_extent.left,
                                     self.ref_extent.top,
                                     self.ref_extent.right,
                                     self.ref_extent.bottom,
                                     wResampling,
                                     float(self.ref_profile['transform'].a),
                                     float(abs(self.ref_profile['transform'].e)),
                                     path_in,
                                     path_out)
        log_message = '* file {} was successfully cropped to AOI'.format(os.path.basename(path_in))

        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd, shell=True)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                raise OSError(
                    'Could not crop the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def Translate2AOI(self, path_in, path_out, wResampling='nearest', wOT='Float64', secure_run=False,
                      mode=None):
        """Translate raster (resampling and cropping) to AOI when in same coordinate system. no checks are made

        Note: Nodata value is kept but dtype of output can be adjusted."""
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # first resolve gdal_translate and gdalwarp language issue
        if wResampling == 'near':
            wResampling = 'nearest'

        # due to gdal_translate sub-pixel shifts we have to distinguish between up & down sampling
        if mode is None:
            mode, _ = self._get_case_parameters()
        if mode == 'same':
            # that is a Crop case
            self.Crop2AOI(path_in, path_out, wOT=wOT, secure_run=False)
            return

        # now the different approaches
        if mode == 'up-sampling':
            # use 2-step approach since gdal_translate first crop and then resample which leds to shifts
            # get the index of UL and LR corner for reference file in existing input file
            UL_row, UL_column = coord_2_index(self.ref_extent.left, self.ref_extent.top,
                                              self.src_parameters['bbox'].left, self.src_parameters['bbox'].top,
                                              self.src_parameters['res'])
            LR_row, LR_column = coord_2_index(self.ref_extent.right, self.ref_extent.bottom,
                                              self.src_parameters['bbox'].left, self.src_parameters['bbox'].top,
                                              self.src_parameters['res'])
            # add buffer of 1 pixel in input file resolution
            if UL_column >= 1:
                UL_column -= 1
            if UL_row >= 1:
                UL_row -= 1
            if LR_column < self.src_parameters['width']:
                LR_column += 1
            if LR_row < self.src_parameters['height']:
                LR_row += 1

            # create CMD for 1. step - resampling a bigger area as needed
            path_temp = os.path.join(self.temp_dir, 'translation_helper_file.tif')
            # again the check is needed if we have a raster file without valid EPSG code
            if self.src_parameters['overwrite_s_srs']:
                # that case is needed since when input raster had no valid EPSG then gdal command gives wired results
                cmd_pre = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                          '-a_srs "EPSG:{}" -co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -srcwin {} {} {} {} ' \
                          '"{}" "{}"'.format(self.ref_profile['crs'].to_epsg(),
                                             wOT,
                                             float(self.ref_profile['transform'].a),
                                             float(abs(self.ref_profile['transform'].e)),
                                             wResampling,
                                             UL_column,
                                             UL_row,
                                             LR_column - UL_column,
                                             LR_row - UL_row,
                                             path_in,
                                             path_temp)
            else:
                cmd_pre = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                          '-co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -srcwin {} {} {} {} ' \
                          '"{}" "{}"'.format(wOT,
                                             float(self.ref_profile['transform'].a),
                                             float(abs(self.ref_profile['transform'].e)),
                                             wResampling,
                                             UL_column,
                                             UL_row,
                                             LR_column - UL_column,
                                             LR_row - UL_row,
                                             path_in,
                                             path_temp)

            # create CMD for 2. step - the cut to the extent
            cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES -co INTERLEAVE=BAND ' \
                  '-ot {} -projwin {} {} {} {} ' \
                  '"{}" "{}"'.format(wOT,
                                     self.ref_extent.left,
                                     self.ref_extent.top,
                                     self.ref_extent.right,
                                     self.ref_extent.bottom,
                                     path_temp,
                                     path_out)
            log_message = '* file {} was cropped to AOI & resampled to target resolution in 2-step approach'.format(
                os.path.basename(path_in))
        elif mode == 'down-sampling':
            cmd_pre = None
            path_temp = os.path.join(self.temp_dir, 'translation_helper_file.tif')

            # again the check is needed if we have a raster file without valid EPSG code
            if self.src_parameters['overwrite_s_srs']:
                # that case is needed since when input raster had no valid EPSG then gdal command gives wired results
                cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                      '-a_srs "EPSG:{}" -co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -projwin {} {} {} {} ' \
                      '"{}" "{}"'.format(self.ref_profile['crs'].to_epsg(),
                                         wOT,
                                         float(self.ref_profile['transform'].a),
                                         float(abs(self.ref_profile['transform'].e)),
                                         wResampling,
                                         self.ref_extent.left,
                                         self.ref_extent.top,
                                         self.ref_extent.right,
                                         self.ref_extent.bottom,
                                         path_in,
                                         path_out)
            else:
                cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                      '-co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -projwin {} {} {} {} ' \
                      '"{}" "{}"'.format(wOT,
                                         float(self.ref_profile['transform'].a),
                                         float(abs(self.ref_profile['transform'].e)),
                                         wResampling,
                                         self.ref_extent.left,
                                         self.ref_extent.top,
                                         self.ref_extent.right,
                                         self.ref_extent.bottom,
                                         path_in,
                                         path_out)
            log_message = '* file {} was cropped to AOI & resampled to target resolution'.format(
                os.path.basename(path_in))
        else:
            raise RuntimeError(f'this mode {mode} is currently not forseen.')

        if not os.path.exists(path_out):
            try:
                if cmd_pre is not None:
                    subprocess.check_call(cmd_pre, shell=True)
                subprocess.check_call(cmd, shell=True)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                raise OSError(
                    'Could not translate the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
                # remove temp files
                if os.path.exists(path_temp):
                    os.remove(path_temp)
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def Warp2AOI(self, path_in, path_out, wResampling='near', wOT='Float64', secure_run=False):
        """Warp a file to an AOI without any checks.

        Note: nodata value stays the same, but dtype can be adjusted."""
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # first resolve gdal_translate and gdalwarp language issue
        if wResampling == 'nearest':
            wResampling = 'near'

        # resolve ESRI / EPSG issue
        if self.src_parameters['epsg'] in ESRI_IDENTIFIER:
            s_srs_string = 'ESRI:{}'.format(self.src_parameters['epsg'])
        else:
            s_srs_string = 'EPSG:{}'.format(self.src_parameters['epsg'])

        cmd = 'gdalwarp --config GDAL_CACHEMAX 256 -s_srs "{}" -t_srs "EPSG:{}" -te {} {} {} {} -tr {} {} -r {} ' \
              '-et 0 -ot {} -ovr None ' \
              '-wo SAMPLE_STEPS=50 -wo SOURCE_EXTRA=5 -wo SAMPLE_GRID=YES ' \
              '-co COMPRESS=DEFLATE -co INTERLEAVE=BAND -co BIGTIFF=YES -multi -co TILED=YES ' \
              '-overwrite "{}" "{}"'.format(s_srs_string,
                                            self.ref_profile['crs'].to_epsg(),
                                            self.ref_extent.left,
                                            self.ref_extent.bottom,
                                            self.ref_extent.right,
                                            self.ref_extent.top,
                                            float(self.ref_profile['transform'].a),
                                            float(abs(self.ref_profile['transform'].e)),
                                            wResampling,
                                            wOT,
                                            path_in,
                                            path_out)
        log_message = '* file {} was warped to AOI and resampled to target resolution'.format(os.path.basename(path_in))

        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd, shell=True)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                raise OSError('Could not warp the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def adapt_file_metadata(self, path_in, path_out):
        """Write metadata extracted from input file to raster file."""
        # get metadata of input file (adapted ones - not only original)
        self.metadata.read_raster_tags([os.path.normpath(path_in)])
        tags = self.metadata.prepare_raster_tags(
            'Original file: {}. Warped/translated to EPSG, resolution and extent of ENCA AOI'.format(
                os.path.basename(os.path.normpath(path_in))), '')

        with rasterio.open(path_out, 'r+') as dst:
            dst.update_tags(**tags)

    def check_raster_contains_ref_extent(self, raster_path):
        """Check if the bounding box of a given raster contains the bounding box of the reference grid."""
        raster_parameters = self._load_profile(raster_path)
        self._epsg_check(raster_parameters, raster_path)

        df_raster = gpd.GeoDataFrame({'id': 1, 'geometry': [shapely.geometry.box(*raster_parameters['bbox'])]})
        df_raster.crs = f'EPSG:{raster_parameters["epsg"]}'

        # Transform raster bounding box to reference coordinate system if needed.
        # TODO : Only tranforming the 4 corners of the bounding box may not be sufficiently accurate for some
        #  coordinate transforms.  Better to create a polygon from a list of points on the bounding box and transform
        #  that.
        ref_epsg = self.ref_profile['crs'].to_epsg()
        if ref_epsg != raster_parameters['epsg']:
            df_raster.to_crs(crs=self.ref_profile['crs'], inplace=True)

        bbox_ref = shapely.geometry.box(*self.ref_extent)

        logger.debug('Raster bbox: %s\nref bbox: %s', df_raster.loc[0, 'geometry'], bbox_ref)
        if not bbox_ref.within(df_raster.loc[0, 'geometry']):
            raise Error(f'Raster file {raster_path} does not contain the complete reference extent.  Please provide a '
                        f'raster file with a minimum extent of {self.ref_extent} (in EPSG:{ref_epsg}).')

    def vector_in_raster_extent_check(self, raster_path, gdf, check_projected=True, check_unit=True, stand_alone=False):
        """Check a set of geographical regions is included in the raster bounds of a given file.
        Note: no content check in raster  is done - so regions could be nodata.
        IMPORTANT: the input raster info is saved after the check in case further processing (Bring2AOI) is needed

        :param raster_path: path to the raster file we want to check.
        :param gdf: GeoDataFrame containing shapes which should be within the raster bounds.
        :param check_projected: also check if raster has a valid projected coordinate system (optional)
        :param check_unit: also check if the raster has a coordinate unit allowed (optional)
        :param stand_alone: functions doesn't store the input profile for further processing steps
        :return: bool
        """
        # always freshly load the profile info
        self.src_parameters = self._load_profile(raster_path)
        # check for main current geoprocessing limitation
        self._epsg_check(self.src_parameters['epsg'], raster_path)

        # check if raster coordinate system is projected or geographic
        if check_projected:
            if self.src_parameters['projected'] is False:
                raise Error(f'Currently the ENCA tool only supports raster maps ({raster_path}) with a projected ' +
                            'coordinate system.')
        # check if linear_units is in list we currently support
        if check_unit:
            if self.src_parameters['unit'] not in LIST_UNITS:
                raise Error(
                    f'Raster map ({raster_path}) coordinate system unit is not supported. Currently the ENCA tool' +
                    ' only supports projected coordinate systems in the following units: ' + ', '.join(LIST_UNITS))

        # bring bbox of raster into a geopandas DataFrame for easier handling
        df_raster = gpd.GeoDataFrame({"id": 1, "geometry": [shapely.geometry.box(*self.src_parameters['bbox'])]})
        # assign the GeoDataFrame the input EPSG code
        df_raster.crs = 'EPSG:{}'.format(self.src_parameters['epsg'])

        # check if we have to reproject something
        # Note: we always convert the raster AOI to avoid issues to change GeoDataFrame projections in the whole project
        # also gives an error if the GeoDataFrame has no valid epsg code
        if gdf.crs.to_epsg() != self.src_parameters['epsg']:
            df_raster.to_crs(epsg=gdf.crs.to_epsg(), inplace=True)

        # reset src_parameters if needed - otherwise further geoprocessing is possible without parameter extraction
        if stand_alone:
            self.src_parameters = {}

        # now we finally can check if all polygons in the GeoDataFrame are within the raster bbox
        if not gdf.geometry.within(df_raster.loc[0, 'geometry']).all():
            logger.warning('Not all needed shapes specified by the GeoDataFrame are included in ' +
                           'the provided raster file {}'.format(raster_path))
            return False
        else:
            return True

    def crop_2_reporting_AOI(self, src_path, dst_path, path_mask, add_progress=lambda p: None,
                             block_shape=(4096, 4096)):
        """Mask an input raster by given raster and crop to reporting extent.

        Note: original metadata will be taken over

        :param src_path: path to raster which will be masked and cropped (mainly in statistical raster configuration)
        :param dst_path: path to output raster
        :param path_mask: path to the masking raster (dimension must be as src_path)
        :param add_progress: to use the progressbar in QGIS
        :param block_shape: size of blocks for processing
        """
        # check
        self._check2()
        # run block-wise processing of destination file
        with rasterio.open(src_path, 'r') as src_data, \
                rasterio.open(path_mask, 'r') as ds_mask:
            with rasterio.open(dst_path, 'w', **dict(self.reporting_profile.copy(), dtype=src_data.dtypes[0],
                                                     nodata=src_data.nodatavals[0])) as ds_out:
                nblocks = number_blocks(self.reporting_profile, block_shape)
                # copy tags to dst
                ds_out.update_tags(**src_data.tags())
                for _, dst_window in block_window_generator(block_shape, ds_out.height, ds_out.width):
                    # here comes the magic -> find the processing_block of dst file in the src file
                    dst_win_bounds = ds_out.window_bounds(dst_window)
                    src_window = src_data.window(dst_win_bounds[0], dst_win_bounds[1],
                                                 dst_win_bounds[2], dst_win_bounds[3])
                    # get data & mask
                    aData = src_data.read(1, window=src_window, masked=True)
                    aMask = ds_mask.read(1, window=src_window)
                    aData[aMask == ds_mask.nodata] = ds_out.nodata
                    ds_out.write(aData.filled(ds_out.nodata), window=dst_window, indexes=1)
                    add_progress(100. / nblocks)

    def merge_raster(self, lPathIn, path_out, mode=None):
        """Merge several GeoTiff files into one
           Note: use mainly to combine several tiles or raster with non-overlapping nodata areas

        :param lPathIn: list of paths with all GeoTiff files to merge
        :param path_out: absolute path of the output file name for merged GeoTiff file
        :param mode: processing extent (statistical, regional, None)
        """
        ##firstgenerate the VRT file
        # write paths to a text file
        path_list = os.path.join(self.temp_dir, 'paths.txt')
        with open(path_list, "w") as outfile:
            outfile.write("\n".join(lPathIn))
        # create temp vrt
        path_vrt = os.path.join(self.temp_dir, os.path.basename(path_out).split('.')[0] + '.vrt')
        cmd = 'gdalbuildvrt -input_file_list "{}" -overwrite -q "{}"'.format(path_list, path_vrt)
        if mode is None:
            pass
        elif mode == 'statistic':
            self._check()
            cmd += ' -te {} {} {} {} -tr {} {}'.format(self.ref_extent.left,
                                                       self.ref_extent.bottom,
                                                       self.ref_extent.right,
                                                       self.ref_extent.top,
                                                       float(self.ref_profile['transform'].a),
                                                       float(abs(self.ref_profile['transform'].e)))
        elif mode == 'reporting':
            self._check2()
            cmd += ' -te {} {} {} {} -tr {} {}'.format(self.reporting_extent.left,
                                                       self.reporting_extent.bottom,
                                                       self.reporting_extent.right,
                                                       self.reporting_extent.top,
                                                       float(self.reporting_profile['transform'].a),
                                                       float(abs(self.reporting_profile['transform'].e)))
        else:
            raise RuntimeError('The given mode option is not forseen in merge_raster function')

        try:
            subprocess.check_call(cmd, shell=True)
        except subprocess.CalledProcessError as e:
            raise OSError(f'Could not generate the needed VRT file: {e}.')

        # transfer to GeoTiff
        cmd = 'gdal_translate -strict -co COMPRESS=DEFLATE "{}" "{}"'.format(path_vrt, path_out)
        try:
            subprocess.check_call(cmd, shell=True)
        except subprocess.CalledProcessError as e:
            raise OSError(f'Could not translate the VRT file into raster file: {e}.')
        finally:
            # remove temp files
            if os.path.exists(path_vrt):
                os.remove(path_vrt)
            if os.path.exists(path_list):
                os.remove(path_list)

    def spatial_disaggregation_byArea(self, path_proxy_raster, data, path_area_raster, area_names, path_out,
                                      add_progress=lambda p: None, proxy_sums=None,
                                      processing_info='N/A', unit_info='N/A', block_shape=(2048, 2048)):
        """Given an Area GeoTiff and area-specific total values, disaggregate the totals according to the proxy raster,
        such that the sum of the disaggregated values for the pixels within each Area equals the given total for the
        area. Uses block processing to reduce memory usage.

        :param path_proxy_raster: path to the raster dataset containing the proxy for spatially disaggregation
        :param data: Series containing the area-specific total values to distribute, indexed by area codes
        :param path_area_raster: path to the raster holding the areas for which statistics are extracted
        :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name,
                           value = raster value)
        :param path_out: absolute output file name for spatially disaggregated raster file
        :param add_progress: callback function to update progress bar.
        :param proxy_sums: Optional Series of precomputed sum of proxy data per Area.
        :param processing_info: string describing the processing step (optional)
        :param unit_info:  string describing the unit of the spatially disaggregated totals (optional)
        :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
        :return: DataFrame containing sum of proxy values per region.  Can be reused in subsequent calls to
                 :meth:`spatial_disaggregation_byArea` to save computation effort.

        """
        progress_remain = 100.  # remaining progress to be used for progress bar
        if proxy_sums is None:
            # first get the proxy raster value sum of the different areas for which to disaggregate the table data
            proxy_sums = statistics_byArea(path_proxy_raster, path_area_raster, area_names,
                                           add_progress=lambda p: add_progress(0.4 * p),
                                           # assign 40% of progress to stats
                                           block_shape=block_shape)[SUM]
            progress_remain = 60.  # 40% assigned to the statistics extraction

        # calculate area-specific contribution factor
        # TODO make sure area_names and data have the same index before calling statistics_byArea, so proxy_sums will
        #  be complete! (and the reindex is not needed here anymore)?  Take care that area_names can be a dict (convert to Series first?)!
        dis_factor = (data / proxy_sums.reindex(data.index)).fillna(0)

        # start processing
        with rasterio.open(path_proxy_raster) as src_proxy, \
                rasterio.open(path_area_raster) as src_area:

            dst_profile = src_proxy.profile
            dst_profile.update(dtype=rasterio.float32, nodata=np.nan)
            # number of to process blocks
            nblocks = number_blocks(dst_profile, block_shape)

            # init the output raster file
            with rasterio.open(path_out, 'w', **dst_profile) as dst:
                # add metadata if needed
                self.metadata.read_raster_tags([path_proxy_raster, path_area_raster])
                dst.update_tags(**self.metadata.prepare_raster_tags(processing_info, unit_info))

                # now iterate over the blocks
                for _, window in block_window_generator(block_shape, dst_profile['height'], dst_profile['width']):
                    # read data
                    aProxy = src_proxy.read(1, window=window, masked=True)
                    aArea = src_area.read(1, window=window)

                    # init output block
                    aData = np.full_like(aProxy, dst_profile['nodata'], dtype=dst_profile['dtype'])

                    # check if empty
                    if aProxy.mask.all() or (aArea == 0).all():
                        dst.write(aData, 1, window=window)
                        add_progress(progress_remain / nblocks)
                        continue
                    # reduce workload by only looping over existing areas & ETclasses in the block
                    lAreas = np.unique(aArea[(aArea != 0) & (~aProxy.mask)]).tolist()

                    # loop over the areas to spatially disaggregate the data to the proxy
                    for area_name, area_value in area_names.items():
                        if area_value not in lAreas:
                            continue
                        np.multiply(aProxy.data, dis_factor[area_name],
                                    where=((aArea == area_value) & (~aProxy.mask)), out=aData)
                    # write to disk
                    dst.write(aData, 1, window=window)
                    add_progress(progress_remain / nblocks)  # remaining progress here
        return proxy_sums  # Return proxy_sums so it may be reused by the caller in subsequent disaggregations

    def spatial_disaggregation_byArea_byET(self, path_proxy_raster, data, path_area_raster, area_names,
                                           path_ET_raster, ET_names, path_out,
                                           add_progress=lambda p: None, proxy_sums=None,
                                           processing_info='N/A', unit_info='N/A', block_shape=(2048, 2048)):
        """Given an Area GeoTiff plus EcosystemType Geotiff and area/ET-specific total values, disaggregate the totals
        according to the proxy raster, such that the sum of the disaggregated values for the pixels for each ET class
        within each Area equals the given total for the ET class and area. Uses block processing to reduce memory usage.

        :param path_proxy_raster: path to the raster dataset containing the proxy for spatially disaggregation
        :param data: Series containing the area-ET-specific total values to distribute, MultiIndex is mandatory given
                      first the AreaID (GEO_ID) and second the ETClassID (ECOTYPE).
        :param path_area_raster: path to the raster holding the areas for which statistics are extracted
        :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name,
                           value = raster value)
        :param path_ET_raster: path to raster dataset containing the ET classes for sub-grouping
        :param ET_names: dict or Series mapping raster value to ecosystem types (key/index = ecosystem type,
                         value = raster value)
        :param path_out: absolute output file name for spatially disaggregated raster file
        :param add_progress: callback function to update progress bar.
        :param proxy_sums: Optional Series of precomputed sum of proxy data per Area and per ET.
        :param processing_info: string describing the processing step (optional)
        :param unit_info: string describing the unit of the spatially disaggregated totals (optional)
        :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
        :return: DataFrame containing sum of proxy values per region and per ecosystem type. Can be reused in subsequent
                 calls to :meth:`spatial_disaggregation_byArea_byET` to save computation effort.

        """
        progress_remain = 100.
        if proxy_sums is None:
            # first get the proxy raster value sum of the different areas for which to disaggregate the table data
            proxy_sums = \
                statistics_byArea_byET(path_proxy_raster, path_area_raster, area_names, path_ET_raster, ET_names,
                                       add_progress=lambda p: add_progress(0.4 * p),  # 40% of progress here
                                       block_shape=block_shape)[SUM]
            progress_remain = 60.

        # calculate area-specific contribution factor
        # Note: proxy_sums has a MultiIndex
        # TODO make sure all indices from data appear in area_names and ET_names before calling statistics_byArea_byET,
        #  so proxy_sums exist for every row in data (and the reindex is perhaps not needed here anymore)?
        #  Take care that area_names and ET_names can be dict (convert to Series first?)!
        dis_factor = (data / proxy_sums.reindex(data.index)).fillna(0)

        # start processing
        with rasterio.open(path_proxy_raster) as src_proxy, \
                rasterio.open(path_area_raster) as src_area, \
                rasterio.open(path_ET_raster) as src_ET:

            # get info for block processing and output raster file
            dst_profile = src_proxy.profile
            dst_profile.update(dtype=rasterio.float32, nodata=np.nan)
            # number of to process blocks
            nblocks = number_blocks(dst_profile, block_shape)

            # init the output raster file
            with rasterio.open(path_out, 'w', **dst_profile) as dst:
                # add metadata if needed
                self.metadata.read_raster_tags([path_proxy_raster, path_area_raster, path_ET_raster])
                dst.update_tags(**self.metadata.prepare_raster_tags(processing_info, unit_info))

                # now iterate over the blocks
                for _, window in block_window_generator(block_shape, dst_profile['height'], dst_profile['width']):
                    # read data
                    aProxy = src_proxy.read(1, window=window, masked=True)
                    aArea = src_area.read(1, window=window)
                    aET = src_ET.read(1, window=window)

                    # init output block
                    aData = np.full_like(aProxy, dst_profile['nodata'], dtype=dst_profile['dtype'])

                    # check if empty
                    if aProxy.mask.all() or (aArea == 0).all():
                        dst.write(aData, 1, window=window)
                        add_progress(progress_remain / nblocks)
                        continue
                    # reduce workload by only looping over existing areas & ETclasses in the block
                    lAreas = np.unique(aArea[(aArea != 0) & (~aProxy.mask)]).tolist()
                    lET = np.unique(aET[(aArea != 0) & (~aProxy.mask)]).tolist()

                    # loop over the areas & ETclasses to spatially disaggregate the data to the proxy
                    for area_name, area_value in area_names.items():
                        if area_value not in lAreas:
                            continue
                        for eco_type, eco_id in ET_names.items():
                            if eco_id not in lET:
                                continue
                            np.multiply(aProxy.data, dis_factor[area_name, eco_type],
                                        where=((aArea == area_value) & (aET == eco_id) & (~aProxy.mask)),
                                        out=aData)
                    # write to disk
                    dst.write(aData, 1, window=window)
                    add_progress(progress_remain / nblocks)  # 60% of progress here
        return proxy_sums  # For optional reuse by the caller in subsequent disaggregations


def Bring2COG():
    """Wrapper to run translation from GeoTiff to COG

    # TODO: put in GeoProcessing class

    implement via the rio-cogeo function.::

       from rio_cogeo.cogeo import cog_translate
       from rio_cogeo.profiles import cog_profiles

       def _translate(src_path, dst_path, profile="webp", profile_options={}, **options):
           \"\"\"Convert image to COG.\"\"\"
           # Format creation option (see gdalwarp `-co` option)
           output_profile = cog_profiles.get(profile)
           output_profile.update(dict(BIGTIFF="IF_SAFER"))
           output_profile.update(profile_options)

           # Dataset Open option (see gdalwarp `-oo` option)
           config = dict(
               GDAL_NUM_THREADS="ALL_CPUS",
               GDAL_TIFF_INTERNAL_MASK=True,
               GDAL_TIFF_OVR_BLOCKSIZE="128",
           )

           cog_translate(
               src_path,
               dst_path,
               output_profile,
               config=config,
               in_memory=False,
               quiet=True,
               **options,
           )
           return True

    """
    pass


def statistics_byArea(path_data_raster, path_area_raster, area_names,
                      add_progress=lambda p: None, block_shape=(2048, 2048)):
    """Extract sum and count statistics for all areas given in the area_raster for the data_raster.

    Note: currently only works correctly for raster with absolute data values (relative datasets would need unit and pixel
    area)

    :param path_data_raster: path to the raster dataset containing the absolute values
    :param path_area_raster: path to the raster holding the areas for which statistics are extracted
    :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name,
                       value = raster value)
    :param add_progress: callback function to update progress bar.
    :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
    :return: pandas dataframe with area codes as index and columns for raster value sum and raster pixel count
    """

    # convert area_names to Series, in case it was provided as a dict
    area_names = pd.DataFrame(area_names.items(), columns=[GEO_ID, SHAPE_ID]).set_index(SHAPE_ID)

    # ini pandas DataFrame to hold the results
    df = pd.DataFrame(0., index=area_names.index, columns=[SUM, COUNT], dtype=float)
    df.index.name = SHAPE_ID

    with rasterio.open(path_data_raster) as src_data, \
            rasterio.open(path_area_raster) as src_area:
        # extract raster info for progress bar
        src_profile = src_data.profile
        # number of to process blocks
        nblocks = number_blocks(src_profile, block_shape)

        # now iterate over the blocks of src files and process
        for _, window in block_window_generator(block_shape, src_profile['height'], src_profile['width']):
            # read data; convert to float to avoid overflow when original data is an integer type
            aData = src_data.read(1, window=window, masked=True).astype(float)
            aArea = src_area.read(1, window=window)

            # create valid data mask
            mValid = (aArea != src_area.nodata) & (~aData.mask)
            # check if all is empty
            if (~mValid).all():
                add_progress(100. / nblocks)
                continue

            # bring valid data into DataFrame
            df_window = pd.DataFrame({SHAPE_ID: aArea[mValid].flatten(), SUM: aData[mValid].data.flatten(), COUNT: 1})
            # add block data to master DataFrame
            df = df.add(df_window.groupby(SHAPE_ID).sum(), fill_value=0.)
            add_progress(100. / nblocks)

    result = df.join(area_names).set_index(GEO_ID)
    return result


def statistics_byArea_byET(path_data_raster, path_area_raster, area_names, path_ET_raster, ET_names,
                           add_progress=lambda p: None,
                           block_shape=(2048, 2048)):
    """Extract sum and count statistics for all Et given by ET_raster in areas given in the area_raster for the data_raster.

    Note: currently only works correctly for raster with absolute data values (relative datasets would need
    unit and pixel area)

    :param path_data_raster: path to the raster dataset containing the absolute values
    :param path_area_raster: path to the raster holding the areas for which statistics are extracted
    :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name/area,
                       value = raster value)
    :param path_ET_raster: path to raster dataset containing the ET classes for sub-grouping
    :param ET_names: dict  or Series mapping raster value to ecosystem types (key/index = clear name/ecosystem type,
                     value = raster value)
    :param add_progress: callback function to update progress bar.
    :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
    :return: pandas dataframe with area codes and ET class as multi-index and columns for raster value sum and
             raster pixel count
    """

    # convert area_names and ET_names to Series, in case they were provided as a dict
    area_names = pd.DataFrame(area_names.items(), columns=[GEO_ID, SHAPE_ID]).set_index(SHAPE_ID)
    ET_names = pd.DataFrame(ET_names.items(), columns=[ECOTYPE, ECO_ID]).set_index(ECO_ID)

    # ini pandas DataFrame to hold the results
    index = pd.MultiIndex.from_product([area_names.index, ET_names.index], names=[SHAPE_ID, ECO_ID])
    df = pd.DataFrame(0., index=index, columns=[SUM, COUNT], dtype=float)

    with rasterio.open(path_data_raster) as src_data, \
            rasterio.open(path_area_raster) as src_area, \
            rasterio.open(path_ET_raster) as src_ET:
        # extract raster info for progress bar
        src_profile = src_data.profile
        # number of to process blocks
        nblocks = number_blocks(src_profile, block_shape)

        # now iterate over the blocks of src files and process
        for _, window in block_window_generator(block_shape, src_profile['height'], src_profile['width']):
            # read data
            aData = src_data.read(1, window=window, masked=True).astype(float)
            aArea = src_area.read(1, window=window)
            aET = src_ET.read(1, window=window)

            # create valid data mask
            mValid = (aArea != src_area.nodata) & (~aData.mask) & (aET != src_ET.nodata)
            # check if all is empty
            if (~mValid).all():
                add_progress(100. / nblocks)
                continue

            # bring valid data into DataFrame
            df_window = pd.DataFrame({SHAPE_ID: aArea[mValid].flatten(), ECO_ID: aET[mValid].flatten(),
                                      SUM: aData[mValid].data.flatten(), COUNT: 1})
            # add block data to master DataFrame
            df = df.add(df_window.groupby([SHAPE_ID, ECO_ID]).sum(), fill_value=0.)
            add_progress(100. / nblocks)

    result = df.join(area_names).join(ET_names).set_index([GEO_ID, ECOTYPE])
    return result


def block_window_generator(block_shapes, img_height, img_width):
    """Returns an iterator over a band's block windows and their indexes.

    Block windows are tuples::

        ((row_start, row_stop), (col_start, col_stop))

    For example, ``((0, 2), (0, 2))`` defines a 2 x 2 block at the upper
    left corner of the raster dataset.
    This iterator yields blocks "left to right" and "top to bottom"
    and is similar to Python's ``enumerate()`` in that it also returns
    indexes.
    Main change to default rasterio function is that you can define
    your own block_shape!!!
    """
    # get block_height and block_width separately
    block_h, block_w = block_shapes
    # calculate number of block in row and cloumn to process
    d, m = divmod(img_height, block_h)
    nrows = d + int(m > 0)
    d, m = divmod(img_width, block_w)
    ncols = d + int(m > 0)
    # start generation of windows
    for j in range(nrows):
        # get row_start
        row_start = j * block_h
        # correct block_height if we at end of image
        block_h_corr = min(block_h, img_height - row_start)
        for i in range(ncols):
            # get col_start
            col_start = i * block_w
            # correct block_width if we at end of image
            block_width_corr = min(block_w, img_width - col_start)
            # generate final window and yield result
            yield (j, i), ((row_start, row_start + block_h_corr), (col_start, col_start + block_width_corr))


def number_blocks(profile, block_shape):
    """Calculate the total number of blocks to process by given raster profile and block_shape.

    :param profile: rasterio profile of a raster dataset
    :param block_shape: tuple (y_size, x_size) for size of blocks in block processing
    :return: number of blocks to process
    """
    return math.ceil(profile['height'] * 1.0 / block_shape[0]) * math.ceil(profile['width'] * 1.0 / block_shape[1])


def pixel_area(crs: rasterio.crs.CRS, transform: affine.Affine):
    """Calculate the area of a pixel in units of m2."""
    affine_area = abs(transform.a * transform.e - transform.b * transform.d)
    unit, factor = crs.linear_units_factor
    return affine_area * factor ** 2


def coord_2_index(x, y, raster_x_min, raster_y_max, pixres, raster_x_max=None, raster_y_min=None):
    """Get array indices from given coordinates.

    rasterio makes it easy for us to find the row and column indices of a pixel by given coordinate (it takes the UL
    corner of a pixel into account in this case)..

    .. note::
        This function needs UL corner coordinate of UL pixel in raster and pix-resolution in same crs as input xy to
        work!!!

    .. note::
        if you also give the UL corner coordinate of first LR pixel outside raster then a plausibility check is done
        --> (== BBOX of raster) the x_max or y_min which can never reached since belongs to first pixel outside the
        raster.

    .. note::
        output tuple is directly in numpy indexing order (row, column).
    """

    index_column = int((x - raster_x_min) / float(pixres[0]))
    index_row = int((y - raster_y_max) / float(-pixres[1]))

    if (raster_x_max is None) or (raster_y_min is None):
        return index_row, index_column
    else:
        if (raster_y_min < y <= raster_y_max) and (raster_x_min <= x < raster_x_max):
            return index_row, index_column
        else:
            raise ValueError("The given coordinate is outside the dataset")
