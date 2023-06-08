"""Registry of all available ENCA components."""

from .carbon.fire import CarbonFire
from .carbon.fire_vuln import CarbonFireVulnerability
from .carbon.agriculture import CarbonAgriculture
from .carbon.forest import CarbonForest
from .carbon.npp import CarbonNPP
from .carbon.livestock import CarbonLivestock
from .carbon.soil import CarbonSoil
from .carbon.soil_erosion import CarbonErosion
from .carbon import Carbon
from .infra import Infra
from .leac import Leac
from .water import Water
from .water.precipitation_evapotranspiration import WaterPrecipEvapo
from .water.usage import Usage
from .water.drought_vuln import DroughtVuln
from .water.river_length_pixel import RiverLength
from .total import Total
from .trend import Trend
from enca.framework.config_check import ConfigError

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
    CarbonAgriculture.component: 'Carbon: agriculture (harvest)',
    CarbonNPP.component: 'Carbon: vegetation productivity (NPP)',
    CarbonForest.component: 'Carbon: forest stock and wood removal',
    CarbonFire.component: 'Carbon: fire emission',
    CarbonErosion.component: 'Carbon: soil erosion',
    CarbonLivestock.component: 'Carbon: livestock',
    CarbonSoil.component: 'Carbon: soil stock',
    CarbonFireVulnerability.component: 'Carbon: fire vulnerability index',
    Carbon.component: 'Carbon',
    Water.component: 'Water',
    Infra.component: 'Infra',
    Leac.component: 'LEAC'
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


def get_component_long_name(component_name):
    """Return a human-readable name of the component with the given name."""
    # return entry from _component_long_names if it exists, otherwise return input name
    return _component_long_names.get(component_name, component_name)
