/** Complete form (9.3 #5): people count + note. Queued if out of range. */

import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { engine } from '../../engine/engine';
import { useStore, useVictim } from '../../state/store';
import { Button, Card, EmptyState, Icon } from '../../ui/components';
import { alpha, colors, font, radii, space, type } from '../../ui/theme';

const QUICK_NOTES = ['Conscious', 'Injured', 'Needs medical care', 'Taken to the base', 'Handed to ambulance'];

export default function CompleteForm() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const v = useVictim(id);
  const conn = useStore((s) => s.conn);
  const insets = useSafeAreaInsets();
  const [people, setPeople] = useState(1);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  if (!v) {
    return <View style={st.missing}><EmptyState icon="account-off-outline" title="Victim not found" /></View>;
  }

  async function submit() {
    setBusy(true);
    await engine.complete(v!.id, people, note.trim() || null);
    setBusy(false);
    if (router.canDismiss()) router.dismissAll();
    router.navigate('/jobs');
  }

  function addNote(text: string) {
    setNote((n) => (n.includes(text) ? n : n.trim() ? `${n.trim()}, ${text.toLowerCase()}` : text));
  }

  return (
    <ScrollView style={{ flex: 1, backgroundColor: colors.bg }}
                contentContainerStyle={[st.page, { paddingBottom: insets.bottom + space.xl }]}
                keyboardShouldPersistTaps="handled">
      <View style={st.hero}>
        <View style={st.heroIcon}><Icon name="medical-bag" size={30} color={colors.ok} /></View>
        <Text style={st.h1}>{v.display_code} rescued</Text>
        <Text style={st.dim}>Report what you found so the coordinator can close it.</Text>
      </View>

      <Card style={{ alignItems: 'center', gap: space.m }}>
        <Text style={st.label}>PEOPLE RESCUED AT THIS SPOT</Text>
        <View style={st.stepper}>
          <Pressable style={st.stepBtn} onPress={() => setPeople(Math.max(0, people - 1))} accessibilityLabel="One less">
            <Icon name="minus" size={30} color={colors.text} />
          </Pressable>
          <Text style={st.count}>{people}</Text>
          <Pressable style={st.stepBtn} onPress={() => setPeople(Math.min(500, people + 1))} accessibilityLabel="One more">
            <Icon name="plus" size={30} color={colors.text} />
          </Pressable>
        </View>
      </Card>

      <Text style={st.label}>NOTE FOR THE COORDINATOR</Text>
      <View style={st.quick}>
        {QUICK_NOTES.map((q) => (
          <Pressable key={q} style={st.quickChip} onPress={() => addNote(q)}>
            <Text style={st.quickText}>{q}</Text>
          </Pressable>
        ))}
      </View>
      <TextInput style={st.input} value={note} onChangeText={setNote} multiline
                 placeholder="Condition, where taken, needs…" placeholderTextColor={colors.muted} />

      {conn !== 'live' && (
        <View style={st.warn}>
          <Icon name="cloud-upload-outline" size={20} color={colors.warn} />
          <Text style={st.warnText}>You are offline. The report is saved and sent when the base is in range.</Text>
        </View>
      )}
      <Button title="Send rescue report" icon="check-circle" variant="success" size="l" busy={busy}
              onPress={() => void submit()} />
    </ScrollView>
  );
}

const st = StyleSheet.create({
  page: { padding: space.l, gap: space.m },
  missing: { flex: 1, backgroundColor: colors.bg, justifyContent: 'center' },
  hero: { alignItems: 'center', gap: space.s, paddingVertical: space.m },
  heroIcon: { width: 64, height: 64, borderRadius: 32, backgroundColor: alpha(colors.ok, 0.15), alignItems: 'center',
              justifyContent: 'center' },
  h1: { ...type.h1, color: colors.text },
  dim: { color: colors.dim, fontSize: 14, textAlign: 'center' },
  label: { ...type.overline, color: colors.muted, marginTop: space.s },
  stepper: { flexDirection: 'row', alignItems: 'center', gap: space.xl },
  stepBtn: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.surface2, borderWidth: 1,
             borderColor: colors.border, alignItems: 'center', justifyContent: 'center' },
  count: { color: colors.text, fontSize: 52, fontWeight: font.black, minWidth: 80, textAlign: 'center' },
  quick: { flexDirection: 'row', flexWrap: 'wrap', gap: space.s },
  quickChip: { backgroundColor: colors.surface2, borderRadius: radii.pill, borderWidth: 1, borderColor: colors.border,
               paddingHorizontal: 12, paddingVertical: 8 },
  quickText: { color: colors.text, fontSize: 13, fontWeight: font.semibold },
  input: { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border, borderWidth: 1,
           borderRadius: radii.m, padding: space.m, minHeight: 110, fontSize: 16, textAlignVertical: 'top' },
  warn: { flexDirection: 'row', alignItems: 'center', gap: space.s, backgroundColor: alpha(colors.warn, 0.12),
          borderRadius: radii.m, padding: space.m, borderWidth: 1, borderColor: alpha(colors.warn, 0.4) },
  warnText: { color: '#FCD34D', flex: 1, fontWeight: font.medium },
});
