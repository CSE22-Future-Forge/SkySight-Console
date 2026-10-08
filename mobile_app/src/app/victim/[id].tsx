/** Victim detail (9.3 #4): image, coordinates, compass + distance, history, state actions. */

import * as Clipboard from 'expo-clipboard';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Alert, Image, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { ageText, describeFrom } from '../../core/geo';
import { engine } from '../../engine/engine';
import { store, useStore, useVictim } from '../../state/store';
import { Button, CompassArrow, PendingBanner, StatusChip } from '../../ui/components';
import { colors, radius, space } from '../../ui/theme';

interface HistoryRow {
  from_status: string | null;
  to_status: string;
  team_name: string | null;
  actor_type: string;
  server_ts: number;
  note: string | null;
}

export default function VictimDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const v = useVictim(id);
  const me = useStore((s) => s.me);
  const teamId = useStore((s) => s.teamId);
  const conn = useStore((s) => s.conn);
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [fullFrame, setFullFrame] = useState(false);

  useEffect(() => {
    if (!id || conn !== 'live') return;
    void engine.victimDetail(id).then((res) => res && setHistory(res.history));
  }, [id, conn]);

  if (!v) {
    return (
      <View style={st.page}>
        <Text style={st.dim}>This victim is no longer on your manifest (taken by another team or withdrawn).</Text>
        <Button title="Back to map" onPress={() => router.replace('/map')} />
      </View>
    );
  }
  const where = describeFrom(me, v);
  const mine = v.assigned_team_id === teamId;
  const api = engine.api;
  const coords = `${v.lat.toFixed(6)}, ${v.lon.toFixed(6)}`;

  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={st.page}>
      <Stack.Screen options={{ title: v.display_code }} />
      <View style={st.head}>
        <Text style={st.code}>{v.display_code}</Text>
        <StatusChip v={v} />
      </View>
      <PendingBanner v={v} />

      <Pressable onPress={() => api && v.thumb_url && conn === 'live' && setFullFrame(true)}>
        {v.thumb_local_path ? (
          <Image source={{ uri: v.thumb_local_path }} style={st.thumb} resizeMode="contain" />
        ) : (
          <View style={[st.thumb, st.center]}><Text style={st.dim}>{v.source === 'manual' ? 'Reported by the base (no image)' : 'Image not downloaded yet'}</Text></View>
        )}
        {conn === 'live' && v.thumb_url ? <Text style={st.hint}>Tap for the full drone frame</Text> : null}
      </Pressable>

      <View style={st.card}>
        {where ? (
          <View style={st.row}>
            <CompassArrow bearing={where.bearing} />
            <View style={{ flex: 1 }}>
              <Text style={st.big}>{where.text}</Text>
              <Text style={st.dim}>{me?.accuracy ? `your GPS ±${Math.round(me.accuracy)} m` : ''}</Text>
            </View>
          </View>
        ) : (
          <Text style={st.dim}>Waiting for your GPS position...</Text>
        )}
      </View>

      <View style={st.card}>
        <Field label="Coordinates" value={coords} />
        <Button title="Copy coordinates" onPress={() => { void Clipboard.setStringAsync(coords); store.toast('Coordinates copied'); }} />
        <Field label="Position error" value={v.position_error_m ? `±${Math.round(v.position_error_m)} m` : '-'} />
        <Field label="Confidence" value={v.max_confidence != null ? `${Math.round(v.max_confidence * 100)}%` : '-'} />
        <Field label="Seen by" value={{ both: 'RGB + thermal', rgb: 'RGB camera', thermal: 'Thermal camera' }[v.modalities ?? 'rgb'] ?? '-'} />
        <Field label="Sightings" value={String(v.sighting_count)} />
        <Field label="First detected" value={ageText(v.first_detected_at)} />
        {v.people_count != null && <Field label="People rescued" value={String(v.people_count)} />}
      </View>

      <View style={{ gap: space.m }}>
        {v.status === 'open' && !v.local_pending_action && (
          <Button title="Accept - we are going" variant="primary" onPress={() => void engine.accept(v.id)} />
        )}
        {v.status === 'ongoing' && mine && !v.local_pending_action && (
          <>
            <Button title="Mark rescued" variant="ok" onPress={() => router.push(`/complete/${v.id}`)} />
            <Button title="Release (cannot reach)" variant="danger" onPress={() => confirmRelease(v.id)} />
          </>
        )}
        <Button title="Show on map" onPress={() => router.push({ pathname: '/map', params: { focus: v.id } })} />
      </View>

      <Text style={st.section}>HISTORY</Text>
      {history === null ? (
        <Text style={st.dim}>{conn === 'live' ? 'Loading...' : 'History loads when connected to the base.'}</Text>
      ) : (
        history.map((h, i) => (
          <Text key={i} style={st.hist}>
            {new Date(h.server_ts).toLocaleTimeString()}  {h.from_status ?? 'new'} {'→'} {h.to_status}
            {h.team_name ? `  (${h.team_name})` : h.actor_type === 'coordinator' ? '  (base)' : ''}
            {h.note ? `\n   "${h.note}"` : ''}
          </Text>
        ))
      )}

      <Modal visible={fullFrame} onRequestClose={() => setFullFrame(false)} animationType="fade">
        <Pressable style={{ flex: 1, backgroundColor: '#000' }} onPress={() => setFullFrame(false)}>
          {api && (
            <Image style={{ flex: 1 }} resizeMode="contain"
                   source={{ uri: api.url(`/api/v1/victims/${v.id}/image?size=frame`), headers: api.authHeaders() }} />
          )}
        </Pressable>
      </Modal>
    </ScrollView>
  );
}

function confirmRelease(id: string) {
  Alert.alert('Release this victim?', 'Every team will see it as open again.', [
    { text: 'Cancel', style: 'cancel' },
    { text: 'Release', style: 'destructive', onPress: () => void engine.release(id, 'released from phone') },
  ]);
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <View style={st.field}>
      <Text style={st.dim}>{label}</Text>
      <Text style={st.value} selectable>{value}</Text>
    </View>
  );
}

const st = StyleSheet.create({
  page: { padding: space.l, gap: space.m, backgroundColor: colors.bg },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  code: { color: colors.text, fontSize: 30, fontWeight: '900' },
  thumb: { width: '100%', height: 220, borderRadius: radius, backgroundColor: '#000' },
  center: { alignItems: 'center', justifyContent: 'center' },
  hint: { color: colors.muted, fontSize: 12, textAlign: 'center', marginTop: 4 },
  card: { backgroundColor: colors.panel, borderRadius: radius, padding: space.l, gap: space.s, borderWidth: 1,
          borderColor: colors.border },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.l },
  big: { color: colors.text, fontSize: 22, fontWeight: '800' },
  dim: { color: colors.dim, fontSize: 14 },
  field: { flexDirection: 'row', justifyContent: 'space-between', gap: space.m },
  value: { color: colors.text, fontSize: 15, fontWeight: '600', flexShrink: 1, textAlign: 'right' },
  section: { color: colors.dim, fontWeight: '800', letterSpacing: 1, marginTop: space.m },
  hist: { color: colors.text, fontSize: 13, fontFamily: 'monospace' },
});
