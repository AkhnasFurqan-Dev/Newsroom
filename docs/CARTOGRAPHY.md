# Cartography sources and conventions

Channel Press does not generate geographic shapes with a language model.

## Bundled source data

- **Natural Earth 1:50m Admin 0 Countries** (`ne_50m_admin_0_countries.geojson`),
  public domain, obtained from the Natural Earth vector repository.
- **Natural Earth 1:10m Admin 0 Disputed Areas**
  (`ne_10m_admin_0_disputed_areas.geojson`), public domain.

The build records the file name, dataset name and license in every edition's
configuration snapshot and on the printed map itself. Maps are locator maps
rendered from those layers; they are not survey or operational products.

## Profiles

- `neutral` — Natural Earth country polygons, disputed areas shown with a
  neutral dashed outline and a disclosure note. No claim is adjudicated.
- `pakistan` — Natural Earth country polygons plus the publisher's chosen
  convention: the map legend states that the depiction follows the publication's
  Pakistan cartographic convention and that territorial status is disputed.
  It does not silently redraw a boundary.
- `egypt` — Natural Earth polygons with an Egypt-convention legend for editions
  aimed at that audience.

Profiles change the published convention/legend and the chosen source layer; they
never invent geometry or override evidence. If a requested location cannot be
supported by the bundled layers, the layout uses a sourced text location box
instead of a fake map.

## Regenerating

The files are committed because they are small, public domain and needed offline.
To refresh, download the same Named Earth GeoJSON layers from
<https://github.com/nvkelso/natural-earth-vector/tree/master/geojson> and record
the new checksums in the edition manifest.
