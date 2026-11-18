/** Map (9.3 #2): offline map, own position, open + own victims, connection pill. */

import { Camera, type CameraRef, GeoJSONSource, Layer, Map, UserLocation } from '@maplibre/maplibre-react-native';
import { File } from 'expo-file-system';
import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ageText, describeFrom, type Fix } from '../../core/geo';
import type { CachedVictim } from '../../core/types';
import { engine } from '../../engine/engine';
import { EMPTY_STYLE } from '../../engine/mapStyle';
import { useStore } from '../../state/store';
import {
  Button, CompassArrow, ConnectionPill, Icon, IconButton, PendingBanner, StatusChip, VictimThumb,
} from '../../ui/components';
import { alpha, colors, font, radii, shadow, space, statusColor, type } from '../../ui/theme';

const SRI_LANKA: [number, number] = [80.7, 7.85];

export default function MapScreen() {
  const insets = useSafeAreaInsets();
  const { focus, at } = useLocalSearchParams<{ focus?: string; at?: string }>();
  const victims = useStore((s) => s.victims);
  const teamId = useStore((s) => s.teamId);
  const teamName = useStore((s) => s.teamName);
  const me = useStore((s) => s.me);
  const styleUri = useStore((s) => s.styleFile);
  const [selected, setSelected] = useState<string | null>(focus ?? null);
  const [pulse, setPulse] = useState(false);
  const [follow, setFollow] = useState(!focus);
  const camera = useRef<CameraRef>(null);

  const mapStyle = useMemo(() => {
    if (!styleUri) return EMPTY_STYLE;
    try {
      return JSON.parse(new File(styleUri).textSync());
    } catch {
      return EMPTY_STYLE;
    }
  }, [styleUri]);

  // Open victims pulse (8 layer table: "all, red, pulsing").
  useEffect(() => {
    const t = setInterval(() => setPulse((p) => !p), 700);
    return () => clearInterval(t);
  }, []);

  // Server already filtered visibility; show open + this team's ongoing/completed.
  const shown = useMemo(() => victims.filter((v) => v.status === 'open' || v.assigned_team_id === teamId),
                        [victims, teamId]);
  const data = useMemo(() => ({
    type: 'FeatureCollection' as const,
    features: shown.map((v) => ({
      type: 'Feature' as const,
      id: v.id,
      properties: { id: v.id, status: v.status, label: v.display_code, pending: v.local_pending_action ? 1 : 0 },
      geometry: { type: 'Point' as const, coordinates: [v.lon, v.lat] },
    })),
  }), [shown]);

  const sel = shown.find((v) => v.id === selected) ?? null;

  // Focus requests come from other screens (Dashboard, alert, victim detail).
  useEffect(() => {
    if (!focus) return;
    const v = victims.find((x) => x.id === focus);
    if (v) {
      setSelected(v.id);
      setFollow(false);
      camera.current?.flyTo({ center: [v.lon, v.lat], zoom: 16 });
    }
  }, [focus, at]); // eslint-disable-line react-hooks/exhaustive-deps

  function fitAll() {
    const pts: Fix[] = [...shown, ...(me ? [me] : [])];
    if (!pts.length) return;
    setFollow(false);
    if (pts.length === 1) {
      camera.current?.flyTo({ center: [pts[0].lon, pts[0].lat], zoom: 15 });
      return;
    }
    const lons = pts.map((p) => p.lon);
    const lats = pts.map((p) => p.lat);
    camera.current?.fitBounds([Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)],
                              { padding: { top: 140, bottom: 260, left: 60, right: 60 } });
  }

  const openCount = shown.filter((v) => v.status === 'open').length;
  const mine = shown.filter((v) => v.status === 'ongoing' && v.assigned_team_id === teamId).length;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <Map style={{ flex: 1 }} mapStyle={mapStyle} logo={false} attribution={false} compass compassHiddenFacingNorth
           onPress={() => setSelected(null)} onRegionWillChange={(e: any) => {
             if (e?.nativeEvent?.userInteraction) setFollow(false);
           }}>
        <Camera ref={camera}
                initialViewState={me ? { center: [me.lon, me.lat], zoom: 15 } : { center: SRI_LANKA, zoom: 7 }}
                trackUserLocation={follow ? 'heading' : undefined} minZoom={5} maxZoom={18} />
        <GeoJSONSource id="victims" data={data} hitbox={{ top: 22, bottom: 22, left: 22, right: 22 }}
                       onPress={(e) => {
                         const id = e.nativeEvent.features?.[0]?.properties?.id as string | undefined;
                         if (id) setSelected(id);
                       }}>
          <Layer id="victim-pulse" type="circle" filter={['==', ['get', 'status'], 'open']}
                 paint={{ 'circle-radius': pulse ? 28 : 15, 'circle-color': statusColor.open,
                          'circle-opacity': pulse ? 0.1 : 0.3 }} />
          <Layer id="victim-selected" type="circle" filter={['==', ['get', 'id'], selected ?? '']}
                 paint={{ 'circle-radius': 19, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': colors.accent,
                          'circle-stroke-width': 4 }} />
          <Layer id="victim-dots" type="circle"
                 paint={{ 'circle-radius': 11,
                          'circle-color': ['match', ['get', 'status'], 'open', statusColor.open, 'ongoing',
                                           statusColor.ongoing, 'completed', statusColor.completed, '#94a3b8'],
                          'circle-stroke-color': ['case', ['==', ['get', 'pending'], 1], '#fcd34d', '#ffffff'],
                          'circle-stroke-width': 3 }} />
          <Layer id="victim-labels" type="symbol"
                 layout={{ 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Medium'], 'text-size': 13,
                           'text-offset': [0, 1.6], 'text-anchor': 'top', 'text-optional': true }}
                 paint={{ 'text-color': '#0f172a', 'text-halo-color': '#ffffff', 'text-halo-width': 2 }} />
        </GeoJSONSource>
        <UserLocation heading accuracy animated />
      </Map>

      <View style={[st.top, { top: insets.top + space.s }]} pointerEvents="box-none">
        <View style={st.glass}>
          <View style={{ flex: 1 }}>
            <Text style={st.teamText} numberOfLines={1}>{teamName ? `Team ${teamName}` : 'SAR Rescue'}</Text>
            <View style={st.counts}>
              <View style={[st.countDot, { backgroundColor: statusColor.open }]} />
              <Text style={st.countText}>{openCount} open</Text>
              <View style={[st.countDot, { backgroundColor: statusColor.ongoing }]} />
              <Text style={st.countText}>{mine} yours</Text>
            </View>
          </View>
          <ConnectionPill />
        </View>
        {!styleUri && (
          <View style={st.banner}>
            <Icon name="map-marker-alert" size={20} color={colors.warn} />
            <Text style={st.bannerText}>No offline map on this phone. Pair again on SAR-NET to download it.</Text>
          </View>
        )}
      </View>

      <View style={[st.fabs, { bottom: (sel ? 300 : space.l) }]}>
        <IconButton icon="fit-to-screen-outline" label="Show all victims" onPress={fitAll} />
        <IconButton icon={follow ? 'crosshairs-gps' : 'crosshairs'} label="Follow my position" active={follow}
                    onPress={() => setFollow(true)} />
      </View>

      {sel && <VictimSheet v={sel} me={me} teamId={teamId} onClose={() => setSelected(null)} />}
    </View>
  );
}

