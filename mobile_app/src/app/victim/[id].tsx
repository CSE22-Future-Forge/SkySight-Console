/** Victim detail (9.3 #4): image, coordinates, compass + distance, history, state actions. */

import * as Clipboard from 'expo-clipboard';
import { router, Stack, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Alert, Image, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ageText, describeFrom, formatDistance } from '../../core/geo';
import { engine } from '../../engine/engine';
import { store, useStore, useVictim } from '../../state/store';
import {
  Button, Card, CompassArrow, EmptyState, Field, Icon, PendingBanner, SectionHeader, StatusChip, useNow,
} from '../../ui/components';
import { alpha, colors, font, radii, space, statusColor, statusLabel, type } from '../../ui/theme';

interface HistoryRow {
  from_status: string | null;
  to_status: string;
  team_name: string | null;
  actor_type: string;
  server_ts: number;
  note: string | null;
}

const SEEN_BY: Record<string, string> = { both: 'RGB + thermal', rgb: 'RGB camera', thermal: 'Thermal camera' };

export default function VictimDetail() {
  useNow();
  const { id } = useLocalSearchParams<{ id: string }>();
  const v = useVictim(id);
  const me = useStore((s) => s.me);
  const teamId = useStore((s) => s.teamId);
  const conn = useStore((s) => s.conn);
  const insets = useSafeAreaInsets();
  const [history, setHistory] = useState<HistoryRow[] | null>(null);
  const [fullFrame, setFullFrame] = useState(false);

  useEffect(() => {
    if (!id || conn !== 'live') return;
    void engine.victimDetail(id).then((res) => res && setHistory(res.history));
  }, [id, conn, v?.version]);

  if (!v) {
    return (
      <View style={st.missing}>
        <EmptyState icon="account-off-outline" title="Not on your manifest"
                    text="This victim was taken by another team or withdrawn by the base.">
          <Button title="Back to the map" icon="map-outline" style={{ alignSelf: 'stretch', marginTop: space.m }}
                  onPress={() => router.navigate('/map')} />
        </EmptyState>
      </View>
    );
  }
  const where = describeFrom(me, v);
  const mine = v.assigned_team_id === teamId;
  const api = engine.api;
  const coords = `${v.lat.toFixed(6)}, ${v.lon.toFixed(6)}`;
  const canAccept = v.status === 'open' && !v.local_pending_action;
  const canFinish = v.status === 'ongoing' && mine && !v.local_pending_action;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <Stack.Screen options={{ title: v.display_code }} />
      <ScrollView contentContainerStyle={[st.page, { paddingBottom: (canAccept || canFinish ? 170 : 40) + insets.bottom }]}>
        <Pressable onPress={() => api && v.thumb_url && conn === 'live' && setFullFrame(true)} style={st.hero}>
          {v.thumb_local_path ? (
            <Image source={{ uri: v.thumb_local_path }} style={st.heroImage} resizeMode="contain" />
          ) : (
            <View style={[st.heroImage, st.center]}>
              <Icon name="image-off-outline" size={36} color={colors.muted} />
              <Text style={st.dim}>{v.source === 'manual' ? 'Reported by the base (no image)' : 'Image not downloaded yet'}</Text>
            </View>
          )}
          {conn === 'live' && v.thumb_url ? (
            <View style={st.heroHint}>
              <Icon name="fullscreen" size={14} color={colors.text} />
              <Text style={st.heroHintText}>Full drone frame</Text>
            </View>
          ) : null}
        </Pressable>

        <View style={st.titleRow}>
          <Text style={st.code}>{v.display_code}</Text>
          <StatusChip v={v} />
        </View>
        <PendingBanner v={v} />

        <Card style={st.navCard}>
          {where ? <CompassArrow bearing={where.bearing} size={92} /> : (
            <View style={st.noGps}><Icon name="crosshairs-question" size={34} color={colors.muted} /></View>
          )}
          <View style={{ flex: 1, gap: 2 }}>
            <Text style={st.distance}>{where ? formatDistance(where.distance) : 'No GPS fix'}</Text>
            <Text style={st.dim}>{where ? where.text : 'Waiting for your GPS position…'}</Text>
            {me?.accuracy ? <Text style={st.muted}>{`your GPS ±${Math.round(me.accuracy)} m`}</Text> : null}
          </View>
        </Card>

        <SectionHeader title="DETAILS" />
        <Card style={{ gap: 0 }}>
          <Field icon="map-marker-outline" label="Coordinates" value={coords} />
          <Field icon="crosshairs" label="Position error" value={v.position_error_m ? `±${Math.round(v.position_error_m)} m` : '-'} />
          <Field icon="target" label="Confidence" value={v.max_confidence != null ? `${Math.round(v.max_confidence * 100)}%` : '-'} />
          <Field icon="camera-outline" label="Seen by" value={SEEN_BY[v.modalities ?? 'rgb'] ?? '-'} />
          <Field icon="eye-outline" label="Sightings" value={String(v.sighting_count)} />
          <Field icon="clock-outline" label="First detected" value={ageText(v.first_detected_at)} />
          {v.people_count != null && <Field icon="account-multiple" label="People rescued" value={String(v.people_count)} />}
          <View style={st.inlineButtons}>
            <Button title="Copy coordinates" icon="content-copy" size="s" style={{ flex: 1 }}
                    onPress={() => { void Clipboard.setStringAsync(coords); store.toast('Coordinates copied'); }} />
            <Button title="On map" icon="map-marker-radius" size="s" style={{ flex: 1 }}
                    onPress={() => router.navigate({ pathname: '/map', params: { focus: v.id, at: String(Date.now()) } })} />
          </View>
        </Card>

        <SectionHeader title="TIMELINE" />
        <Card style={{ gap: 0 }}>
          {history === null ? (
            <Text style={st.dim}>{conn === 'live' ? 'Loading…' : 'The timeline loads when connected to the base.'}</Text>
          ) : history.length === 0 ? (
            <Text style={st.dim}>No changes yet.</Text>
          ) : history.map((h, i) => {
            const color = statusColor[h.to_status] ?? colors.dim;
            const who = h.team_name ? h.team_name : h.actor_type === 'coordinator' ? 'Base' : h.actor_type === 'system' ? 'Drone' : '';
            return (
              <View key={i} style={st.tlRow}>
                <View style={st.tlRail}>
                  <View style={[st.tlDot, { backgroundColor: color }]} />
                  {i < history.length - 1 && <View style={st.tlLine} />}
                </View>
                <View style={{ flex: 1, paddingBottom: space.m }}>
                  <Text style={st.tlTitle}>
                    {statusLabel[h.to_status] ?? h.to_status}{who ? <Text style={st.muted}>{`  ·  ${who}`}</Text> : null}
                  </Text>
                  <Text style={st.muted}>{new Date(h.server_ts).toLocaleTimeString()}</Text>
                  {h.note ? <Text style={st.note}>{`"${h.note}"`}</Text> : null}
                </View>
              </View>
            );
          })}
        </Card>
      </ScrollView>

      {(canAccept || canFinish) && (
        <View style={[st.footer, { paddingBottom: insets.bottom + space.m }]}>
          {canAccept && (
            <Button title="Accept · we are going" icon="run-fast" variant="primary" size="l"
                    onPress={() => void engine.accept(v.id)} />
          )}
          {canFinish && (
            <>
              <Button title="Mark rescued" icon="check-circle" variant="success" size="l"
                      onPress={() => router.push(`/complete/${v.id}`)} />
              <Button title="Release - we cannot reach it" icon="undo" variant="danger" size="s"
                      onPress={() => confirmRelease(v.id)} />
            </>
          )}
        </View>
      )}

      <Modal visible={fullFrame} onRequestClose={() => setFullFrame(false)} animationType="fade">
        <Pressable style={{ flex: 1, backgroundColor: '#000' }} onPress={() => setFullFrame(false)}>
          {api && (
            <Image style={{ flex: 1 }} resizeMode="contain"
                   source={{ uri: api.url(`/api/v1/victims/${v.id}/image?size=frame`), headers: api.authHeaders() }} />
          )}
        </Pressable>
      </Modal>
    </View>
  );
}

