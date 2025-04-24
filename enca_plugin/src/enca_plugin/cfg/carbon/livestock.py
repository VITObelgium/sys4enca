CATTLE = 'cattle'
CHICKEN = 'chicken'
SHEEP = 'sheep'
GOAT = 'goats'
PIG = 'pigs'

LIVESTOCK_DIST = 'livestock_distribution'
LIVESTOCK_CARBON = 'livestock_carbon'
WEIGHTS = 'weights'

DWF = 'DWF'

livestock_types = [CATTLE, CHICKEN, SHEEP, GOAT, PIG]

_livestock_long_names = {
    CATTLE: {
        "en": "Cattle",
        "fr": "Bovins",
    },
    CHICKEN: {
        "en": "Chicken",
        "fr": "Poulet",
    },
    SHEEP: {
        "en": "Sheep",
        "fr": "Mouton",
    },
    GOAT: {
        "en": "Goat",
        "fr": "Chèvre",
    },
    PIG: {
        "en": "Pig",
        "fr": "Porc",
    },
}

component = 'CARBON_LIVESTOCK'

def get_livestock_long_name(livestock_key, locale="en"):
    """
    Fetches the component name in the specified language.

    :param livestock_key: The key identifier for the component.
    :param locale: The locale
    :return: The name of the component in the specified language.
    """
    # The locale is usually in the format 'en_US', 'fr_FR', etc.
    # If you only need the first two characters (e.g., 'en', 'fr')
    language_code = locale[0:2]
    return _livestock_long_names.get(livestock_key, {}).get(language_code, "Unknown livestock type")
