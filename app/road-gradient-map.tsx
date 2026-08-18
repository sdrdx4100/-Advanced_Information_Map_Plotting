"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AttributionControl, Map as MapLibreMap, NavigationControl, Popup, type GeoJSONSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

type RouteName = "東名" | "新東名";
type Direction = "上り" | "下り";
type Quality = "measured" | "estimated";
type Structure = "normal" | "bridge" | "tunnel";
type WindowSize = 50 | 100 | 250;

type RoadSegment = {
  id: string; route: RouteName; direction: Direction; from: string | null; to: string | null;
  grade: number; elevation_start: number; elevation_end: number; quality: Quality;
  structure: Structure; chain_rank: number; anomaly: boolean;
  cum_dist_start: number; cum_dist_end: number;
  coordinates: [number, number][];
};

type Facility = { name: string; kind: string; coords: [number, number] };

type RouteProfile = {
  route: RouteName; direction: Direction; length_km: number;
  elevation_m: number[]; grade_pct: number[];
  max_elevation_m: number; min_elevation_m: number;
  total_ascent_m: number; max_abs_grade_pct: number;
};

const STRUCTURE_LABEL: Record<Structure, string> = { normal: "通常区間", bridge: "橋梁", tunnel: "トンネル" };

function gradeColor(grade: number) {
  const abs = Math.abs(grade);
  if (abs >= 4) return "#e34444";
  if (abs >= 3) return "#f2763d";
  if (abs >= 2) return "#f0b53c";
  if (abs >= 1) return "#88be5c";
  return "#2aa7a1";
}
function segmentFeatureCollection(items: RoadSegment[]) {
  return {
    type: "FeatureCollection" as const,
    features: items.map((item) => ({
      type: "Feature" as const,
      properties: { id: item.id, grade: item.grade },
      geometry: { type: "LineString" as const, coordinates: item.coordinates },
    })),
  };
}
function facilityFeatureCollection(items: Facility[]) {
  return {
    type: "FeatureCollection" as const,
    features: items.map((f) => ({
      type: "Feature" as const,
      properties: { name: f.name, kind: f.kind },
      geometry: { type: "Point" as const, coordinates: f.coords },
    })),
  };
}
function downsample(arr: number[], maxPoints: number) {
  if (arr.length <= maxPoints) return arr;
  const step = arr.length / maxPoints;
  return Array.from({ length: maxPoints }, (_, i) => arr[Math.floor(i * step)]);
}
function RouteIcon({ route }: { route: RouteName }) {
  return <span className={`route-mark ${route === "東名" ? "tomei" : "shintomei"}`}>{route === "東名" ? "E1" : "E1A"}</span>;
}

async function loadGeoJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  return (await res.json()) as T;
}

