from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
import ee
from gee_pipeline import get_ndvi_image, get_lst_image, calculate_tvdi, init_gee
from fastapi.middleware.cors import CORSMiddleware
import os
from datetime import datetime, timedelta

app = FastAPI(title="Crop Water Stress API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Database Configuration ---
DB_URL =os.environ.get("DATABASE_URL", "")

# --- Pydantic Data Validation Schemas ---
class SpatialArea(BaseModel):
    type: str  # Usually "Polygon"
    coordinates: list  # GeoJSON coordinate nesting


class CWSIRequest(BaseModel):
    field_id: str  # Target field UUID from our database
    date: str  # Target date string (e.g., "2025-02-15")

class FieldRegistration(BaseModel):
    name: str
    geometry: dict  # The GeoJSON polygon from the frontend drawing tool


# --- Background Worker Function ---
# --- Background Worker Function ---
def processing_worker(field_id: str, date: str):
    """Loops backward through 6 months of satellite data, gracefully skipping cloudy months."""
    conn = None
    try:
        init_gee('service-account.json')
        print(f"⚙️ Time-Series Task Started for Field: {field_id} from Date: {date}")

        # 1. Fetch the field geometry from PostGIS
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, name, ST_AsGeoJSON(geom) as geojson FROM fields WHERE id = %s;", (field_id,))
        field = cursor.fetchone()

        if not field:
            return {"success": False, "error": "Field not found in database."}

        import json
        aoi_geometry = json.loads(field['geojson'])
        ee_aoi = ee.Geometry.Polygon(aoi_geometry["coordinates"])

        # Prepare for the 6-month loop
        target_dt = datetime.strptime(date, "%Y-%m-%d")
        successful_months = 0

        # 2. Loop backward in 30-day steps, 6 times total
        for i in range(6):
            step_dt = target_dt - timedelta(days=30 * i)
            step_date_str = step_dt.strftime("%Y-%m-%d")

            print(f"⏳ Analyzing historical step {i + 1}/6: {step_date_str}...")

            try:
                # Run your existing robust GEE pipeline
                ndvi_img = get_ndvi_image(ee_aoi, step_date_str)
                lst_img = get_lst_image(ee_aoi, step_date_str)
                cwsi_img = calculate_tvdi(ndvi_img, lst_img, ee_aoi)

                # Extract metrics
                ndvi_stats = ndvi_img.reduceRegion(reducer=ee.Reducer.mean(), geometry=ee_aoi, scale=10).getInfo()
                lst_stats = lst_img.reduceRegion(reducer=ee.Reducer.mean(), geometry=ee_aoi, scale=30).getInfo()
                cwsi_stats = cwsi_img.reduceRegion(
                    reducer=ee.Reducer.mean().combine(ee.Reducer.percentile([25, 75, 90]), "", True),
                    geometry=ee_aoi, scale=10
                ).getInfo()

                # Extract scalar values safely
                cwsi_mean = cwsi_stats.get("CWSI_mean")
                if cwsi_mean is None:
                    raise ValueError("Polygon is too small for a 30m pixel.")

                cwsi_p25 = cwsi_stats.get("CWSI_p25")
                cwsi_p75 = cwsi_stats.get("CWSI_p75")
                cwsi_p90 = cwsi_stats.get("CWSI_p90")
                ndvi_mean = ndvi_stats.get("NDVI")
                lst_mean_k = lst_stats.get("LST")
                etr_mm = 5.2

                # Save this specific month to PostGIS
                insert_query = """
                INSERT INTO cwsi_obs (field_id, obs_date, cwsi_mean, cwsi_p25, cwsi_p75, cwsi_p90, etr_mm, ndvi_mean, lst_mean_k)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                cursor.execute(insert_query,
                               (field_id, step_date_str, cwsi_mean, cwsi_p25, cwsi_p75, cwsi_p90, etr_mm, ndvi_mean,
                                lst_mean_k))
                conn.commit()

                print(f"✅ Data saved for {step_date_str}")
                successful_months += 1

            except Exception as e:
                # If this specific month is entirely blocked by clouds, skip it and continue to the next!
                print(f"⚠️ Skipping {step_date_str} due to Earth Engine error: {e}")
                continue

        # 3. Final Validation
        if successful_months == 0:
            raise ValueError(
                "Could not extract ANY data for the last 6 months. Polygon is either too small or the area is completely covered by persistent clouds.")

        cursor.close()
        return {"success": True}

    except Exception as e:
        error_msg = str(e)
        print(f"❌ Critical Pipeline Failure: {error_msg}")

        # 🧹 CLEANUP: If the entire 6-month block failed, delete the useless ghost field!
        if conn:
            try:
                cleanup_cursor = conn.cursor()
                cleanup_cursor.execute("DELETE FROM fields WHERE id = %s;", (field_id,))
                conn.commit()
            except Exception as cleanup_error:
                print(f"Cleanup failed: {cleanup_error}")

        return {"success": False, "error": error_msg}
    finally:
        if conn:
            conn.close()

@app.post("/cwsi/compute")
def trigger_cwsi_computation(payload: CWSIRequest):
    """Triggers the computation synchronously to prevent Cloud Run CPU throttling."""
    print(f"⚙️ Starting Earth Engine computation for field {payload.field_id}...")

    # Call worker and check the result
    result = processing_worker(payload.field_id, payload.date)

    if not result["success"]:
        # Raise an HTTP Error so React catches it immediately!
        raise HTTPException(status_code=422, detail=result["error"])

    print(f"✅ Computation finished for field {payload.field_id}")
    return {"message": "Computation completed successfully.", "status": "completed"}

# --- API Endpoints ---

@app.get("/")
def read_root():
    return {"status": "online", "project": "Rajanganaya Water Stress Monitor"}


@app.post("/cwsi/compute")
def trigger_cwsi_computation(payload: CWSIRequest):
    """Triggers the computation synchronously to prevent Cloud Run CPU throttling."""
    print(f"⚙️ Starting Earth Engine computation for field {payload.field_id}...")

    # 1. We call the worker directly and wait for it to finish
    processing_worker(payload.field_id, payload.date)

    print(f"✅ Computation finished for field {payload.field_id}")

    # 2. We only return a response AFTER the database is successfully updated
    return {
        "message": f"Computation completed successfully for field {payload.field_id}.",
        "status": "completed"
    }


@app.get("/fields")
def get_all_fields():
    """Returns all registered fields, but first deletes any older than 1 hour."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # --- 🧹 GARBAGE COLLECTION SWEEP ---
    try:
        # 1. Delete the stress observations for old fields first (to prevent foreign key errors)
        cursor.execute("""
            DELETE FROM cwsi_obs 
            WHERE field_id IN (
                SELECT id FROM fields WHERE created_at < NOW() - INTERVAL '1 hour'
            );
        """)
        # 2. Delete the old fields themselves
        cursor.execute("""
            DELETE FROM fields 
            WHERE created_at < NOW() - INTERVAL '1 hour';
        """)
        conn.commit() # Save the deletions!
    except Exception as e:
        print(f"Cleanup error: {e}")
        conn.rollback()
    # ------------------------------------

    # We use a subquery to grab the most recent CWSI value for each field
    cursor.execute("""
        SELECT f.id, f.name, f.crop_type, f.kc, ST_AsGeoJSON(f.geom) as geometry,
               (SELECT cwsi_mean FROM cwsi_obs c WHERE c.field_id = f.id ORDER BY obs_date DESC LIMIT 1) as latest_cwsi
        FROM fields f;
    """)
    rows = cursor.fetchall()

    features = []
    import json
    for row in rows:
        features.append({
            "type": "Feature",
            "properties": {
                "id": str(row["id"]),
                "name": row["name"],
                "cwsi": row["latest_cwsi"] if row["latest_cwsi"] is not None else 0
            },
            "geometry": json.loads(row["geometry"])
        })

    cursor.close()
    conn.close()

    return {"type": "FeatureCollection", "features": features}

@app.get("/cwsi/{field_id}/latest")
def get_latest_cwsi(field_id: str):
    """Fetches the most recent water stress reading for a specific field."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT obs_date, cwsi_mean, etr_mm 
        FROM cwsi_obs 
        WHERE field_id = %s 
        ORDER BY obs_date DESC LIMIT 1;
    """, (field_id,))

    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="No observations found for this field.")

    # Calculate the ET Deficit in mm/day
    cwsi = row["cwsi_mean"]
    etr = row["etr_mm"]
    kc = 1.20  # Using mid-season paddy Kc for
    etd = round(cwsi * etr * kc, 2)

    return {
        "date": row["obs_date"].isoformat(),
        "cwsi_mean": round(cwsi, 3),
        "et_deficit_mm_day": etd,
        "stress_level": "Critical" if cwsi >= 0.6 else "Moderate" if cwsi >= 0.4 else "Mild/None"
    }


