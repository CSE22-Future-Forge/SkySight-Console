/** Home (9.3 #2): offline map, own position, open + own victims, connection pill. */

import { Camera, type CameraRef, GeoJSONSource, Layer, Map, UserLocation } from '@maplibre/maplibre-react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { File } from 'expo-file-system';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ageText, describeFrom } from '../core/geo';
import type { CachedVictim } from '../core/types';
import { engine } from '../engine/engine';
import { EMPTY_STYLE } from '../engine/mapStyle';
import { useStore } from '../state/store';
import { Button, ConnectionPill, PendingBanner, StatusChip } from '../ui/components';
import { colors, radius, space, statusColor } from '../ui/theme';

const SRI_LANKA: [number, number] = [80.7, 7.85];

export default function MapScreen() {
  const insets = useSafeAreaInsets();
  const { focus } = useLocalSearchParams<{ focus?: string }>();
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
  const shown = victims.filter((v) => v.status === 'open' || v.assigned_team_id === teamId);
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

  useEffect(() => {
    if (focus) {
      const v = victims.find((x) => x.id === focus);
      if (v) {
        setFollow(false);
        camera.current?.flyTo({ center: [v.lon, v.lat], zoom: 16 });
      }
    }
  }, [focus]); // eslint-disable-line react-hooks/exhaustive-deps

  const openCount = victims.filter((v) => v.status === 'open').length;
  const mine = victims.filter((v) => v.status === 'ongoing' && v.assigned_team_id === teamId).length;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <Map style={{ flex: 1 }} mapStyle={mapStyle} logo={false} attribution compass compassHiddenFacingNorth
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
                 paint={{ 'circle-radius': pulse ? 26 : 14, 'circle-color': statusColor.open,
                          'circle-opacity': pulse ? 0.12 : 0.32 }} />
          <Layer id="victim-selected" type="circle" filter={['==', ['get', 'id'], selected ?? '']}
                 paint={{ 'circle-radius': 17, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#1565c0',
                          'circle-stroke-width': 4 }} />
          <Layer id="victim-dots" type="circle"
                 paint={{ 'circle-radius': 10,
                          'circle-color': ['match', ['get', 'status'], 'open', statusColor.open, 'ongoing',
                                           statusColor.ongoing, 'completed', statusColor.completed, '#9e9e9e'],
                          'circle-stroke-color': ['case', ['==', ['get', 'pending'], 1], '#ffd54f', '#ffffff'],
                          'circle-stroke-width': 3 }} />
          <Layer id="victim-labels" type="symbol"
                 layout={{ 'text-field': ['get', 'label'], 'text-font': ['Noto Sans Medium'], 'text-size': 13,
                           'text-offset': [0, 1.5], 'text-anchor': 'top', 'text-optional': true }}
                 paint={{ 'text-color': '#111', 'text-halo-color': '#fff', 'text-halo-width': 1.8 }} />
        </GeoJSONSource>
        <UserLocation heading accuracy animated />
      </Map>

      <View style={[st.top, { top: insets.top + space.s }]} pointerEvents="box-none">
        <ConnectionPill />
        <View style={st.teamTag}>
          <Text style={st.teamText}>{teamName ?? ''}</Text>
          <Text style={st.counts}>{`${openCount} open · ${mine} mine`}</Text>
        </View>
      </View>

      {!styleUri && (
        <View style={[st.banner, { top: insets.top + 60 }]}>
          <Text style={st.bannerText}>No offline map on this phone. Pair again on SAR-NET to download it.</Text>
        </View>
      )}

      <View style={[st.fabs, { bottom: (sel ? 230 : space.xl) + insets.bottom }]}>
        <Pressable style={st.fab} onPress={() => { setFollow(true); }}>
          <Text style={st.fabText}>{follow ? '◉' : '◎'}</Text>
        </Pressable>
        <Pressable style={st.fab} onPress={() => router.push('/jobs')}>
          <Text style={st.fabText}>{'☰'}</Text>
        </Pressable>
        <Pressable style={st.fab} onLongPress={() => confirmUnpair()} onPress={() =>
          Alert.alert('SAR Rescue', 'Long-press this button to unpair the phone from the base.')}>
          <Text style={st.fabText}>{'⚙'}</Text>
        </Pressable>
      </View>

      {sel && <VictimCard v={sel} me={me} teamId={teamId} bottom={insets.bottom} />}
    </View>
  );
}

