"""Registry of all available ENCA components."""

from enca.framework.errors import ConfigError

from .carbon import Carbon
from .carbon.agriculture import CarbonAgriculture
from .carbon.fire import CarbonFire
from .carbon.fire_vuln import CarbonFireVulnerability
from .carbon.forest import CarbonForest
from .carbon.livestock import CarbonLivestock
from .carbon.npp import CarbonNPP
from .carbon.soil import CarbonSoil
from .carbon.soil_erosion import CarbonErosion
from .infra import Infra
from .leac import Leac
from .total import Total
from .trend import Trend
from .water import Water
from .water.drought_vuln import DroughtVuln
from .water.precipitation_evapotranspiration import WaterPrecipEvapo
from .water.river_length_pixel import RiverLength
from .water.usage import Usage

COMPONENT = 'component'
_run_components = {CarbonAgriculture, CarbonFire, CarbonFireVulnerability, CarbonForest, CarbonLivestock, CarbonNPP,
                   CarbonSoil, CarbonErosion,
                   WaterPrecipEvapo, Usage, RiverLength, DroughtVuln,
                   Carbon, Infra, Leac, Water,
                   Total, Trend}  #: List of all ENCA components we can run.

# Build a dict of {'component name': class} for all run components, so we can easily start a run given it's component
# name.
_component_registry = {cls.component: cls for cls in _run_components}

# Dict of descriptive names for components:
_component_long_names = {
    CarbonAgriculture.component: {
        "en": "Carbon: agriculture (harvest)",
        "fr": "Carbone : agriculture (récolte)",
    },
    CarbonNPP.component: {
        "en": "Carbon: vegetation productivity (NPP)",
        "fr": "Carbone : productivité de la végétation (PNB)",
    },
    CarbonForest.component: {
        "en": "Carbon: forest stock and wood removal",
        "fr": "Carbone : stock forestier et prélèvement de bois",
    },
    CarbonFire.component: {"en": "Carbon: fire emission", "fr": "Carbone : émission de feu"},
    CarbonErosion.component: {"en": "Carbon: soil erosion", "fr": "Carbone : érosion du sol"},
    CarbonLivestock.component: {"en": "Carbon: livestock", "fr": "Carbone : bétail"},
    CarbonSoil.component: {"en": "Carbon: soil stock", "fr": "Carbone : stock de sol"},
    CarbonFireVulnerability.component: {
        "en": "Carbon: fire vulnerability index",
        "fr": "Carbone : indice de vulnérabilité au feu",
    },
    WaterPrecipEvapo.component: {
        "en": "Water: Precipitation & Evapotranspiration",
        "fr": "Eau : Précipitations & Évapotranspiration",
    },
    Usage.component: {"en": "Water: Usage", "fr": "Eau : Utilisation"},
    RiverLength.component: {"en": "Water: River length", "fr": "Eau : Longueur de la rivière"},
    DroughtVuln.component: {
        "en": "Water: Drought vulnerability",
        "fr": "Eau : Vulnérabilité à la sécheresse",
    },
    Carbon.component: {"en": "Carbon", "fr": "Carbone"},
    Water.component: {"en": "Water", "fr": "Eau"},
    Infra.component: {"en": "Infrastructure", "fr": "Infrastructure"},
    Leac.component: {"en": "Landcover", "fr": "Couverture des terres"},
    Total.component: {"en": "Total", "fr": "Total"},
    Trend.component: {"en": "Trend", "fr": "Tendance"},
}


def make_run(config):
    """Read the component from config, and create a Run object for that component."""
    if COMPONENT not in config:
        raise ConfigError('Config does not contain an ENCA component name.', [COMPONENT])

    component_name = config[COMPONENT]
    if component_name not in _component_registry:
        raise ConfigError(f'Unknown ENCA component name {component_name}.', [COMPONENT])

    return _component_registry[component_name](config)


def list_components():
    """Return a list of all known components."""
    return list(_component_registry.keys())


def get_component(component_name):
    """Return the class for the component with the given name."""
    return _component_registry[component_name]


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