function confirmRelease(id: string) {
  Alert.alert('Release this victim?', 'Every team will see it as open again and the siren rings on their phones.', [
    { text: 'Cancel', style: 'cancel' },
    { text: 'Release', style: 'destructive', onPress: () => void engine.release(id, 'released from phone') },
  ]);
}

const st = StyleSheet.create({
  page: { padding: space.l, gap: space.m },
  missing: { flex: 1, backgroundColor: colors.bg, justifyContent: 'center', padding: space.l },
  hero: { borderRadius: radii.l, overflow: 'hidden', borderWidth: 1, borderColor: colors.border },
  heroImage: { width: '100%', height: 240, backgroundColor: '#000' },
  heroHint: { position: 'absolute', right: space.s, bottom: space.s, flexDirection: 'row', alignItems: 'center', gap: 4,
              backgroundColor: alpha('#000000', 0.6), borderRadius: radii.pill, paddingHorizontal: 10, paddingVertical: 4 },
  heroHintText: { color: colors.text, fontSize: 12, fontWeight: font.semibold },
  center: { alignItems: 'center', justifyContent: 'center', gap: space.s },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  code: { ...type.display, color: colors.text },
  navCard: { flexDirection: 'row', alignItems: 'center', gap: space.l },
  noGps: { width: 92, height: 92, borderRadius: 46, backgroundColor: colors.surface2, alignItems: 'center',
           justifyContent: 'center' },
  distance: { fontSize: 32, fontWeight: font.heavy, color: colors.text, letterSpacing: -0.5 },
  dim: { color: colors.dim, fontSize: 14 },
  muted: { color: colors.muted, fontSize: 12, fontWeight: font.regular },
  inlineButtons: { flexDirection: 'row', gap: space.s, marginTop: space.m },
  tlRow: { flexDirection: 'row', gap: space.m },
  tlRail: { width: 14, alignItems: 'center' },
  tlDot: { width: 12, height: 12, borderRadius: 6, marginTop: 4 },
  tlLine: { flex: 1, width: 2, backgroundColor: colors.border, marginTop: 2 },
  tlTitle: { color: colors.text, fontSize: 15, fontWeight: font.bold },
  note: { color: colors.dim, fontSize: 14, fontStyle: 'italic', marginTop: 2 },
  footer: { position: 'absolute', left: 0, right: 0, bottom: 0, padding: space.l, gap: space.s,
            backgroundColor: alpha(colors.bg, 0.97), borderTopWidth: 1, borderTopColor: colors.border },
});
