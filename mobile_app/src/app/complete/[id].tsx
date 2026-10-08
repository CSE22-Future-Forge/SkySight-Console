/** Complete form (9.3 #5): people count + note. Queued if out of range. */

import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { engine } from '../../engine/engine';
import { useStore, useVictim } from '../../state/store';
import { Button } from '../../ui/components';
import { colors, radius, space } from '../../ui/theme';

export default function CompleteForm() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const v = useVictim(id);
  const conn = useStore((s) => s.conn);
  const [people, setPeople] = useState(1);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  if (!v) return <View style={st.page}><Text style={st.dim}>Victim not found.</Text></View>;

  async function submit() {
    setBusy(true);
    await engine.complete(v!.id, people, note.trim() || null);
    setBusy(false);
    router.dismissTo('/map');
  }

  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.bg }} contentContainerStyle={st.page}>
      <Text style={st.h1}>{v.display_code} rescued</Text>
      <Text style={st.label}>People rescued at this spot</Text>
      <View style={st.stepper}>
        <Button title={'−'} style={st.stepBtn} onPress={() => setPeople(Math.max(0, people - 1))} />
        <Text style={st.count}>{people}</Text>
        <Button title="+" style={st.stepBtn} onPress={() => setPeople(Math.min(500, people + 1))} />
      </View>
      <Text style={st.label}>Note for the coordinator</Text>
      <TextInput style={st.input} value={note} onChangeText={setNote} multiline
                 placeholder="Condition, where taken, needs..." placeholderTextColor={colors.muted} />
      {conn !== 'live' && (
        <Text style={st.warn}>You are offline. The report is saved and sent automatically when the base is in range.</Text>
      )}
      <Button title="Send rescue report" variant="ok" busy={busy} onPress={() => void submit()} />
    </ScrollView>
  );
}

const st = StyleSheet.create({
  page: { padding: space.l, gap: space.m, backgroundColor: colors.bg },
  h1: { color: colors.text, fontSize: 24, fontWeight: '800' },
  label: { color: colors.dim, fontSize: 14, marginTop: space.s },
  dim: { color: colors.dim },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: space.l },
  stepBtn: { width: 72 },
  count: { color: colors.text, fontSize: 40, fontWeight: '900', minWidth: 60, textAlign: 'center' },
  input: { backgroundColor: colors.panel2, color: colors.text, borderColor: colors.border, borderWidth: 1,
           borderRadius: radius, padding: space.m, minHeight: 110, fontSize: 16, textAlignVertical: 'top' },
  warn: { color: '#ffe0a3', backgroundColor: '#5a4300', borderRadius: radius, padding: space.m },
});
