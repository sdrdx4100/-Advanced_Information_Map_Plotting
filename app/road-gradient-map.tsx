"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AttributionControl, Map as MapLibreMap, NavigationControl, Popup, type GeoJSONSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

type RouteName = "東名" | "新東名";
type Direction = "上り" | "下り";
type Quality = "3D道路" | "DEM推定";
type RoadSegment = {
  id: string; route: RouteName; direction: Direction; from: string; to: string;
  grade: number; elevationStart: number; elevationEnd: number; quality: Quality;
  structure: "normal" | "bridge" | "tunnel";
  coordinates: [[number, number], [number, number]];
};

const routePoints: Record<RouteName, [number, number][]> = {
  東名: [[138.943,35.286],[138.893,35.252],[138.813,35.226],[138.72,35.196],[138.623,35.161],[138.51,35.151],[138.39,35.133],[138.273,35.091],[138.154,35.054],[138.035,35.019],[137.914,34.968],[137.79,34.914],[137.676,34.825],[137.593,34.769]],
  新東名: [[138.939,35.286],[138.862,35.296],[138.772,35.27],[138.671,35.241],[138.568,35.224],[138.457,35.201],[138.348,35.178],[138.239,35.151],[138.132,35.123],[138.02,35.087],[137.91,35.04],[137.804,34.989],[137.704,34.925],[137.648,34.895]],
};

const nodeNames: Record<RouteName, string[]> = {
  東名: ["御殿場JCT","裾野IC","沼津IC","富士IC","清水JCT","静岡IC","焼津IC","吉田IC","相良牧之原IC","菊川IC","掛川IC","袋井IC","浜松IC","浜松西IC"],
  新東名: ["御殿場JCT","新御殿場IC","長泉沼津IC","新富士IC","新清水JCT","新静岡IC","藤枝岡部IC","島田金谷IC","森掛川IC","新磐田SIC","浜松浜北IC","浜松SA","浜松いなさJCT","三ヶ日JCT"],
};
const grades: Record<RouteName, number[]> = {
  東名: [3.8,-2.6,4.2,-3.4,2.7,1.4,-3.1,2.2,4.0,-1.8,2.9,-3.7,1.1],
  新東名: [1.8,-1.3,2.0,-1.6,1.1,1.7,-1.9,1.5,-1.2,1.8,-1.6,1.2,-1.7],
};

const segments: RoadSegment[] = (["東名", "新東名"] as RouteName[]).flatMap((route) =>
  routePoints[route].slice(0, -1).flatMap((start, index) => {
    const end = routePoints[route][index + 1];
    const grade = grades[route][index];
    const base = route === "東名" ? 42 + index * 5 : 118 + index * 3;
    const quality: Quality = index === 3 || index === 8 ? "DEM推定" : "3D道路";
    const structure = index === 4 || index === 10 ? "tunnel" : index === 6 ? "bridge" : "normal";
    return (["上り", "下り"] as Direction[]).map((direction, lane) => ({
      id: `${route}-${direction}-${index}`, route, direction,
      from: nodeNames[route][index], to: nodeNames[route][index + 1],
      grade: direction === "上り" ? grade : -grade,
      elevationStart: Math.round(base), elevationEnd: Math.round(base + grade), quality, structure,
      coordinates: [[start[0],start[1]+(lane===0?.0022:-.0022)],[end[0],end[1]+(lane===0?.0022:-.0022)]],
    }));
  }),
);

const facilities = [
  {name:"御殿場JCT",kind:"JCT",coords:[138.941,35.286]}, {name:"新富士IC",kind:"IC",coords:[138.67,35.241]},
  {name:"新清水JCT",kind:"JCT",coords:[138.568,35.224]}, {name:"新静岡IC",kind:"IC",coords:[138.457,35.201]},
  {name:"静岡SA",kind:"SA",coords:[138.35,35.178]}, {name:"藤枝岡部IC",kind:"IC",coords:[138.239,35.151]},
  {name:"浜松SA",kind:"SA",coords:[137.704,34.925]}, {name:"浜松いなさJCT",kind:"JCT",coords:[137.648,34.895]},
];