export function RoadGradientMap() {
  const mapNode = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const segmentsRef = useRef<RoadSegment[]>([]);

  const [routeFilter, setRouteFilter] = useState<"両方" | RouteName>("両方");
  const [direction, setDirection] = useState<"両方" | Direction>("両方");
  const [threshold, setThreshold] = useState(0);
  const [windowSize, setWindowSize] = useState<WindowSize>(100);
  const [segments, setSegments] = useState<RoadSegment[]>([]);
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [profiles, setProfiles] = useState<Record<string, RouteProfile>>({});
  const [loading, setLoading] = useState(true);
  const [mapReady, setMapReady] = useState(false);
  const [selected, setSelected] = useState<RoadSegment | null>(null);
  const [profileMode, setProfileMode] = useState<"elevation" | "grade">("elevation");
  const [panelOpen, setPanelOpen] = useState(true);

  const filtered = useMemo(
    () =>
      segments.filter(
        (item) =>
          (routeFilter === "両方" || item.route === routeFilter) &&
          (direction === "両方" || item.direction === direction) &&
          Math.abs(item.grade) >= threshold,
      ),
    [segments, routeFilter, direction, threshold],
  );

  useEffect(() => {
    segmentsRef.current = segments;
  }, [segments]);

  // facilities load once; segments/profiles reload whenever the gradient window changes
  useEffect(() => {
    loadGeoJSON<{ features: { properties: { name: string; kind: string }; geometry: { coordinates: [number, number] } }[] }>(
      "/data/facilities.geojson",
    ).then((fc) => {
      setFacilities(fc.features.map((f) => ({ name: f.properties.name, kind: f.properties.kind, coords: f.geometry.coordinates })));
    });
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      loadGeoJSON<{ features: { properties: Omit<RoadSegment, "coordinates">; geometry: { coordinates: [number, number][] } }[] }>(
        `/data/road-segments-${windowSize}.geojson`,
      ),
      loadGeoJSON<Record<string, RouteProfile>>(`/data/profiles-${windowSize}.json`),
    ]).then(([segFc, profileData]) => {
      const loaded = segFc.features.map((f) => ({ ...f.properties, coordinates: f.geometry.coordinates }));
      setSegments(loaded);
      setProfiles(profileData);
      setSelected((prev) => {
        if (prev) {
          const near = loaded
            .filter((s) => s.route === prev.route && s.direction === prev.direction)
            .sort((a, b) => Math.abs(a.cum_dist_start - prev.cum_dist_start) - Math.abs(b.cum_dist_start - prev.cum_dist_start))[0];
          if (near) return near;
        }
        return loaded.find((s) => s.route === "新東名" && s.direction === "上り" && s.quality === "measured" && !s.anomaly) ?? loaded[0] ?? null;
      });
      setLoading(false);
    });
  }, [windowSize]);

  useEffect(() => {
    if (!mapNode.current || mapRef.current) return;
    const map = new MapLibreMap({ container: mapNode.current, style: "https://tiles.openfreemap.org/styles/positron", center: [138.27, 35.07], zoom: 8.35, attributionControl: false });
    map.addControl(new NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => {
      map.addSource("roads", { type: "geojson", data: segmentFeatureCollection([]) });
      map.addLayer({ id: "road-casing", type: "line", source: "roads", paint: { "line-color": "#fff", "line-width": 7, "line-opacity": 0.92 } });
      map.addLayer({
        id: "roads", type: "line", source: "roads",
        paint: {
          "line-color": ["case", [">=", ["abs", ["get", "grade"]], 4], "#e34444", [">=", ["abs", ["get", "grade"]], 3], "#f2763d", [">=", ["abs", ["get", "grade"]], 2], "#f0b53c", [">=", ["abs", ["get", "grade"]], 1], "#88be5c", "#2aa7a1"],
          "line-width": 4,
        },
      });
      map.addSource("facilities", { type: "geojson", data: facilityFeatureCollection([]) });
      map.addLayer({ id: "facilities", type: "circle", source: "facilities", paint: { "circle-radius": 4.5, "circle-color": "#14231e", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      map.addLayer({ id: "facility-labels", type: "symbol", source: "facilities", layout: { "text-field": ["get", "name"], "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top" }, paint: { "text-color": "#263831", "text-halo-color": "#fff", "text-halo-width": 1.5 } });
      map.on("mouseenter", "roads", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "roads", () => { map.getCanvas().style.cursor = ""; });
      map.on("click", "roads", (event) => {
        const id = event.features?.[0]?.properties?.id as string | undefined;
        const item = segmentsRef.current.find((segment) => segment.id === id);
        if (item) {
          setSelected(item);
          setPanelOpen(true);
          new Popup({ closeButton: false, offset: 10 })
            .setLngLat(event.lngLat)
            .setHTML(`<strong>${item.route}高速道路</strong><br><span>${item.direction} ${item.grade > 0 ? "+" : ""}${item.grade.toFixed(1)}%</span>`)
            .addTo(map);
        }
      });
      setMapReady(true);
    });
    mapRef.current = map;

    // The container's final size isn't settled yet when MapLibre reads it at
    // construction time (CSS grid rows + async content below still resolving),
    // so the canvas can end up locked to a stale/default size. Force a resize
    // once layout has actually stabilized.
    const ro = new ResizeObserver(() => map.resize());
    if (mapNode.current) ro.observe(mapNode.current);
    requestAnimationFrame(() => map.resize());

    return () => { ro.disconnect(); map.remove(); mapRef.current = null; };
  }, []);

  // mapReady is a dependency, not just a guard: the map's "load" event and the
  // segments/facilities fetches race, so without it a fetch that resolves
  // before the map finishes loading would set data on a source that doesn't
  // exist yet and never get retried once it does.
  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getSource("roads")) return;
    (map.getSource("roads") as GeoJSONSource).setData(segmentFeatureCollection(filtered));
  }, [filtered, mapReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map?.getSource("facilities")) return;
    (map.getSource("facilities") as GeoJSONSource).setData(facilityFeatureCollection(facilities));
  }, [facilities, mapReady]);

  const profile = selected ? profiles[`${selected.route}_${selected.direction}`] : undefined;
  const routeProfile = profile ? downsample(profile.elevation_m, 400) : [];
  const gradeProfile = profile ? downsample(profile.grade_pct, 300) : [];
  const estimatedShare = useMemo(() => {
    if (!selected) return 0;
    const inDir = segments.filter((s) => s.route === selected.route && s.direction === selected.direction);
    if (inDir.length === 0) return 0;
    return Math.round((inDir.filter((s) => s.quality === "estimated").length / inDir.length) * 100);
  }, [segments, selected]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-icon" aria-hidden="true"><span /></div><div><h1>ROAD SLOPE</h1><p>東名・新東名 道路勾配マップ</p></div></div>
        <div className="status-pill"><span className="status-dot" /> 静岡区間 <b>PROTOTYPE</b></div>
        <button className="icon-button" aria-label="情報">i</button>
      </header>
      <section className="workspace">
        <aside className={`sidebar ${panelOpen ? "open" : ""}`}>
          <div className="sidebar-head"><span>表示条件</span><button onClick={() => setPanelOpen(false)} aria-label="パネルを閉じる">×</button></div>
          <div className="control-group"><label>路線</label><div className="segmented three">{(["両方", "東名", "新東名"] as const).map((item) => <button key={item} className={routeFilter === item ? "active" : ""} onClick={() => setRouteFilter(item)}>{item}</button>)}</div></div>
          <div className="route-summary">
            <div><RouteIcon route="東名" /><span><b>東名高速道路</b><small>OSM連結区間 約{profiles["東名_上り"]?.length_km ?? "—"}km（全区間の一部）</small></span></div>
            <div><RouteIcon route="新東名" /><span><b>新東名高速道路</b><small>OSM連結区間 約{profiles["新東名_上り"]?.length_km ?? "—"}km</small></span></div>
          </div>
          <div className="control-group"><label>方向</label><div className="segmented three">{(["両方", "上り", "下り"] as const).map((item) => <button key={item} className={direction === item ? "active" : ""} onClick={() => setDirection(item)}>{item === "上り" ? "上り ↗" : item === "下り" ? "下り ↙" : item}</button>)}</div></div>
          <div className="control-group range-control"><label><span>勾配しきい値</span><strong>{threshold === 0 ? "すべて表示" : `${threshold}% 以上`}</strong></label><input aria-label="勾配しきい値" type="range" min="0" max="4" step="1" value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /><div className="ticks"><span>0%</span><span>1</span><span>2</span><span>3</span><span>4%+</span></div></div>
          <div className="control-group"><label>計算区間</label><div className="segmented three">{([50, 100, 250] as const).map((size) => <button key={size} className={windowSize === size ? "active" : ""} onClick={() => setWindowSize(size)}>{size}m</button>)}</div><p className="hint">25m間隔の標高点から移動平均との差を算出</p></div>
          <div className="legend-block"><label>勾配</label>{[["0 — 1%", "緩い", "#2aa7a1"], ["1 — 2%", "やや勾配", "#88be5c"], ["2 — 3%", "勾配あり", "#f0b53c"], ["3 — 4%", "急", "#f2763d"], ["4%以上", "非常に急", "#e34444"]].map(([range, name, color]) => <div className="legend-row" key={range}><span className="legend-line" style={{ background: color }} /><b>{range}</b><small>{name}</small></div>)}</div>
          <div className="data-note"><span>◆</span><p><b>データソースについて</b><br />OpenStreetMapの道路線形 + 国土地理院DEMから算出。道路中心線自体の高さデータは未統合（試作段階）。トンネル・橋梁とその前後は前後端点から補間した推定値です。</p></div>
        </aside>
        <div className="map-area">
          <div ref={mapNode} className="map" aria-label="東名・新東名の道路勾配地図" />
          {!panelOpen && <button className="open-panel" onClick={() => setPanelOpen(true)}>☰ 表示条件</button>}
          <div className="map-caption"><span className="pulse" /> {loading ? "読み込み中…" : `${filtered.length} 区間を表示中`} <em>•</em> 道路をクリックして詳細</div>
          {selected && (
            <article className="segment-card">
              <button className="card-close" aria-label="詳細を閉じる" onClick={() => setSelected(null)}>×</button>
              <div className="segment-title"><RouteIcon route={selected.route} /><div><small>{selected.route}高速道路・{selected.direction}</small><h2>{selected.from ?? "—"} <span>→</span> {selected.to ?? "—"}</h2></div></div>
              <div className="metric-grid">
                <div><small>平均勾配</small><strong style={{ color: gradeColor(selected.grade) }}>{selected.grade > 0 ? "+" : ""}{selected.grade.toFixed(1)}<span>%</span></strong><em>{selected.grade >= 0 ? "上り勾配" : "下り勾配"}</em></div>
                <div><small>道路標高</small><strong>{selected.elevation_start.toFixed(0)}<span>m</span> <i>→</i> {selected.elevation_end.toFixed(0)}<span>m</span></strong><em>{windowSize}m 区間</em></div>
              </div>
              <div className="quality-row">
                <span className={selected.quality === "measured" && !selected.anomaly ? "verified" : "estimated"}>● {selected.quality === "measured" && !selected.anomaly ? "DEM実測" : "推定値"}</span>
                <small>{selected.anomaly ? "異常値として要検証" : "OSM + 国土地理院DEM"}</small>
                <b>{STRUCTURE_LABEL[selected.structure]}</b>
              </div>
            </article>
          )}
        </div>
      </section>
      <section className="profile-panel">
        <div className="profile-head">
          <div><RouteIcon route={selected?.route ?? "東名"} /><span><b>{selected?.route ?? "—"}高速道路・{selected?.direction ?? ""}</b><small>{profile ? `OSM連結区間 約${profile.length_km}km` : "区間を選択してください"}</small></span></div>
          <div className="segmented profile-toggle"><button className={profileMode === "elevation" ? "active" : ""} onClick={() => setProfileMode("elevation")}>標高プロファイル</button><button className={profileMode === "grade" ? "active" : ""} onClick={() => setProfileMode("grade")}>勾配グラフ</button></div>
        </div>
        {routeProfile.length > 1 ? (
          <>
            <ProfileChart mode={profileMode} elevation={routeProfile} grades={gradeProfile} lengthKm={profile!.length_km} />
            <div className="profile-stats">
              <span><small>最高標高</small><b>{profile!.max_elevation_m} m</b></span>
              <span><small>累積上昇</small><b>{profile!.total_ascent_m.toLocaleString()} m</b></span>
              <span><small>最大勾配</small><b>{profile!.max_abs_grade_pct}%</b></span>
              <span><small>推定区間</small><b>{estimatedShare}%</b></span>
            </div>
          </>
        ) : (
          <div className="chart-wrap" style={{ display: "grid", placeItems: "center", color: "#8a9891", fontSize: 11 }}>データを読み込み中…</div>
        )}
      </section>
      <footer><span>試作データ — OSM + 国土地理院DEM、道路中心線標高は未統合</span><span>道路縦断勾配 / 25mサンプリング / {windowSize}m移動平均</span></footer>
    </main>
  );
}

