"""Types of ENCA runs"""

from enum import Enum

import enca_plugin.cfg.carbon as carbon
import enca_plugin.cfg.infra as infra
import enca_plugin.cfg.leac as leac
import enca_plugin.cfg.water as water

import enca_plugin.cfg.carbon.agriculture as carbon_agriculture
import enca_plugin.cfg.carbon.fire as carbon_fire
import enca_plugin.cfg.carbon.fire_vuln as carbon_fire_vuln
import enca_plugin.cfg.carbon.forest as carbon_forest
import enca_plugin.cfg.carbon.livestock as carbon_livestock
import enca_plugin.cfg.carbon.npp as carbon_npp
import enca_plugin.cfg.carbon.soil as carbon_soil
import enca_plugin.cfg.carbon.soil_erosion as carbon_soil_erosion

import enca_plugin.cfg.water.drought_vuln as water_drought_vuln
import enca_plugin.cfg.water.precipitation_evapotranspiration as water_precip_evapo
import enca_plugin.cfg.water.river_length_pixel as water_river_length_px
import enca_plugin.cfg.water.usage as water_usage

import enca_plugin.cfg.total as total
import enca_plugin.cfg.trend as trend


class RunType(Enum):
    ENCA       = 0  #: Regular run for a single component.
    ACCOUNT    = 1  #: Yearly account or trend.
    PREPROCESS = 2  #: Preprocessing.

component_run_types = {
    carbon_agriculture.component   : RunType.PREPROCESS,
    carbon_npp.component           : RunType.PREPROCESS,
    carbon_forest.component        : RunType.PREPROCESS,
    carbon_fire.component          : RunType.PREPROCESS,
    carbon_soil_erosion.component  : RunType.PREPROCESS,
    carbon_livestock.component     : RunType.PREPROCESS,
    carbon_soil.component          : RunType.PREPROCESS,
    carbon_fire_vuln.component     : RunType.PREPROCESS,
    water_precip_evapo.component   : RunType.PREPROCESS,
    water_usage.component          : RunType.PREPROCESS,
    water_river_length_px.component: RunType.PREPROCESS,
    water_drought_vuln.component   : RunType.PREPROCESS,
    carbon.component: RunType.ENCA,
    water.component : RunType.ENCA,
    infra.component : RunType.ENCA,
    leac.component  : RunType.ENCA,
    total.component : RunType.ACCOUNT,
    trend.component : RunType.ACCOUNT,
}