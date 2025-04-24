"""Carbon reporting."""

FOREST_AGB = 'ForestAGB'
FOREST_BGB = 'ForestBGB'
FOREST_LITTER = 'ForestLitter'

SOIL = 'Soil'

LIVESTOCK = 'Livestock'
NPP = 'NPP'
AGRICULTURE_CEREALS = 'Agriculture_cereals'
AGRICULTURE_FIBER = 'Agriculture_fiber'
AGRICULTURE_FRUIT = 'Agriculture_fruit'
AGRICULTURE_OILCROP = 'Agriculture_oilcrop'
AGRICULTURE_PULSES = 'Agriculture_pulses'
AGRICULTURE_ROOTS = 'Agriculture_roots'
AGRICULTURE_CAFE = 'Agriculture_cafe'
AGRICULTURE_VEGETABLES = 'Agriculture_vegetables'
AGRICULTURE_SUGAR = 'Agriculture_sugar'
WOODREMOVAL = 'WoodRemoval'
SOIL_EROSION = 'SoilErosion'
ILUP = 'ILUP'
CEH1 = 'CEH1'
CEH4 = 'CEH4'
CEH6 = 'CEH6'
CEH7 = 'CEH7'
COW = 'Cow'
FIRE = 'Fire'
FIRE_SPLIT = 'FireSplit'
FIRE_INTEN = 'FireInten'

input_codes = dict(
    C1_1=FOREST_AGB,
    C1_2=FOREST_LITTER,
    C1_3_1=FOREST_BGB,
    C1_3_2=SOIL,
    C1_43=LIVESTOCK,
    C2_3=NPP,
    C3_11=AGRICULTURE_CEREALS,
    C3_12=AGRICULTURE_FIBER,
    C3_13=AGRICULTURE_FRUIT,
    C3_14=AGRICULTURE_OILCROP,
    C3_15=AGRICULTURE_PULSES,
    C3_16=AGRICULTURE_ROOTS,
    C3_17=AGRICULTURE_CAFE,
    C3_18=AGRICULTURE_VEGETABLES,
    C3_19=AGRICULTURE_SUGAR,
    C3_4=WOODREMOVAL,
    C4_111=0,
    C4_112=0,
    C4_11b=0,
    C4_33=0,
    C6_2=SOIL_EROSION,
    C10_2ILUP=ILUP,
    CEH1=(CEH1, 1),
    CEH4=CEH4,
    CEH6=(CEH6, 1),
    CEH7=CEH7,
    Cow_in_Liv=COW,
    fire=FIRE,
    fire_ratio=FIRE_SPLIT,
    fire_inten=(FIRE_INTEN, 1)
)  #: Mapping of index column names to config values.

component = 'CARBON'
