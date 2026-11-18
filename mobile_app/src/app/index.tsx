import { Redirect } from 'expo-router';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { useStore } from '../state/store';
import { Icon } from '../ui/components';
import { alpha, colors, font, space } from '../ui/theme';

export default function Index() {
  const paired = useStore((s) => s.paired);
  if (paired === null) {
    return (
      <View style={st.page}>
        <View style={st.logo}><Icon name="radar" size={44} color={colors.accent} /></View>
        <Text style={st.title}>SAR Rescue</Text>
        <Text style={st.sub}>Starting the field link…</Text>
        <ActivityIndicator color={colors.accent} style={{ marginTop: space.l }} />
      </View>
    );
  }
  return <Redirect href={paired ? '/home' : '/pairing'} />;
}

const st = StyleSheet.create({
  page: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.bg, gap: space.s },
  logo: { width: 88, height: 88, borderRadius: 28, backgroundColor: alpha(colors.accent, 0.14), borderWidth: 1,
          borderColor: alpha(colors.accent, 0.4), alignItems: 'center', justifyContent: 'center', marginBottom: space.m },
  title: { color: colors.text, fontSize: 26, fontWeight: font.heavy },
  sub: { color: colors.dim, fontSize: 15 },
});
