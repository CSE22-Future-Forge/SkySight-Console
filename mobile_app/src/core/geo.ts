/** Distance + bearing on the phone, fully offline (ARCHITECTURE.md 8 layer table). */

const EARTH_R = 6_371_008.8;
const rad = (d: number) => (d * Math.PI) / 180;
const deg = (r: number) => (r * 180) / Math.PI;

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = rad(lat1);
  const p2 = rad(lat2);
  const dp = p2 - p1;
  const dl = rad(lon2 - lon1);
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_R * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Initial bearing from 1 to 2, degrees clockwise from true north, 0..360. */
export function bearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = rad(lat1);
  const p2 = rad(lat2);
  const dl = rad(lon2 - lon1);
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (deg(Math.atan2(y, x)) + 360) % 360;
}

const CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

export function cardinal(bearing: number): string {
  return CARDINALS[Math.round((((bearing % 360) + 360) % 360) / 45) % 8];
}

export function formatDistance(m: number): string {
  if (!Number.isFinite(m)) return '-';
  if (m < 1000) return `${Math.round(m)} m`;
  return `${(m / 1000).toFixed(m < 10_000 ? 2 : 1)} km`;
}

/** Arrow rotation on screen: bearing to target relative to where the phone points. */
export function relativeBearing(bearingToTarget: number, deviceHeading: number | null): number {
  if (deviceHeading == null || !Number.isFinite(deviceHeading)) return bearingToTarget;
  return (((bearingToTarget - deviceHeading) % 360) + 360) % 360;
}

export interface Fix {
  lat: number;
  lon: number;
}

export function describeFrom(me: Fix | null, target: Fix): { distance: number; bearing: number; text: string } | null {
  if (!me) return null;
  const distance = haversineM(me.lat, me.lon, target.lat, target.lon);
  const bearing = bearingDeg(me.lat, me.lon, target.lat, target.lon);
  return { distance, bearing, text: `${formatDistance(distance)} ${cardinal(bearing)} (${Math.round(bearing)}°)` };
}

export function ageText(sinceMs: number | null | undefined, nowMs = Date.now()): string {
  if (!sinceMs) return '-';
  const s = Math.max(0, Math.floor((nowMs - sinceMs) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h ${m % 60} min ago`;
}
