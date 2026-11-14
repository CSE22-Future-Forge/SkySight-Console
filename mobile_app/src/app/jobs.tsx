/** My jobs (9.3 #6): this team's ongoing + completed victims. */

import { router } from 'expo-router';
import { FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import { ageText, describeFrom } from '../core/geo';
import { useStore } from '../state/store';
import { PendingBanner, StatusChip } from '../ui/components';
import { colors, radius, space } from '../ui/theme';

export default function Jobs() {
  const teamId = useStore((s) => s.teamId);
  const me = useStore((s) => s.me);
  const all = useStore((s) => s.victims);
  const jobs = all
    .filter((v) => v.assigned_team_id === teamId || v.local_pending_action === 'accept')
    .sort((a, b) => (a.status === 'ongoing' ? 0 : 1) - (b.status === 'ongoing' ? 0 : 1) || b.first_detected_at - a.first_detected_at);

  return (
    <FlatList
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: space.l, gap: space.m }}
      data={jobs}
      keyExtractor={(v) => v.id}
      ListEmptyComponent={<Text style={st.dim}>No jobs yet. Accept an open victim from the map or an alert.</Text>}
      renderItem={({ item: v }) => {
        const where = describeFrom(me, v);
        return (
          <Pressable style={st.row} onPress={() => router.push(`/victim/${v.id}`)}>
            <View style={st.head}>
              <Text style={st.code}>{v.display_code}</Text>
              <StatusChip v={v} />
            </View>
            <Text style={st.text}>{where ? where.text : 'No GPS yet'}</Text>
            <Text style={st.dim}>
              {v.status === 'completed' && v.completed_at ? `rescued ${ageText(v.completed_at)}`
                : v.accepted_at ? `accepted ${ageText(v.accepted_at)}` : ''}
              {v.people_count != null ? ` · ${v.people_count} people` : ''}
            </Text>
            <PendingBanner v={v} />
          </Pressable>
        );
      }}
    />
  );
}

const st = StyleSheet.create({
  row: { backgroundColor: colors.panel, borderRadius: radius, padding: space.l, gap: 6, borderWidth: 1,
         borderColor: colors.border },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  code: { color: colors.text, fontSize: 22, fontWeight: '900' },
  text: { color: colors.text, fontSize: 16, fontWeight: '600' },
  dim: { color: colors.dim, fontSize: 14 },
});
