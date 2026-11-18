/** Dashboard: the team's situation at a glance, and the next thing to do. */

import { router } from 'expo-router';
import { useMemo } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { ageText, describeFrom, formatDistance } from '../../core/geo';
import type { CachedVictim } from '../../core/types';
import { engine } from '../../engine/engine';
import { activityFeed, myJobs, openByDistance, teamStats } from '../../state/selectors';
import { unseenAlerts, useStore } from '../../state/store';
import {
  Button, Card, CompassArrow, ConnectionPill, EmptyState, Icon, type IconName, PendingBanner, ScreenHeader, SectionHeader,
  StatTile, StatusChip, TeamBadge, useNow, VictimThumb,
} from '../../ui/components';
import { alpha, colors, font, radii, space, statusColor, type } from '../../ui/theme';

export default function Dashboard() {
  const now = useNow();
  const victims = useStore((s) => s.victims);
  const teamId = useStore((s) => s.teamId);
  const teamName = useStore((s) => s.teamName);
  const callSign = useStore((s) => s.teamCallSign);
  const teamColor = useStore((s) => s.teamColor);
  const mission = useStore((s) => s.missionName);
  const me = useStore((s) => s.me);
  const queued = useStore((s) => s.queued);
  const pendingPoints = useStore((s) => s.pendingPoints);

  const stats = useMemo(() => teamStats(victims, teamId), [victims, teamId]);
  const open = useMemo(() => openByDistance(victims, me), [victims, me]);
  const jobs = useMemo(() => myJobs(victims, teamId).filter((v) => v.status !== 'completed'), [victims, teamId]);
  const feed = useMemo(() => activityFeed(victims, teamId, 6), [victims, teamId]);
  const unseen = useMemo(() => unseenAlerts(victims), [victims]);
  const nearest = open[0];

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScreenHeader
        subtitle={mission ? `Mission · ${mission}` : 'SAR Rescue'}
        title={teamName ? `Team ${teamName}` : 'Dashboard'}
        right={<View style={st.headerRight}>
          <ConnectionPill />
          <TeamBadge color={teamColor} label={callSign ?? teamName?.slice(0, 2) ?? '?'} size={42} />
        </View>}
      />
      <ScrollView contentContainerStyle={st.content}>
        {unseen.length > 0 && (
          <Pressable style={st.alarm} onPress={() => router.push(`/alert/${unseen[0].id}`)}>
            <View style={st.alarmIcon}><Icon name="bell-ring" size={26} color="#fff" /></View>
            <View style={{ flex: 1 }}>
              <Text style={st.alarmTitle}>
                {unseen.length === 1 ? `${unseen[0].display_code} needs a team` : `${unseen.length} victims need a team`}
              </Text>
              <Text style={st.alarmText}>Tap to view the alert</Text>
            </View>
            <Icon name="chevron-right" size={26} color="#fff" />
          </Pressable>
        )}

        <View style={st.grid}>
          <StatTile icon="alert-circle" tone={statusColor.open} value={stats.open} label="Open victims"
                    hint={nearest?.distance != null ? `nearest ${formatDistance(nearest.distance)}` : undefined}
                    onPress={() => router.navigate('/map')} />
          <StatTile icon="run-fast" tone={statusColor.ongoing} value={stats.onTheWay} label="On the way"
                    hint="your team" onPress={() => router.navigate('/jobs')} />
        </View>
        <View style={st.grid}>
          <StatTile icon="check-circle" tone={statusColor.completed} value={stats.rescued} label="Rescued"
                    hint={stats.peopleRescued ? `${stats.peopleRescued} people` : 'by your team'}
                    onPress={() => router.navigate('/jobs')} />
          <StatTile icon={queued || pendingPoints ? 'cloud-upload-outline' : 'check-all'} tone={colors.accent}
                    value={queued} label={queued ? 'Waiting to send' : 'All synced'}
                    hint={pendingPoints ? `${pendingPoints} GPS points queued` : 'actions + GPS'} />
        </View>

        <SectionHeader title="NEAREST OPEN VICTIM" action={open.length > 1 ? `All ${open.length}` : undefined}
                       onAction={() => router.navigate('/map')} />
        {nearest ? <NearestCard v={nearest.v} /> : (
          <Card><EmptyState icon="shield-check-outline" title="No open victims"
                            text="New detections from the drone appear here and ring the siren." /></Card>
        )}

        {jobs.length > 0 && (
          <>
            <SectionHeader title="YOUR ACTIVE JOBS" action="See all" onAction={() => router.navigate('/jobs')} />
            {jobs.slice(0, 3).map((v) => <JobRow key={v.id} v={v} />)}
          </>
        )}

        <SectionHeader title="RECENT ACTIVITY" />
        <Card style={{ paddingVertical: space.s }}>
          {feed.length === 0 ? (
            <Text style={st.dim}>Nothing yet on this mission.</Text>
          ) : feed.map((item, i) => {
            const icon: IconName = item.kind === 'found' ? 'radar' : item.kind === 'accepted' ? 'run-fast' : 'check-circle';
            const tone = item.kind === 'found' ? statusColor.open : item.kind === 'accepted' ? statusColor.ongoing
              : statusColor.completed;
            return (
              <Pressable key={item.key} onPress={() => router.push(`/victim/${item.victim.id}`)}
                         style={[st.feedRow, i < feed.length - 1 && st.feedDivider]}>
                <View style={[st.feedIcon, { backgroundColor: alpha(tone, 0.16) }]}>
                  <Icon name={icon} size={17} color={tone} />
                </View>
                <Text style={st.feedText} numberOfLines={1}>{item.text}</Text>
                <Text style={st.feedTime}>{ageText(item.at, now)}</Text>
              </Pressable>
            );
          })}
        </Card>

        <SectionHeader title="FIELD STATUS" />
        <FieldStatus />
      </ScrollView>
    </View>
  );
}

