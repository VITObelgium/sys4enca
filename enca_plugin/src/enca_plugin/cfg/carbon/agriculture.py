import pandas as pd

CAFE = 'cafe'
CEREALS = 'cereals'
FIBER = 'fiber'
FRUIT = 'fruit'
OILCROP = 'oilcrop'
PULSES = 'pulses'
ROOTS = 'roots'
SUGAR = 'sugar'
VEGETABLES = 'vegetables'

FOOD = 'food'
NONFOOD = 'non-food'

AGRICULTURE_DISTRIBUTION = 'agriculture_distribution'
AGRICULTURE_STATS = 'agriculture_stats'

EARTH_RADIUS = 6356752.3

_agri_types = [CAFE, CEREALS, FIBER, FRUIT, OILCROP, PULSES, ROOTS, SUGAR, VEGETABLES]

_carbon = {
    CAFE: 0.4,
    CEREALS: 0.4,
    FIBER: 0.4,
    FRUIT: 0.2,
    OILCROP: 0.4,
    PULSES: 0.4,
    ROOTS: 0.3,
    SUGAR: 0.3,
    VEGETABLES: 0.1,
}

component = 'CARBON_AGRICULTURE'

lut_crops = pd.DataFrame(
    columns=['full_name', 'name', 'food/non-food', 'group'],
    data=[['wheat', 'whea', FOOD, CEREALS],
          ['rice', 'rice', FOOD, CEREALS],
          ['maize', 'maiz', FOOD, CEREALS],
          ['barley', 'barl', FOOD, CEREALS],
          ['pearl millet', 'pmil', FOOD, CEREALS],
          ['small millet', 'smil', FOOD, CEREALS],
          ['sorghum', 'sorg', FOOD, CEREALS],
          ['other cereals', 'ocer', FOOD, CEREALS],
          ['potato', 'pota', FOOD, ROOTS],
          ['sweet potato', 'swpo', FOOD, ROOTS],
          ['yams', 'yams', FOOD, ROOTS],
          ['cassava', 'cass', FOOD, ROOTS],
          ['other roots', 'orts', FOOD, ROOTS],
          ['bean', 'bean', FOOD, PULSES],
          ['chickpea', 'chic', FOOD, PULSES],
          ['cowpea', 'cowp', FOOD, PULSES],
          ['pigeonpea', 'pige', FOOD, 'not_used'],
          ['lentil', 'lent', FOOD, 'not_used'],
          ['other pulses', 'opul', FOOD, PULSES],
          ['soybean', 'soyb', FOOD, OILCROP],
          ['groundnut', 'grou', FOOD, OILCROP],
          ['coconut', 'cnut', FOOD, OILCROP],
          ['oilpalm', 'oilp', NONFOOD, OILCROP],
          ['sunflower', 'sunf', NONFOOD, OILCROP],
          ['rapeseed', 'rape', NONFOOD, OILCROP],
          ['sesameseed', 'sesa', NONFOOD, OILCROP],
          ['other oil crops', 'ooil', NONFOOD, OILCROP],
          ['sugarcane', 'sugc', NONFOOD, SUGAR],
          ['sugarbeet', 'sugb', NONFOOD, 'not_used'],
          ['cotton', 'cott', NONFOOD, FIBER],
          ['other fibre crops', 'ofib', NONFOOD, FIBER],
          ['arabica coffee', 'acof', NONFOOD, 'not_used'],
          ['robusta coffee', 'rcof', NONFOOD, CAFE],
          ['cocoa', 'coco', NONFOOD, CAFE],
          ['tea', 'teas', NONFOOD, CAFE],
          ['tobacco', 'toba', NONFOOD, CAFE],
          ['banana', 'bana', FOOD, FRUIT],
          ['plantain', 'plnt', FOOD, FRUIT],
          ['tropical fruit', 'trof', FOOD, FRUIT],
          ['temperate fruit', 'temf', FOOD, FRUIT],
          ['vegetables', 'vege', FOOD, VEGETABLES],
          ['rest of crops', 'rest', NONFOOD, 'not_used']])