function gradeColor(grade: number) {
  const abs=Math.abs(grade); if(abs>=4)return"#e34444"; if(abs>=3)return"#f2763d"; if(abs>=2)return"#f0b53c"; if(abs>=1)return"#88be5c"; return"#2aa7a1";
}
function featureCollection(items: RoadSegment[]) {
  return {type:"FeatureCollection" as const,features:items.map((item)=>({type:"Feature" as const,properties:{...item,coordinates:undefined},geometry:{type:"LineString" as const,coordinates:item.coordinates}}))};
}
function RouteIcon({route}:{route:RouteName}) { return <span className={`route-mark ${route==="東名"?"tomei":"shintomei"}`}>{route==="東名"?"E1":"E1A"}</span>; }

export function RoadGradientMap() {
  const mapNode=useRef<HTMLDivElement>(null); const mapRef=useRef<MapLibreMap|null>(null);
  const [routeFilter,setRouteFilter]=useState<"両方"|RouteName>("両方"); const [direction,setDirection]=useState<"両方"|Direction>("両方");
  const [threshold,setThreshold]=useState(0); const [windowSize,setWindowSize]=useState(100);
  const [selected,setSelected]=useState<RoadSegment>(segments.find((s)=>s.id==="新東名-上り-5")!);
  const [profileMode,setProfileMode]=useState<"elevation"|"grade">("elevation"); const [panelOpen,setPanelOpen]=useState(true);
  const filtered=useMemo(()=>segments.filter((item)=>(routeFilter==="両方"||item.route===routeFilter)&&(direction==="両方"||item.direction===direction)&&Math.abs(item.grade)>=threshold),[routeFilter,direction,threshold]);

  useEffect(()=>{
    if(!mapNode.current||mapRef.current)return;
    const map=new MapLibreMap({container:mapNode.current,style:"https://tiles.openfreemap.org/styles/positron",center:[138.27,35.07],zoom:8.35,attributionControl:false});
    map.addControl(new NavigationControl({showCompass:false}),"bottom-right"); map.addControl(new AttributionControl({compact:true}),"bottom-right");
    map.on("load",()=>{
      map.addSource("roads",{type:"geojson",data:featureCollection(segments)});
      map.addLayer({id:"road-casing",type:"line",source:"roads",paint:{"line-color":"#fff","line-width":7,"line-opacity":.92}});
      map.addLayer({id:"roads",type:"line",source:"roads",paint:{"line-color":["case",[">=",["abs",["get","grade"]],4],"#e34444",[">=",["abs",["get","grade"]],3],"#f2763d",[">=",["abs",["get","grade"]],2],"#f0b53c",[">=",["abs",["get","grade"]],1],"#88be5c","#2aa7a1"],"line-width":4}});
      map.addSource("facilities",{type:"geojson",data:{type:"FeatureCollection",features:facilities.map((f)=>({type:"Feature",properties:f,geometry:{type:"Point",coordinates:f.coords}}))} as GeoJSON.FeatureCollection});
      map.addLayer({id:"facilities",type:"circle",source:"facilities",paint:{"circle-radius":4.5,"circle-color":"#14231e","circle-stroke-color":"#fff","circle-stroke-width":2}});
      map.addLayer({id:"facility-labels",type:"symbol",source:"facilities",layout:{"text-field":["get","name"],"text-size":11,"text-offset":[0,1.1],"text-anchor":"top"},paint:{"text-color":"#263831","text-halo-color":"#fff","text-halo-width":1.5}});
      map.on("mouseenter","roads",()=>{map.getCanvas().style.cursor="pointer"}); map.on("mouseleave","roads",()=>{map.getCanvas().style.cursor=""});
      map.on("click","roads",(event)=>{const id=event.features?.[0]?.properties?.id as string|undefined; const item=segments.find((segment)=>segment.id===id); if(item){setSelected(item);setPanelOpen(true);new Popup({closeButton:false,offset:10}).setLngLat(event.lngLat).setHTML(`<strong>${item.route}高速道路</strong><br><span>${item.direction} ${item.grade>0?"+":""}${item.grade.toFixed(1)}%</span>`).addTo(map);}});
    }); mapRef.current=map; return()=>{map.remove();mapRef.current=null};
  },[]);
  useEffect(()=>{const map=mapRef.current;if(!map?.getSource("roads"))return;(map.getSource("roads") as GeoJSONSource).setData(featureCollection(filtered));},[filtered]);

  const routeProfile=selected.route==="新東名"?[122,138,155,184,196,178,162,151,143,128,115,103,91,105,119,108]:[48,72,126,91,155,112,78,119,65,103,144,82,55,91,62,46];
  const gradeProfile=selected.route==="新東名"?[.8,1.7,1.9,.9,-1.6,-1.9,-1.2,-.4,-1.5,-1.8,-1.3,1.2,1.7,-.8,-1.1]:[1.6,3.8,-2.4,4.1,-3.3,-2,3.1,-4,2.8,3.7,-3.6,-2.2,2.9,-3.1,-1.3];
  return <main className="app-shell">
    <header className="topbar"><div className="brand"><div className="brand-icon" aria-hidden="true"><span/></div><div><h1>ROAD SLOPE</h1><p>東名・新東名 道路勾配マップ</p></div></div><div className="status-pill"><span className="status-dot"/> 静岡区間 <b>PROTOTYPE</b></div><button className="icon-button" aria-label="情報">i</button></header>
    <section className="workspace">
      <aside className={`sidebar ${panelOpen?"open":""}`}><div className="sidebar-head"><span>表示条件</span><button onClick={()=>setPanelOpen(false)} aria-label="パネルを閉じる">×</button></div>
        <div className="control-group"><label>路線</label><div className="segmented three">{(["両方","東名","新東名"] as const).map((item)=><button key={item} className={routeFilter===item?"active":""} onClick={()=>setRouteFilter(item)}>{item}</button>)}</div></div>
        <div className="route-summary"><div><RouteIcon route="東名"/><span><b>東名高速道路</b><small>御殿場JCT — 浜松西IC</small></span></div><div><RouteIcon route="新東名"/><span><b>新東名高速道路</b><small>御殿場JCT — 浜松いなさJCT</small></span></div></div>
        <div className="control-group"><label>方向</label><div className="segmented three">{(["両方","上り","下り"] as const).map((item)=><button key={item} className={direction===item?"active":""} onClick={()=>setDirection(item)}>{item==="上り"?"上り ↗":item==="下り"?"下り ↙":item}</button>)}</div></div>
        <div className="control-group range-control"><label><span>勾配しきい値</span><strong>{threshold===0?"すべて表示":`${threshold}% 以上`}</strong></label><input aria-label="勾配しきい値" type="range" min="0" max="4" step="1" value={threshold} onChange={(e)=>setThreshold(Number(e.target.value))}/><div className="ticks"><span>0%</span><span>1</span><span>2</span><span>3</span><span>4%+</span></div></div>
        <div className="control-group"><label>計算区間</label><div className="segmented three">{[50,100,250].map((size)=><button key={size} className={windowSize===size?"active":""} onClick={()=>setWindowSize(size)}>{size}m</button>)}</div><p className="hint">25m間隔の標高点から移動平均との差を算出</p></div>
        <div className="legend-block"><label>勾配</label>{[["0 — 1%","緩い","#2aa7a1"],["1 — 2%","やや勾配","#88be5c"],["2 — 3%","勾配あり","#f0b53c"],["3 — 4%","急","#f2763d"],["4%以上","非常に急","#e34444"]].map(([range,name,color])=><div className="legend-row" key={range}><span className="legend-line" style={{background:color}}/><b>{range}</b><small>{name}</small></div>)}</div>
        <div className="data-note"><span>◆</span><p><b>道路高を優先</b><br/>3D道路中心線を優先し、DEM補完は推定値として表示します。</p></div>
      </aside>
      <div className="map-area"><div ref={mapNode} className="map" aria-label="東名・新東名の道路勾配地図"/>{!panelOpen&&<button className="open-panel" onClick={()=>setPanelOpen(true)}>☰ 表示条件</button>}<div className="map-caption"><span className="pulse"/> {filtered.length} 区間を表示中 <em>•</em> 道路をクリックして詳細</div>
        <article className="segment-card"><button className="card-close" aria-label="詳細を閉じる">×</button><div className="segment-title"><RouteIcon route={selected.route}/><div><small>{selected.route}高速道路・{selected.direction}</small><h2>{selected.from} <span>→</span> {selected.to}</h2></div></div><div className="metric-grid"><div><small>平均勾配</small><strong style={{color:gradeColor(selected.grade)}}>{selected.grade>0?"+":""}{selected.grade.toFixed(1)}<span>%</span></strong><em>{selected.grade>=0?"上り勾配":"下り勾配"}</em></div><div><small>道路標高</small><strong>{selected.elevationStart}<span>m</span> <i>→</i> {selected.elevationEnd}<span>m</span></strong><em>{windowSize}m 区間</em></div></div><div className="quality-row"><span className={selected.quality==="3D道路"?"verified":"estimated"}>● {selected.quality==="3D道路"?"3D道路高":"推定値"}</span><small>国土地理院・道路中心線</small><b>{selected.structure}</b></div></article>
      </div>
    </section>
    <section className="profile-panel"><div className="profile-head"><div><RouteIcon route={selected.route}/><span><b>{selected.route}高速道路・{selected.direction}</b><small>御殿場JCT → 浜松いなさJCT　約145 km</small></span></div><div className="segmented profile-toggle"><button className={profileMode==="elevation"?"active":""} onClick={()=>setProfileMode("elevation")}>標高プロファイル</button><button className={profileMode==="grade"?"active":""} onClick={()=>setProfileMode("grade")}>勾配グラフ</button></div></div><ProfileChart mode={profileMode} elevation={routeProfile} grades={gradeProfile}/><div className="profile-stats"><span><small>最高標高</small><b>{Math.max(...routeProfile)} m</b></span><span><small>累積上昇</small><b>{selected.route==="新東名"?"624":"1,148"} m</b></span><span><small>最大勾配</small><b>{Math.max(...gradeProfile.map(Math.abs)).toFixed(1)}%</b></span><span><small>推定区間</small><b>{selected.route==="新東名"?"8.4":"13.2"} km</b></span></div></section>
    <footer><span>試作データ — UI・分析仕様の検証用</span><span>道路縦断勾配 / 25mサンプリング / {windowSize}m移動平均</span></footer>
  </main>;
}