function VictimSheet({ v, me, teamId, onClose }: {
  v: CachedVictim; me: Fix | null; teamId: string | null; onClose: () => void;
}) {
  const where = describeFrom(me, v);
  const mine = v.assigned_team_id === teamId;
  return (
    <View style={st.sheet}>
      <View style={st.grabber} />
      <View style={st.sheetHead}>
        <VictimThumb v={v} size={58} />
        <View style={{ flex: 1, gap: 4 }}>
          <View style={st.row}>
            <Text style={st.code}>{v.display_code}</Text>
            <StatusChip v={v} />
          </View>
          <Text style={st.meta}>
            {v.max_confidence != null ? `${Math.round(v.max_confidence * 100)}% sure · ` : ''}seen {ageText(v.last_seen_at)}
            {v.position_error_m ? ` · ±${Math.round(v.position_error_m)} m` : ''}
          </Text>
        </View>
        <IconButton icon="close" label="Close" onPress={onClose} style={st.close} />
      </View>
      <View style={st.whereRow}>
        {where ? <CompassArrow bearing={where.bearing} size={64} /> : <Icon name="crosshairs-question" size={30} color={colors.muted} />}
        <Text style={st.where}>{where ? where.text : 'Waiting for your GPS position'}</Text>
      </View>
      <PendingBanner v={v} />
      <View style={st.row}>
        {v.status === 'open' && !v.local_pending_action && (
          <Button title="Accept" icon="run-fast" variant="primary" style={{ flex: 1 }} onPress={() => void engine.accept(v.id)} />
        )}
        {v.status === 'ongoing' && mine && !v.local_pending_action && (
          <Button title="Mark rescued" icon="check-circle" variant="success" style={{ flex: 1 }}
                  onPress={() => router.push(`/complete/${v.id}`)} />
        )}
        <Button title="Details" icon="information-outline" style={{ flex: 1 }} onPress={() => router.push(`/victim/${v.id}`)} />
      </View>
    </View>
  );
}

const st = StyleSheet.create({
  top: { position: 'absolute', left: space.m, right: space.m, gap: space.s },
  glass: { flexDirection: 'row', alignItems: 'center', gap: space.m, backgroundColor: alpha(colors.surface, 0.94),
           borderRadius: radii.l, borderWidth: 1, borderColor: colors.border, paddingHorizontal: space.l,
           paddingVertical: space.m, ...shadow.float },
  teamText: { color: colors.text, fontWeight: font.heavy, fontSize: 16 },
  counts: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 3 },
  countDot: { width: 8, height: 8, borderRadius: 4 },
  countText: { color: colors.dim, fontSize: 13, fontWeight: font.semibold, marginRight: 6 },
  banner: { flexDirection: 'row', alignItems: 'center', gap: space.s, backgroundColor: alpha(colors.warn, 0.16),
            borderColor: alpha(colors.warn, 0.5), borderWidth: 1, borderRadius: radii.m, padding: space.m },
  bannerText: { color: '#FCD34D', fontWeight: font.semibold, flex: 1 },
  fabs: { position: 'absolute', right: space.m, gap: space.s },
  sheet: { position: 'absolute', left: space.s, right: space.s, bottom: space.s, backgroundColor: colors.surface,
           borderRadius: radii.xl, padding: space.l, paddingTop: space.s, gap: space.m, borderWidth: 1,
           borderColor: colors.border, ...shadow.float },
  grabber: { alignSelf: 'center', width: 40, height: 4, borderRadius: 2, backgroundColor: colors.borderStrong },
  sheetHead: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  close: { width: 40, height: 40, borderRadius: 20, elevation: 0, shadowOpacity: 0 },
  code: { ...type.h2, color: colors.text },
  whereRow: { flexDirection: 'row', alignItems: 'center', gap: space.l },
  where: { color: colors.text, fontSize: 18, fontWeight: font.bold, flex: 1 },
  meta: { color: colors.dim, fontSize: 13 },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.s },
});
