from qgis.core import QgsStyle, QgsVectorLayer, QgsProject, QgsClassificationQuantile, QgsGraduatedSymbolRenderer

def load_vector_layer(file, layer_name, attribute_name, color_ramp='Viridis', num_classes=8):
    classification = QgsClassificationQuantile()
    classification.setLabelFormat('%1 - %2')
    classification.setLabelPrecision(4)
    classification.setLabelTrimTrailingZeroes(True)

    layer = QgsVectorLayer(file, layer_name)

    renderer = QgsGraduatedSymbolRenderer()
    renderer.setClassAttribute(attribute_name)
    renderer.setClassificationMethod(classification)
    renderer.updateClasses(layer, num_classes)
    renderer.updateColorRamp(QgsStyle().defaultStyle().colorRamp(color_ramp))

    layer.setRenderer(renderer)
    QgsProject().instance().addMapLayer(layer)
