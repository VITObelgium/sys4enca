"""Multi-language, descriptive names for components"""

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

_component_long_names = {
    carbon_agriculture.component: {
        "en": "Carbon: agriculture (harvest)",
        "fr": "Carbone : agriculture (récolte)",
    },
    carbon_npp.component: {
        "en": "Carbon: vegetation productivity (NPP)",
        "fr": "Carbone : productivité de la végétation (PNB)",
    },
    carbon_forest.component: {
        "en": "Carbon: forest stock and wood removal",
        "fr": "Carbone : stock forestier et prélèvement de bois",
    },
    carbon_fire.component: {"en": "Carbon: fire emission", "fr": "Carbone : émission de feu"},
    carbon_soil_erosion.component: {"en": "Carbon: soil erosion", "fr": "Carbone : érosion du sol"},
    carbon_livestock.component: {"en": "Carbon: livestock", "fr": "Carbone : bétail"},
    carbon_soil.component: {"en": "Carbon: soil stock", "fr": "Carbone : stock de sol"},
    carbon_fire_vuln.component: {
        "en": "Carbon: fire vulnerability index",
        "fr": "Carbone : indice de vulnérabilité au feu",
    },
    water_precip_evapo.component: {
        "en": "Water: Precipitation & Evapotranspiration",
        "fr": "Eau : Précipitations & Évapotranspiration",
    },
    water_usage.component: {"en": "Water: Usage", "fr": "Eau : Utilisation"},
    water_river_length_px.component: {"en": "Water: River length", "fr": "Eau : Longueur de la rivière"},
    water_drought_vuln.component: {
        "en": "Water: Drought vulnerability",
        "fr": "Eau : Vulnérabilité à la sécheresse",
    },
    carbon.component: {"en": "Carbon", "fr": "Carbone"},
    water.component: {"en": "Water", "fr": "Eau"},
    infra.component: {"en": "Infrastructure", "fr": "Infrastructure"},
    leac.component: {"en": "Landcover", "fr": "Couverture des terres"},
    total.component: {"en": "Total", "fr": "Total"},
    trend.component: {"en": "Trend", "fr": "Tendance"},
}

def get_component_long_name(component_key, locale="en"):
    """
    Fetches the component name in the specified language.

    :param component_key: The key identifier for the component.
    :param locale: The locale.
    :return: The name of the component in the specified language.
    """
    # The locale is usually in the format 'en_US', 'fr_FR', etc.
    # If you only need the first two characters (e.g., 'en', 'fr')
    language_code = locale[0:2]
    return _component_long_names.get(component_key, {}).get(language_code, "Unknown Component")
