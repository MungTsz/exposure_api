const fs = require('fs');
const path = require('path');

function haversine(a, b) {
  const toRad = v => v * Math.PI / 180;
  const R = 6371000;
  const dLat = toRad(b[1]-a[1]);
  const dLon = toRad(b[0]-a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);
  const sinDLat = Math.sin(dLat/2);
  const sinDLon = Math.sin(dLon/2);
  const c = 2 * Math.asin(Math.sqrt(sinDLat*sinDLat + Math.cos(lat1)*Math.cos(lat2)*sinDLon*sinDLon));
  return R * c;
}

function douglasPeucker(points, epsilon) {
  if (points.length < 3) return points;
  const sqEps = epsilon*epsilon;

  function perpendicularDistance(pt, lineStart, lineEnd) {
    const x0 = pt[0], y0 = pt[1];
    const x1 = lineStart[0], y1 = lineStart[1];
    const x2 = lineEnd[0], y2 = lineEnd[1];
    const dx = x2 - x1;
    const dy = y2 - y1;
    if (dx === 0 && dy === 0) return haversine(pt, lineStart);
    const t = ((x0 - x1) * dx + (y0 - y1) * dy) / (dx*dx + dy*dy);
    const proj = [x1 + t*dx, y1 + t*dy];
    return haversine(pt, proj);
  }

  function recurse(pts) {
    let maxDist = 0, index = -1;
    const start = pts[0], end = pts[pts.length-1];
    for (let i=1;i<pts.length-1;i++){
      const d = perpendicularDistance(pts[i], start, end);
      if (d > maxDist) { maxDist = d; index = i; }
    }
    if (maxDist > epsilon) {
      const left = recurse(pts.slice(0, index+1));
      const right = recurse(pts.slice(index));
      return left.slice(0,-1).concat(right);
    }
    return [start, end];
  }

  return recurse(points);
}

function movingAverageSmooth(points, windowSize) {
  if (points.length <= 2) return points;
  const half = Math.floor(windowSize/2);
  const out = [];
  for (let i=0;i<points.length;i++){
    let sumX=0,sumY=0,count=0;
    for (let j=Math.max(0,i-half); j<=Math.min(points.length-1,i+half); j++){
      sumX += points[j][0];
      sumY += points[j][1];
      count++;
    }
    out.push([sumX/count, sumY/count]);
  }
  return out;
}

function processFeatures(features, opts){
  // extract ordered points with timestamps
  const pts = features.map(f=>{
    const [lon,lat] = f.geometry.coordinates;
    const t = f.properties && (f.properties.time_long || Date.parse(f.properties.time) || null);
    return {lon,lat,t,raw:f};
  }).filter(p=>p.t!==null).sort((a,b)=>a.t-b.t);

  // segment on time/distance gaps
  const segs = [];
  let cur = [];
  for (let i=0;i<pts.length;i++){
    const p = pts[i];
    if (cur.length===0) { cur.push(p); continue; }
    const prev = cur[cur.length-1];
    const dt = (p.t - prev.t)/1000; // seconds
    const dist = haversine([prev.lon,prev.lat],[p.lon,p.lat]);
    if (dt > opts.timeGap || dist > opts.distGap) {
      segs.push(cur);
      cur = [p];
    } else {
      cur.push(p);
    }
  }
  if (cur.length) segs.push(cur);

  const outSegments = segs.map(seg=>{
    const rawPoints = seg.map(p=>[p.lon,p.lat]);
    const simplified = douglasPeucker(rawPoints, opts.simplifyMeters);
    const smoothed = movingAverageSmooth(simplified, opts.smoothWindow);
    // keep timestamps only for endpoints and sample evenly for others
    return smoothed.map((pt, idx)=>({coordinates:pt,index:idx}));
  });

  return outSegments;
}

function main(){
  const infile = process.argv[2] || path.join(__dirname,'..','testing','data','20260830.geojson');
  const outFile = process.argv[3] || path.join(__dirname,'..','testing','data','20260830.processed.geojson');
  const raw = JSON.parse(fs.readFileSync(infile,'utf8'));
  const opts = {timeGap: 120, distGap: 200, simplifyMeters:5, smoothWindow:3};
  const segs = processFeatures(raw.features, opts);

  const out = {type:'FeatureCollection',features:[]};
  segs.forEach((seg,i)=>{
    const coords = seg.map(p=>p.coordinates);
    out.features.push({type:'Feature',properties:{segment:i},geometry:{type:'LineString',coordinates:coords}});
  });
  fs.writeFileSync(outFile, JSON.stringify(out,null,2));
  console.log('Wrote', outFile);
}

if (require.main===module) main();
