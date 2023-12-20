<?xml version="1.0" encoding="UTF-8"?>
<StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:sld="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" version="1.0.0" xmlns:ogc="http://www.opengis.net/ogc">
  <UserLayer>
    <sld:LayerFeatureConstraints>
      <sld:FeatureTypeConstraint/>
    </sld:LayerFeatureConstraints>
    <sld:UserStyle>
      <sld:Name>ZAEG-SEN-imprinted-filled_2000_NKL-L2-100m_3857_v2</sld:Name>
      <sld:FeatureTypeStyle>
        <sld:Rule>
          <sld:RasterSymbolizer>
            <sld:ChannelSelection>
              <sld:GrayChannel>
                <sld:SourceChannelName>1</sld:SourceChannelName>
              </sld:GrayChannel>
            </sld:ChannelSelection>
            <sld:ColorMap type="values">
              <sld:ColorMapEntry quantity="0" color="#282828" label="no data (0)"/>
              <sld:ColorMapEntry quantity="11" color="#006400" label="Fôret dense (11)"/>
              <sld:ColorMapEntry quantity="12" color="#008c00" label="Fôret claire (12)"/>
              <sld:ColorMapEntry quantity="13" color="#00ffbf" label="Fôret galeries (13)"/>
              <sld:ColorMapEntry quantity="14" color="#ba00ba" label="Mangroves (14)"/>
              <sld:ColorMapEntry quantity="15" color="#84ff7b" label="Savanne boisée et arborée (15)"/>
              <sld:ColorMapEntry quantity="21" color="#ffea00" label="Ssavanna arbustive (21)"/>
              <sld:ColorMapEntry quantity="31" color="#dcff02" label="Savanna herbuese (31)"/>
              <sld:ColorMapEntry quantity="40" color="#f096ff" label="Culture pluviale et bas-fonds (40)"/>
              <sld:ColorMapEntry quantity="41" color="#ff00ff" label="Agrofôret (41)"/>
              <sld:ColorMapEntry quantity="50" color="#fa0000" label="Tissu urbain (50)"/>
              <sld:ColorMapEntry quantity="60" color="#b4b4b4" label="Roche et sol nue (60)"/>
              <sld:ColorMapEntry quantity="61" color="#63653f" label="Mines et carrieres et extration (61)"/>
              <sld:ColorMapEntry quantity="80" color="#0032c8" label="Plan d'eau (80)"/>
              <sld:ColorMapEntry quantity="90" color="#0096a0" label="Prairie humide (90)"/>
              <sld:ColorMapEntry quantity="200" color="#000080" opacity="0" label="Mêr (200)"/>
              <sld:ColorMapEntry quantity="255" color="#000000" opacity="0" label="Non classé (255)"/>
            </sld:ColorMap>
          </sld:RasterSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </UserLayer>
</StyledLayerDescriptor>
