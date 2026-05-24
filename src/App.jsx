import { useState, useEffect, useCallback, useRef } from 'react';
import Map, { Source, Layer, Popup } from 'react-map-gl/maplibre';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import * as turf from '@turf/turf';

const API_BASE = 'https://cwsi-backend-251521207330.asia-southeast1.run.app';

const getTodayString = () => {
  const today = new Date();
  const year = today.getFullYear();
  const month = String(today.getMonth() + 1).padStart(2, '0');
  const day = String(today.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

// Basemap Configurations
const satelliteStyle = {
  version: 8,
  sources: {
    'esri-satellite': {
      type: 'raster',
      tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
      tileSize: 256
    }
  },
  layers: [{ id: 'satellite-layer', type: 'raster', source: 'esri-satellite', minzoom: 0, maxzoom: 22 }]
};

const osmStyle = {
  version: 8,
  sources: {
    'osm': {
      type: 'raster',
      tiles: ['https://a.tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '&copy; OpenStreetMap Contributors'
    }
  },
  layers: [{ id: 'osm-layer', type: 'raster', source: 'osm', minzoom: 0, maxzoom: 19 }]
};

const maplibreDrawTheme = [
  { id: 'gl-draw-polygon-fill-inactive', type: 'fill', filter: ['all', ['==', 'active', 'false'], ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']], paint: { 'fill-color': '#3bb2d0', 'fill-outline-color': '#3bb2d0', 'fill-opacity': 0.1 } },
  { id: 'gl-draw-polygon-fill-active', type: 'fill', filter: ['all', ['==', 'active', 'true'], ['==', '$type', 'Polygon']], paint: { 'fill-color': '#fbb03b', 'fill-outline-color': '#fbb03b', 'fill-opacity': 0.1 } },
  { id: 'gl-draw-polygon-stroke-inactive', type: 'line', filter: ['all', ['==', 'active', 'false'], ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']], paint: { 'line-color': '#3bb2d0', 'line-width': 2 } },
  { id: 'gl-draw-polygon-stroke-active', type: 'line', filter: ['all', ['==', 'active', 'true'], ['==', '$type', 'Polygon']], paint: { 'line-color': '#fbb03b', 'line-dasharray': ['literal', [0.2, 2]], 'line-width': 2 } },
  { id: 'gl-draw-polygon-and-line-vertex-stroke-inactive', type: 'circle', filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point'], ['!=', 'mode', 'static']], paint: { 'circle-radius': 5, 'circle-color': '#fff' } },
  { id: 'gl-draw-polygon-and-line-vertex-inactive', type: 'circle', filter: ['all', ['==', 'meta', 'vertex'], ['==', '$type', 'Point'], ['!=', 'mode', 'static']], paint: { 'circle-radius': 3, 'circle-color': '#fbb03b' } }
];

export default function App() {
  const [fields, setFields] = useState(null);
  const [hoverInfo, setHoverInfo] = useState(null);

  // Core States
  const [selectedDate, setSelectedDate] = useState(getTodayString());
  const [isProcessing, setIsProcessing] = useState(false);
  const [latestResult, setLatestResult] = useState(null);

  // New UI States
  const [activeFieldId, setActiveFieldId] = useState(null);
  const [basemap, setBasemap] = useState('satellite');
  const [enableHistory, setEnableHistory] = useState(false);
  const [historyMonths, setHistoryMonths] = useState(3);
  const [historicalData, setHistoricalData] = useState([]);

  const drawControlRef = useRef(null);

  const loadFields = useCallback(() => {
    fetch(`${API_BASE}/fields`)
      .then(res => res.json())
      .then(data => setFields(data))
      .catch(err => console.error("Failed to load fields:", err));
  }, []);

  useEffect(() => { loadFields(); }, [loadFields]);

  // Auto-fetch history when toggles or the active field changes
  useEffect(() => {
    if (enableHistory && activeFieldId) {
      fetch(`${API_BASE}/cwsi/${activeFieldId}/history?months=${historyMonths}`)
        .then(res => res.json())
        .then(data => setHistoricalData(data))
        .catch(err => console.error("History fetch failed:", err));
    } else {
      setHistoricalData([]); // Clear chart if toggled off
    }
  }, [enableHistory, historyMonths, activeFieldId]);

  const onMapLoad = (e) => {
    if (drawControlRef.current) return;
    const map = e.target;
    const draw = new MapboxDraw({ displayControlsDefault: false, styles: maplibreDrawTheme });
    map.addControl(draw);
    drawControlRef.current = draw;

    map.on('draw.create', async (event) => {
      const feature = event.features[0];
      const geometry = feature.geometry;

      // --- 🚨 THE EMERGENCY FAIL SAFE 🚨 ---
      // Calculate area in square meters, then convert to Hectares
      const areaSquareMeters = turf.area(feature);
      const areaHectares = areaSquareMeters / 10000;

      // Set your maximum limit (e.g., 500 hectares)
      const MAX_HECTARES = 500;

      if (areaHectares > MAX_HECTARES) {
        alert(`🚨 Whoa there! That polygon is massive (${Math.round(areaHectares)} hectares).\n\nPlease keep individual paddy fields under ${MAX_HECTARES} hectares to prevent server overloads.`);
        draw.deleteAll(); // Instantly wipe the massive shape off the map
        return; // Stop the code completely!
      }
      // ------------------------------------

      const fieldName = prompt("Enter a name for this new Paddy Field:");

      if (!fieldName) { draw.deleteAll(); return; }

      setIsProcessing(true);
      setLatestResult(null);
      setHistoricalData([]);

      try {
        const regRes = await fetch(`${API_BASE}/fields/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: fieldName, geometry })
        });
        const regData = await regRes.json();

        const computeRes = await fetch(`${API_BASE}/cwsi/compute`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ field_id: regData.field_id, date: selectedDate })
        });

        if (!computeRes.ok) {
          const errorData = await computeRes.json();
          alert(`⚠️ Analysis Failed:\n\n${errorData.detail}`);
          draw.deleteAll();
          return;
        }

        const resultRes = await fetch(`${API_BASE}/cwsi/${regData.field_id}/latest`);
        if (!resultRes.ok) {
          alert("Earth Engine finished, but failed to generate data.");
          draw.deleteAll();
          loadFields();
          return;
        }
        const resultData = await resultRes.json();

        // 🎯 HISTORY LOGIC: Only fetch if toggled on!
        setActiveFieldId(regData.field_id);

        setLatestResult({ name: fieldName, ...resultData });
        draw.deleteAll();
        loadFields();

      } catch (error) {
        console.error("Pipeline Error:", error);
        alert("Something went wrong processing the field.");
      } finally {
        setIsProcessing(false);
      }
    });
  };

  const handleStartDrawing = () => drawControlRef.current?.changeMode('draw_polygon');
  const handleClearDrawing = () => drawControlRef.current?.deleteAll();

  const polygonLayerStyle = {
    id: 'paddy-fields',
    type: 'fill',
    paint: {
      'fill-color': ['interpolate', ['linear'], ['get', 'cwsi'], 0.0, '#10b981', 0.4, '#fbbf24', 0.7, '#ef4444'],
      'fill-opacity': 0.7,
      'fill-outline-color': '#ffffff'
    }
  };

  return (
    <div className="dashboard-container">

      {/* LEFT SIDE: MAP & CHART */}
      {/* 👇 Applied the new mobile-friendly map-section class! */}
      <div className="map-section">

        {/* Map Container */}
        {/* 👇 Added minHeight: '400px' so the map doesn't disappear on phones */}
        <div style={{ flex: enableHistory && historicalData.length > 0 ? '0 0 55%' : '1', position: 'relative', minHeight: '400px' }}>

          {/* Basemap Toggle Buttons */}
          <div style={{ position: 'absolute', top: 15, left: 15, zIndex: 10, display: 'flex', gap: '5px', background: 'rgba(15, 23, 42, 0.8)', padding: '5px', borderRadius: '8px', border: '1px solid #334155' }}>
            <button
              onClick={() => setBasemap('satellite')}
              style={{ padding: '8px 12px', cursor: 'pointer', background: basemap === 'satellite' ? '#2563eb' : 'transparent', color: 'white', border: 'none', borderRadius: '4px', fontWeight: 'bold' }}>
              Satellite
            </button>
            <button
              onClick={() => setBasemap('osm')}
              style={{ padding: '8px 12px', cursor: 'pointer', background: basemap === 'osm' ? '#2563eb' : 'transparent', color: 'white', border: 'none', borderRadius: '4px', fontWeight: 'bold' }}>
              OSM
            </button>
          </div>

          <Map
            initialViewState={{ longitude: 80.7718, latitude: 7.8731, zoom: 6.5 }}
            mapStyle={basemap === 'satellite' ? satelliteStyle : osmStyle}
            mapLib={maplibregl}
            interactiveLayerIds={['paddy-fields']}
            onLoad={onMapLoad}
            onClick={(e) => {
              if (e.features.length > 0) {
                const f = e.features[0];
                setHoverInfo({ lngLat: e.lngLat, name: f.properties.name, cwsi: f.properties.cwsi });
                setActiveFieldId(f.properties.id);
              } else {
                setHoverInfo(null);
                setActiveFieldId(null);
              }
            }}
            onMouseEnter={(e) => e.target.getCanvas().style.cursor = 'pointer'}
            onMouseLeave={(e) => e.target.getCanvas().style.cursor = ''}
          >
            {fields && (
              <Source id="fields-data" type="geojson" data={fields}>
                <Layer {...polygonLayerStyle} />
              </Source>
            )}

            {hoverInfo && (
              <Popup longitude={hoverInfo.lngLat.lng} latitude={hoverInfo.lngLat.lat} closeOnClick={false} onClose={() => setHoverInfo(null)}>
                <div style={{ fontFamily: 'sans-serif', padding: '5px'}}>
                  <h3 style={{ margin: '0 0 5px 0' }}>{hoverInfo.name}</h3>
                  <p style={{ margin: '0', fontWeight: 'bold', color: hoverInfo.cwsi > 0.6 ? '#dc2626' : hoverInfo.cwsi > 0.4 ? '#d97706' : '#16a34a' }}>
                    {hoverInfo.cwsi > 0.6 ? '🚨 CRITICAL' : hoverInfo.cwsi > 0.4 ? '🟡 MODERATE' : '✅ HEALTHY'}
                  </p>
                </div>
              </Popup>
            )}
          </Map>
        </div>

        {/* Dynamic Chart Container (Renders under the map) */}
        {enableHistory && historicalData.length > 0 && !isProcessing && (
          <div style={{ flex: '0 0 45%', padding: '20px', background: '#0f172a' }}>
            <h3 style={{ margin: '0 0 10px 0', color: '#e2e8f0' }}>📈 {historyMonths}-Month Stress Trend</h3>
            <ResponsiveContainer width="100%" height="90%">
              <LineChart data={historicalData} margin={{ top: 10, right: 30, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
                <XAxis dataKey="date" stroke="#94a3b8" tickFormatter={(tick) => tick.substring(5, 10)} tick={{ fontSize: 12 }} />
                <YAxis stroke="#94a3b8" domain={[0, 1]} tick={{ fontSize: 12 }} />
                <Tooltip contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', borderRadius: '6px', color: '#f8fafc' }} itemStyle={{ color: '#38bdf8' }} />
                <ReferenceLine y={0.6} stroke="#ef4444" strokeDasharray="3 3" label={{ position: 'top', value: 'Critical', fill: '#ef4444', fontSize: 10 }} />
                <ReferenceLine y={0.4} stroke="#f59e0b" strokeDasharray="3 3" label={{ position: 'top', value: 'Moderate', fill: '#f59e0b', fontSize: 10 }} />
                <Line type="monotone" dataKey="cwsi" stroke="#38bdf8" strokeWidth={3} dot={{ r: 4, fill: '#38bdf8', strokeWidth: 2, stroke: '#0f172a' }} activeDot={{ r: 6 }} animationDuration={1500} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* RIGHT SIDE: UI PANEL */}
      <div className="ui-section">
        <h1>🌾 Crop Water Stress Monitor</h1>

        <div className="control-card">
          <label>1. Select Analysis Parameters</label>
          <input type="date" className="date-picker" value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)} />

          {/* History Controls */}
          <div style={{ marginTop: '15px', padding: '10px', background: '#0f172a', borderRadius: '6px', border: '1px solid #334155' }}>
            <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', margin: 0 }}>
              <input
                type="checkbox"
                checked={enableHistory}
                onChange={(e) => setEnableHistory(e.target.checked)}
                style={{ width: '18px', height: '18px', marginRight: '10px' }}
              />
              Enable Historical Time-Series
            </label>

            {enableHistory && (
              <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column' }}>
                <span className="hint" style={{ marginBottom: '5px' }}>Select history range:</span>
                <select
                  className="date-picker"
                  value={historyMonths}
                  onChange={(e) => setHistoryMonths(Number(e.target.value))}
                >
                  {[3, 4, 5, 6, 7].map(m => (
                    <option key={m} value={m}>{m} Months Back</option>
                  ))}
                </select>
              </div>
            )}
          </div>
        </div>

        <div className="control-card">
          <label>2. Digitize Paddy Field</label>
          <p className="hint">Click corners on the map, double-click to finish.</p>
          <div className="button-group">
            <button className="btn-primary" onClick={handleStartDrawing}>✏️ Draw Polygon</button>
            <button className="btn-secondary" onClick={handleClearDrawing}>🗑️ Clear</button>
          </div>
        </div>

        {isProcessing && (
          <div className="loading-box">
            ⏳ Analyzing thermal satellites via Earth Engine...
          </div>
        )}

        {latestResult && !isProcessing && (
          <div className="result-card">
            <h2 style={{ margin: '0 0 10px 0', color: '#064e3b' }}>✅ Analysis Complete!</h2>
            <p><strong>Field:</strong> {latestResult.name}</p>
            <p><strong>Date:</strong> {latestResult.date.split('T')[0]}</p>
            <p><strong>Stress (CWSI):</strong> {latestResult.cwsi_mean}</p>
            <p><strong>Status:</strong> <span style={{ color: latestResult.cwsi_mean > 0.6 ? 'red' : 'green', fontWeight: 'bold'}}>{latestResult.stress_level}</span></p>
          </div>
        )}
      </div>
    </div>
  );
}