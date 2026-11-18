/** Team tab: who this phone is, how its link is doing, phone setup, and unpairing. */

import Constants from 'expo-constants';
import { router } from 'expo-router';
import { Alert, ScrollView, StyleSheet, Text, View } from 'react-native';

import { ageText } from '../../core/geo';
import { engine } from '../../engine/engine';
import { useStore } from '../../state/store';
import { Button, Card, ConnectionPill, Field, ScreenHeader, SectionHeader, TeamBadge, useNow } from '../../ui/components';
import { PermissionChecklist } from '../../ui/permissions';
import { colors, font, space, type } from '../../ui/theme';

export default function TeamScreen() {
  useNow();
  const teamName = useStore((s) => s.teamName);
  const callSign = useStore((s) => s.teamCallSign);
  const teamColor = useStore((s) => s.teamColor);
  const mission = useStore((s) => s.missionName);
  const conn = useStore((s) => s.conn);
  const wifi = useStore((s) => s.wifiBound);
  const queued = useStore((s) => s.queued);
  const points = useStore((s) => s.pendingPoints);
  const me = useStore((s) => s.me);
  const map = useStore((s) => s.styleFile);
  const settings = useStore((s) => s.settings);

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScreenHeader subtitle="This phone" title="Team & settings" />
      <ScrollView contentContainerStyle={st.content}>
        <Card style={st.teamCard}>
          <TeamBadge color={teamColor} label={callSign ?? teamName?.slice(0, 2) ?? '?'} size={58} />
          <View style={{ flex: 1, gap: 2 }}>
            <Text style={st.team}>{teamName ? `Team ${teamName}` : 'Not paired'}</Text>
            <Text style={st.dim}>{mission ? `Mission: ${mission}` : 'No mission name from the base'}</Text>
          </View>
        </Card>

        <SectionHeader title="LINK TO THE BASE" />
        <Card style={{ gap: 0 }}>
          <View style={st.linkHead}>
            <Text style={st.label}>Status</Text>
            <ConnectionPill />
          </View>
          <Field icon="server-network" label="Base" value={engine.sync?.server_url ?? '-'} />
          <Field icon={wifi ? 'wifi' : 'wifi-off'} label="SAR-NET Wi-Fi" value={wifi ? 'Bound (no internet needed)' : 'Not bound'} />
          <Field icon="cloud-upload-outline" label="Actions waiting" value={String(queued)} />
          <Field icon="map-marker-path" label="GPS points waiting" value={String(points)} />
          <Field icon="crosshairs-gps" label="Last GPS fix"
                 value={me ? `±${Math.round(me.accuracy ?? 0)} m · ${ageText(me.ts)}` : 'none yet'} />
          <Field icon="map-check-outline" label="Offline map" value={map ? 'Installed' : 'Missing - pair again'} />
          <Field icon="bell-ring-outline" label="Siren repeats" value={`every ${settings.alert_repeat_s} s`} />
          {conn !== 'live' && (
            <Button title="Reconnect now" icon="sync" size="s" style={{ marginTop: space.m }}
                    onPress={() => void engine.reconnect()} />
          )}
        </Card>

        <SectionHeader title="PHONE SETUP" />
        <Text style={st.dim}>All four should be green, or alerts may not reach you with the screen off.</Text>
        <PermissionChecklist />

        <SectionHeader title="DEVICE" />
        <Card style={{ gap: space.m }}>
          <Field icon="cellphone" label="App version" value={String(Constants.expoConfig?.version ?? '-')} />
          <Button title="Unpair this phone" icon="logout" variant="danger" onPress={confirmUnpair} />
        </Card>
      </ScrollView>
    </View>
  );
}

function confirmUnpair() {
  Alert.alert('Unpair this phone?', 'It stops receiving alerts. Queued actions not yet sent to the base are lost.', [
    { text: 'Cancel', style: 'cancel' },
    { text: 'Unpair', style: 'destructive', onPress: async () => { await engine.unpair(); router.replace('/pairing'); } },
  ]);
}

const st = StyleSheet.create({
  content: { padding: space.l, paddingTop: space.xs, gap: space.m, paddingBottom: space.xxl },
  teamCard: { flexDirection: 'row', alignItems: 'center', gap: space.l },
  team: { ...type.h2, color: colors.text },
  dim: { color: colors.dim, fontSize: 14 },
  label: { color: colors.dim, fontSize: 14 },
  linkHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingBottom: space.s },
  version: { color: colors.muted, fontSize: 12, fontWeight: font.medium },
});