function NearestCard({ v }: { v: CachedVictim }) {
  const me = useStore((s) => s.me);
  const where = describeFrom(me, v);
  return (
    <Card tone={statusColor.open} onPress={() => router.push(`/victim/${v.id}`)}>
      <View style={st.nearHead}>
        <VictimThumb v={v} size={64} />
        <View style={{ flex: 1, gap: 4 }}>
          <View style={st.row}>
            <Text style={st.code}>{v.display_code}</Text>
            <StatusChip v={v} />
          </View>
          <Text style={st.meta}>
            {v.max_confidence != null ? `${Math.round(v.max_confidence * 100)}% confidence · ` : ''}
            seen {ageText(v.last_seen_at)}
          </Text>
        </View>
      </View>
      <View style={st.nearBody}>
        {where ? <CompassArrow bearing={where.bearing} size={76} /> : (
          <View style={st.noGps}><Icon name="crosshairs-question" size={30} color={colors.muted} /></View>
        )}
        <View style={{ flex: 1 }}>
          <Text style={st.distance}>{where ? formatDistance(where.distance) : 'No GPS fix'}</Text>
          <Text style={st.meta}>{where ? where.text : 'Waiting for your position'}</Text>
          {v.position_error_m ? <Text style={st.meta}>{`position ±${Math.round(v.position_error_m)} m`}</Text> : null}
        </View>
      </View>
      <PendingBanner v={v} />
      <View style={st.row}>
        <Button title="Accept" icon="run-fast" variant="primary" style={{ flex: 1.3 }}
                onPress={() => void engine.accept(v.id)} />
        <Button title="Map" icon="map-marker-radius" style={{ flex: 1 }}
                onPress={() => router.navigate({ pathname: '/map', params: { focus: v.id, at: String(Date.now()) } })} />
      </View>
    </Card>
  );
}

function JobRow({ v }: { v: CachedVictim }) {
  const me = useStore((s) => s.me);
  const where = describeFrom(me, v);
  return (
    <Card onPress={() => router.push(`/victim/${v.id}`)} style={st.jobRow}>
      <VictimThumb v={v} size={48} />
      <View style={{ flex: 1, gap: 3 }}>
        <View style={st.row}>
          <Text style={st.jobCode}>{v.display_code}</Text>
          <StatusChip v={v} />
        </View>
        <Text style={st.meta} numberOfLines={1}>
          {where ? where.text : 'No GPS fix'}{v.accepted_at ? ` · accepted ${ageText(v.accepted_at)}` : ''}
        </Text>
      </View>
      <Icon name="chevron-right" size={24} color={colors.muted} />
    </Card>
  );
}

