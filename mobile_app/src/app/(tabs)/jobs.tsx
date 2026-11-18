/** My jobs (9.3 #6): this team's ongoing + completed victims. */

import { router } from 'expo-router';
import { useMemo, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { ageText, describeFrom } from '../../core/geo';
import type { CachedVictim } from '../../core/types';
import { myJobs } from '../../state/selectors';
import { useStore } from '../../state/store';
import {
  Button, Card, EmptyState, Icon, PendingBanner, ScreenHeader, StatusChip, useNow, VictimThumb,
} from '../../ui/components';
import { alpha, colors, font, radii, space, type } from '../../ui/theme';

type Filter = 'active' | 'rescued' | 'all';

export default function Jobs() {
  useNow();
  const teamId = useStore((s) => s.teamId);
  const victims = useStore((s) => s.victims);
  const [filter, setFilter] = useState<Filter>('active');
  const all = useMemo(() => myJobs(victims, teamId), [victims, teamId]);
  const active = all.filter((v) => v.status !== 'completed');
  const rescued = all.filter((v) => v.status === 'completed');
  const list = filter === 'active' ? active : filter === 'rescued' ? rescued : all;
  const people = rescued.reduce((n, v) => n + (v.people_count ?? 0), 0);

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScreenHeader subtitle={`${rescued.length} rescued · ${people} people`} title="Jobs" />
      <View style={st.segment}>
        {([['active', `Active (${active.length})`], ['rescued', `Rescued (${rescued.length})`], ['all', 'All']] as const)
          .map(([key, label]) => (
            <Pressable key={key} onPress={() => setFilter(key)} style={[st.segBtn, filter === key && st.segOn]}>
              <Text style={[st.segText, filter === key && st.segTextOn]}>{label}</Text>
            </Pressable>
          ))}
      </View>
      <FlatList
        contentContainerStyle={st.content}
        data={list}
        keyExtractor={(v) => v.id}
        ListEmptyComponent={
          <EmptyState icon={filter === 'rescued' ? 'medical-bag' : 'clipboard-check-outline'}
                      title={filter === 'rescued' ? 'No rescues reported yet' : 'No active jobs'}
                      text="Accept an open victim from the dashboard, the map or an alert.">
            <Button title="Open the map" icon="map-outline" onPress={() => router.navigate('/map')}
                    style={{ marginTop: space.m, alignSelf: 'stretch' }} />
          </EmptyState>
        }
        renderItem={({ item }) => <JobCard v={item} />}
      />
    </View>
  );
}

function JobCard({ v }: { v: CachedVictim }) {
  const me = useStore((s) => s.me);
  const teamId = useStore((s) => s.teamId);
  const where = describeFrom(me, v);
  const ongoing = v.status === 'ongoing' && v.assigned_team_id === teamId && !v.local_pending_action;
  return (
    <Card onPress={() => router.push(`/victim/${v.id}`)}>
      <View style={st.head}>
        <VictimThumb v={v} size={54} />
        <View style={{ flex: 1, gap: 4 }}>
          <View style={st.row}>
            <Text style={st.code}>{v.display_code}</Text>
            <StatusChip v={v} />
          </View>
          <Text style={st.meta}>
            {v.status === 'completed' && v.completed_at ? `Rescued ${ageText(v.completed_at)}`
              : v.accepted_at ? `Accepted ${ageText(v.accepted_at)}` : 'Waiting for the base'}
            {v.people_count != null ? ` · ${v.people_count} ${v.people_count === 1 ? 'person' : 'people'}` : ''}
          </Text>
        </View>
        <Icon name="chevron-right" size={24} color={colors.muted} />
      </View>
      {v.status !== 'completed' && (
        <View style={st.where}>
          <Icon name="navigation-variant-outline" size={18} color={colors.accent} />
          <Text style={st.whereText}>{where ? where.text : 'No GPS fix yet'}</Text>
        </View>
      )}
      <PendingBanner v={v} />
      {ongoing && (
        <View style={st.row}>
          <Button title="Navigate" icon="map-marker-radius" size="s" style={{ flex: 1 }}
                  onPress={() => router.navigate({ pathname: '/map', params: { focus: v.id, at: String(Date.now()) } })} />
          <Button title="Mark rescued" icon="check-circle" variant="success" size="s" style={{ flex: 1 }}
                  onPress={() => router.push(`/complete/${v.id}`)} />
        </View>
      )}
    </Card>
  );
}

const st = StyleSheet.create({
  content: { padding: space.l, paddingTop: space.s, gap: space.m, paddingBottom: space.xxl, flexGrow: 1 },
  segment: { flexDirection: 'row', marginHorizontal: space.l, backgroundColor: colors.surface, borderRadius: radii.m,
             padding: 4, borderWidth: 1, borderColor: colors.border },
  segBtn: { flex: 1, paddingVertical: 10, borderRadius: radii.s, alignItems: 'center' },
  segOn: { backgroundColor: alpha(colors.accent, 0.2) },
  segText: { color: colors.dim, fontWeight: font.semibold, fontSize: 14 },
  segTextOn: { color: colors.text, fontWeight: font.bold },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.s },
  code: { ...type.h3, color: colors.text, fontSize: 19 },
  meta: { color: colors.dim, fontSize: 13 },
  where: { flexDirection: 'row', alignItems: 'center', gap: space.s, backgroundColor: colors.surface2,
           borderRadius: radii.s, paddingHorizontal: space.m, paddingVertical: 10 },
  whereText: { color: colors.text, fontWeight: font.semibold, fontSize: 15 },
});