function confirmUnpair() {
  Alert.alert('Unpair this phone?', 'Queued actions not yet sent to the base will be lost.', [
    { text: 'Cancel', style: 'cancel' },
    { text: 'Unpair', style: 'destructive', onPress: async () => { await engine.unpair(); router.replace('/pairing'); } },
  ]);
}

function VictimCard({ v, me, teamId, bottom }: { v: CachedVictim; me: any; teamId: string | null; bottom: number }) {
  const where = describeFrom(me, v);
  const mine = v.assigned_team_id === teamId;
  return (
    <View style={[st.card, { paddingBottom: space.l + bottom }]}>
      <View style={st.cardHead}>
        <Text style={st.code}>{v.display_code}</Text>
        <StatusChip v={v} />
      </View>
      <Text style={st.where}>{where ? where.text : 'Waiting for your GPS position'}</Text>
      <Text style={st.meta}>
        {v.max_confidence != null ? `${Math.round(v.max_confidence * 100)}% sure · ` : ''}seen {ageText(v.last_seen_at)}
        {v.position_error_m ? ` · ±${Math.round(v.position_error_m)} m` : ''}
      </Text>
      <PendingBanner v={v} />
      <View style={st.row}>
        {v.status === 'open' && !v.local_pending_action && (
          <Button title="Accept" variant="primary" style={{ flex: 1 }} onPress={() => void engine.accept(v.id)} />
        )}
        {v.status === 'ongoing' && mine && !v.local_pending_action && (
          <Button title="Mark rescued" variant="ok" style={{ flex: 1 }} onPress={() => router.push(`/complete/${v.id}`)} />
        )}
        <Button title="Details" style={{ flex: 1 }} onPress={() => router.push(`/victim/${v.id}`)} />
      </View>
    </View>
  );
}

const st = StyleSheet.create({
  top: { position: 'absolute', left: space.m, right: space.m, flexDirection: 'row', justifyContent: 'space-between',
         alignItems: 'flex-start' },
  teamTag: { backgroundColor: colors.panel + 'ee', borderRadius: radius, paddingHorizontal: 10, paddingVertical: 5,
             alignItems: 'flex-end' },
  teamText: { color: colors.text, fontWeight: '800' },
  counts: { color: colors.dim, fontSize: 12 },
  banner: { position: 'absolute', left: space.m, right: space.m, backgroundColor: '#5a4300', borderRadius: radius,
            padding: space.m },
  bannerText: { color: '#ffe0a3', fontWeight: '600' },
  fabs: { position: 'absolute', right: space.m, gap: space.s },
  fab: { width: 54, height: 54, borderRadius: 27, backgroundColor: colors.panel, borderWidth: 1, borderColor: colors.border,
         alignItems: 'center', justifyContent: 'center', elevation: 4 },
  fabText: { color: colors.text, fontSize: 24 },
  card: { position: 'absolute', left: 0, right: 0, bottom: 0, backgroundColor: colors.panel, borderTopLeftRadius: 18,
          borderTopRightRadius: 18, padding: space.l, gap: space.s, borderTopWidth: 1, borderColor: colors.border,
          elevation: 10 },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  code: { color: colors.text, fontSize: 26, fontWeight: '900' },
  where: { color: colors.text, fontSize: 18, fontWeight: '700' },
  meta: { color: colors.dim, fontSize: 14 },
  row: { flexDirection: 'row', gap: space.m, marginTop: space.s },
});