function FieldStatus() {
  const me = useStore((s) => s.me);
  const wifi = useStore((s) => s.wifiBound);
  const map = useStore((s) => s.styleFile);
  const conn = useStore((s) => s.conn);
  const rows: { icon: IconName; label: string; value: string; ok: boolean }[] = [
    { icon: 'crosshairs-gps', label: 'GPS', ok: !!me && (me.accuracy ?? 99) <= 50,
      value: me ? `±${Math.round(me.accuracy ?? 0)} m · ${ageText(me.ts)}` : 'Searching…' },
    { icon: wifi ? 'wifi' : 'wifi-off', label: 'SAR-NET Wi-Fi', ok: wifi, value: wifi ? 'Bound' : 'Not bound' },
    { icon: 'access-point', label: 'Base link', ok: conn === 'live',
      value: conn === 'live' ? 'Live' : conn === 'connecting' ? 'Reconnecting' : 'Offline' },
    { icon: 'map-check-outline', label: 'Offline map', ok: !!map, value: map ? 'Installed' : 'Missing' },
  ];
  return (
    <Card style={{ gap: 0, paddingVertical: space.s }}>
      {rows.map((r, i) => (
        <View key={r.label} style={[st.statusRow, i < rows.length - 1 && st.feedDivider]}>
          <Icon name={r.icon} size={20} color={r.ok ? colors.ok : colors.warn} />
          <Text style={st.statusLabel}>{r.label}</Text>
          <Text style={[st.statusValue, { color: r.ok ? colors.text : colors.warn }]}>{r.value}</Text>
        </View>
      ))}
    </Card>
  );
}

const st = StyleSheet.create({
  content: { padding: space.l, paddingTop: space.xs, gap: space.m, paddingBottom: space.xxl },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: space.s },
  alarm: { flexDirection: 'row', alignItems: 'center', gap: space.m, backgroundColor: colors.danger,
           borderRadius: radii.l, padding: space.l },
  alarmIcon: { width: 46, height: 46, borderRadius: 23, backgroundColor: 'rgba(255,255,255,0.18)', alignItems: 'center',
               justifyContent: 'center' },
  alarmTitle: { color: '#fff', fontSize: 17, fontWeight: font.heavy },
  alarmText: { color: 'rgba(255,255,255,0.85)', fontSize: 13, marginTop: 2 },
  grid: { flexDirection: 'row', gap: space.m },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.s },
  nearHead: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  nearBody: { flexDirection: 'row', alignItems: 'center', gap: space.l, paddingVertical: space.s },
  noGps: { width: 76, height: 76, borderRadius: 38, backgroundColor: colors.surface2, alignItems: 'center',
           justifyContent: 'center' },
  code: { ...type.h2, color: colors.text },
  distance: { fontSize: 30, fontWeight: font.heavy, color: colors.text, letterSpacing: -0.5 },
  meta: { color: colors.dim, fontSize: 13 },
  dim: { color: colors.dim, fontSize: 14, paddingVertical: space.m },
  jobRow: { flexDirection: 'row', alignItems: 'center', gap: space.m, padding: space.m },
  jobCode: { ...type.h3, color: colors.text },
  feedRow: { flexDirection: 'row', alignItems: 'center', gap: space.m, paddingVertical: 11 },
  feedDivider: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  feedIcon: { width: 32, height: 32, borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  feedText: { color: colors.text, fontSize: 14, fontWeight: font.medium, flex: 1 },
  feedTime: { color: colors.muted, fontSize: 12 },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: space.m, paddingVertical: 12 },
  statusLabel: { color: colors.dim, fontSize: 14, flex: 1 },
  statusValue: { fontSize: 14, fontWeight: font.semibold },
});