function ProfileChart({mode,elevation,grades}:{mode:"elevation"|"grade";elevation:number[];grades:number[]}) {
  const width=1000,height=118;
  if(mode==="grade")return <div className="chart-wrap"><div className="axis-labels"><span>+4%</span><span>0%</span><span>−4%</span></div><svg className="chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label="勾配グラフ"><line x1="0" y1="59" x2={width} y2="59" className="zero-line"/>{grades.map((g,i)=>{const barW=width/grades.length-4,barH=Math.abs(g)/4.5*48;return <rect key={i} x={i*width/grades.length+2} y={g>0?59-barH:59} width={barW} height={barH} rx="2" fill={gradeColor(g)} opacity=".9"/>})}</svg><DistanceLabels/></div>;
  const min=Math.min(...elevation)-20,max=Math.max(...elevation)+15,pts=elevation.map((v,i)=>`${i/(elevation.length-1)*width},${height-((v-min)/(max-min))*(height-16)}`).join(" ");
  return <div className="chart-wrap"><div className="axis-labels"><span>{Math.round(max)}m</span><span>{Math.round((max+min)/2)}m</span><span>{Math.round(min)}m</span></div><svg className="chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-label="標高プロファイル"><defs><linearGradient id="profileFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#43a489" stopOpacity=".3"/><stop offset="100%" stopColor="#43a489" stopOpacity=".02"/></linearGradient></defs><polygon points={`0,${height} ${pts} ${width},${height}`} fill="url(#profileFill)"/><polyline points={pts} fill="none" stroke="#278c76" strokeWidth="3" vectorEffect="non-scaling-stroke"/></svg><DistanceLabels/></div>;
}
function DistanceLabels(){return <div className="distance-labels"><span>0 km</span><span>25</span><span>50</span><span>75</span><span>100</span><span>125</span><span>145 km</span></div>}