function ProfileChart({ mode, elevation, grades, lengthKm }: { mode: "elevation" | "grade"; elevation: number[]; grades: number[]; lengthKm: number }) {
  const width = 1000, height = 118;
  if (mode === "grade") {
    return (
      <div className="chart-wrap">
        <div className="axis-labels"><span>+4%</span><span>0%</span><span>−4%</span></div>
        <svg className="chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label="勾配グラフ">
          <line x1="0" y1="59" x2={width} y2="59" className="zero-line" />
          {grades.map((g, i) => {
            const barW = width / grades.length - 1, barH = Math.min(Math.abs(g), 4.5) / 4.5 * 48;
            return <rect key={i} x={(i * width) / grades.length} y={g > 0 ? 59 - barH : 59} width={Math.max(barW, 0.6)} height={barH} rx="1" fill={gradeColor(g)} opacity=".9" />;
          })}
        </svg>
        <DistanceLabels lengthKm={lengthKm} />
      </div>
    );
  }
  const min = Math.min(...elevation) - 20, max = Math.max(...elevation) + 15;
  const pts = elevation.map((v, i) => `${(i / (elevation.length - 1)) * width},${height - ((v - min) / (max - min)) * (height - 16)}`).join(" ");
  return (
    <div className="chart-wrap">
      <div className="axis-labels"><span>{Math.round(max)}m</span><span>{Math.round((max + min) / 2)}m</span><span>{Math.round(min)}m</span></div>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label="標高プロファイル">
        <defs><linearGradient id="profileFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#43a489" stopOpacity=".3" /><stop offset="100%" stopColor="#43a489" stopOpacity=".02" /></linearGradient></defs>
        <polygon points={`0,${height} ${pts} ${width},${height}`} fill="url(#profileFill)" />
        <polyline points={pts} fill="none" stroke="#278c76" strokeWidth="3" vectorEffect="non-scaling-stroke" />
      </svg>
      <DistanceLabels lengthKm={lengthKm} />
    </div>
  );
}
function DistanceLabels({ lengthKm }: { lengthKm: number }) {
  const steps = 6;
  return (
    <div className="distance-labels">
      {Array.from({ length: steps + 1 }, (_, i) => {
        const km = Math.round((lengthKm * i) / steps);
        return <span key={i}>{i === steps ? `${km} km` : km}</span>;
      })}
    </div>
  );
}
