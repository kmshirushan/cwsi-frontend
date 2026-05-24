import ee


def init_gee(service_account_file: str):
    """Initializes GEE using the Service Account JSON key."""
    credentials = ee.ServiceAccountCredentials('', service_account_file)
    ee.Initialize(credentials)
    print("🛰️  Earth Engine Engine Initialized Successfully.")


def get_ndvi_image(aoi: ee.Geometry, target_date: str) -> ee.Image:
    """
    Fetches a cloud-free Sentinel-2 NDVI composite.
    Dynamically expands the search window if extreme cloud cover is detected.
    """
    # Try a standard 15-day window first
    days_buffer = 15
    start = ee.Date(target_date).advance(-days_buffer, "day")
    end = ee.Date(target_date).advance(days_buffer, "day")

    s2_col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
    )

    # Check if we have data. If empty, expand look-window to 35 days
    if s2_col.size().getInfo() == 0:
        print(f"⚠️  Cloud lockout at 15 days. Expanding search window to 35 days...")
        days_buffer = 35
        start = ee.Date(target_date).advance(-days_buffer, "day")
        end = ee.Date(target_date).advance(days_buffer, "day")

        s2_col = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi)
            .filterDate(start, end)
            # Relax cloud constraint slightly for fallback if needed
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        )

    # Final hard stop if even the wide window is blacked out
    if s2_col.size().getInfo() == 0:
        raise ValueError(
            f"Severe monsoon cloud blackout. No usable Sentinel-2 scenes found within ±{days_buffer} days.")

    def calc_ndvi(img):
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        return img.addBands(ndvi)

    composite = s2_col.map(calc_ndvi).median().clip(aoi)
    return composite.select("NDVI")


def get_lst_image(aoi: ee.Geometry, target_date: str) -> ee.Image:
    """Fetches Landsat Thermal imagery. Dynamically expands search window if clouds are severe."""

    # Helper to calculate Surface Temperature in Kelvin
    def scale_lst(img):
        lst = img.select("ST_B10").multiply(0.00341802).add(149.0)
        return lst.rename("LST").copyProperties(img, ["system:time_start"])

    # Attempt 1: Strict 16-day window, max 20% clouds
    days_buffer = 16
    start = ee.Date(target_date).advance(-days_buffer, "day")
    end = ee.Date(target_date).advance(days_buffer, "day")

    l9 = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2").filterBounds(aoi).filterDate(start, end).filter(
        ee.Filter.lt("CLOUD_COVER", 20))
    l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").filterBounds(aoi).filterDate(start, end).filter(
        ee.Filter.lt("CLOUD_COVER", 20))
    merged = l9.merge(l8)

    # Attempt 2: If empty, expand to 40 days and relax clouds to 40%
    if merged.size().getInfo() == 0:
        print(f"⚠️ Cloud lockout at ±16 days. Expanding Landsat search window to ±40 days...")
        days_buffer = 40
        start = ee.Date(target_date).advance(-days_buffer, "day")
        end = ee.Date(target_date).advance(days_buffer, "day")

        l9_wide = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2").filterBounds(aoi).filterDate(start, end).filter(
            ee.Filter.lt("CLOUD_COVER", 40))
        l8_wide = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").filterBounds(aoi).filterDate(start, end).filter(
            ee.Filter.lt("CLOUD_COVER", 40))
        merged = l9_wide.merge(l8_wide)

    # Final hard stop if completely blacked out
    if merged.size().getInfo() == 0:
        raise ValueError(
            f"Severe cloud blackout. No usable Landsat 8/9 thermal scenes found within ±{days_buffer} days of the target date.")

    return merged.map(scale_lst).median().clip(aoi)


def calculate_tvdi(ndvi: ee.Image, lst: ee.Image, aoi: ee.Geometry) -> ee.Image:
    """Computes the Temperature-Vegetation Dryness Index (TVDI)."""
    # Determine Wet Edge
    lst_min = ee.Number(lst.reduceRegion(
        reducer=ee.Reducer.percentile([5]),
        geometry=aoi,
        scale=30,
        maxPixels=1e8
    ).get("LST"))

    # Extract linear Dry Edge (a + b * NDVI)
    combined = ndvi.addBands(lst)
    fit = combined.reduceRegion(
        reducer=ee.Reducer.linearFit(),
        geometry=aoi,
        scale=30,
        maxPixels=1e8
    )

    a = ee.Number(fit.get("offset"))
    b = ee.Number(fit.get("scale"))

    lst_dry = ndvi.multiply(b).add(a)
    tvdi = (lst.subtract(lst_min)).divide(lst_dry.subtract(lst_min))

    return tvdi.rename("CWSI").clamp(0, 1)