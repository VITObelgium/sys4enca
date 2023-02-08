# Copyright (c) 2022 European Union.
#
# The tool was developed with the contribution of the Joint Research Centre of the European Commission.
#
# This program is free software: you can redistribute it and/or modify it under the terms of the European Union Public
# Licence, either version 1.2 of the License, or (at your option) any later version.
# You may not use this work except in compliance with the Licence.
#
# You may obtain a copy of the Licence at: https://joinup.ec.europa.eu/collection/eupl/eupl-guidelines-faq-infographics
#
# Unless required by applicable law or agreed to in writing, software distributed under the Licence is distributed on
# an "AS IS" basis, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#
# See the Licence for the specific language governing permissions and limitations under the Licence.

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def reclassification(raster_in, dict_classes, nodata_in, nodata_out, outputtype=None, fverbose=True):
    """Run a reclassification based on a provided dictionary."""
    # get an overview of all valid values in input raster
    lValues = np.unique(raster_in).tolist()

    # check if for each original value an assignment in the dictionary exists
    # if not then fill with new nodata value
    # I know not really need to work, but maybe helpful if we later want a list of all unique raster_in values
    if nodata_in in lValues:
        lValues.remove(nodata_in)
    for element in lValues:
        if element not in dict_classes.keys():
            if fverbose:
                logger.warning('Warning: for class %s no new class was assigned. Will be set to new nodata value',
                               element)
            # insert the new value in dictionary
            dict_classes[element] = nodata_out

    # now we reclassify the input raster
    if outputtype is None:
        raster_out = np.full_like(raster_in, nodata_out)
    else:
        raster_out = np.full_like(raster_in, nodata_out, dtype=outputtype)

    for key in lValues:
        raster_out[raster_in == key] = dict_classes[key]

    return raster_out, dict_classes


def CSV_2_dict(path_csv, old_class='old_class', new_class='new_class'):
    """Convert the information for reclassification stored in a CSV to a usable dict.

    .. note::
       function need the columns "old_class" and "new_class"
    """

    df = pd.read_csv(path_csv, comment='#')
    # check that the needed columns are existing
    if (new_class in df.columns.values.tolist()) and (old_class in df.columns.values.tolist()):
        dClasses = {}
        for row in df.itertuples(index=False):
            dClasses[row[df.columns.get_loc(old_class)]] = row[df.columns.get_loc(new_class)]
        return dClasses
    else:
        raise ValueError('the needed column names for the reclassification assignment are not available.')
