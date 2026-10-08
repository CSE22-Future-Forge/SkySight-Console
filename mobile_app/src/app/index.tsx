import { Redirect } from 'expo-router';
import { ActivityIndicator, Text, View } from 'react-native';

import { useStore } from '../state/store';
import { colors } from '../ui/theme';

export default function Index() {
  const paired = useStore((s) => s.paired);
  if (paired === null) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.bg }}>
        <ActivityIndicator color={colors.accent} size="large" />
        <Text style={{ color: colors.dim, marginTop: 12 }}>SAR Rescue</Text>
      </View>
    );
  }
  return <Redirect href={paired ? '/map' : '/pairing'} />;
}