@app.post("/fields/register", status_code=201)
def register_new_field(payload: FieldRegistration):
    """Saves a new user-drawn field to PostGIS and returns the generated UUID."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    import json
    geom_json = json.dumps(payload.geometry)

    # Insert the field and immediately return the new UUID
    cursor.execute("""
        INSERT INTO fields (name, crop_type, kc, geom)
        VALUES (%s, 'paddy_rice', 1.20, ST_GeomFromGeoJSON(%s))
        RETURNING id;
    """, (payload.name, geom_json))

    new_id = cursor.fetchone()["id"]
    conn.commit()
    cursor.close()
    conn.close()

    return {"message": "Field registered successfully", "field_id": str(new_id)}

@app.get("/cwsi/{field_id}/history")
def get_field_history(field_id: str, months: int = 3): # <-- Added 'months' parameter
    """Fetches the historical time-series of CWSI, bounded by user selection."""
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Use the 'months' parameter to limit how far back SQL looks
    cursor.execute(f"""
        SELECT obs_date, cwsi_mean, etr_mm, ndvi_mean, lst_mean_k 
        FROM cwsi_obs 
        WHERE field_id = %s 
        AND obs_date >= NOW() - INTERVAL '{months} months'
        ORDER BY obs_date ASC;
    """, (field_id,))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        return []

    history = []
    for row in rows:
        history.append({
            "date": row["obs_date"].isoformat(),
            "cwsi": round(row["cwsi_mean"], 3),
            "ndvi": round(row["ndvi_mean"], 3),
            "et_deficit": round(row["cwsi_mean"] * row["etr_mm"] * 1.20, 2)
        })

    return history
