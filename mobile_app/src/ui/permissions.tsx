/** What the field phone must allow for alerts to reach it (9.4); used at pairing and on the Team tab. */

import * as Location from 'expo-location';
import { useCallback, useEffect, useState } from 'react';
import { AppState, PermissionsAndroid, Platform, StyleSheet, Text, View } from 'react-native';

import SarNative from '../../modules/sar-native';
import { Button, Icon, type IconName } from './components';
import { alpha, colors, font, radii, space } from './theme';

type Key = 'location' | 'notifications' | 'fullScreen' | 'battery';

const ITEMS: { key: Key; icon: IconName; title: string; why: string; fix: () => Promise<unknown> | void }[] = [
  { key: 'notifications', icon: 'bell-ring-outline', title: 'Notifications', why: 'Siren alarm for new victims.',
    fix: () => PermissionsAndroid.request('android.permission.POST_NOTIFICATIONS' as any) },
  { key: 'fullScreen', icon: 'cellphone-wireless', title: 'Full-screen alerts',
    why: 'Shows the alarm over the lock screen.', fix: () => SarNative.openFullScreenIntentSettings() },
  { key: 'battery', icon: 'battery-charging-high', title: 'No battery optimisation',
    why: 'Keeps the link to the base open with the screen off.', fix: () => SarNative.requestIgnoreBatteryOptimizations() },
  { key: 'location', icon: 'crosshairs-gps', title: 'Location',
    why: 'Shares your position with the base, so the drone does not flag your team.',
    fix: () => Location.requestForegroundPermissionsAsync() },
];

export async function readPermissions(): Promise<Record<Key, boolean>> {
  const loc = await Location.getForegroundPermissionsAsync();
  const notifications = Platform.OS === 'android' && (Platform.Version as number) >= 33
    ? await PermissionsAndroid.check('android.permission.POST_NOTIFICATIONS' as any) : true;
  return {
    location: loc.granted,
    notifications,
    fullScreen: SarNative.canUseFullScreenIntent(),
    battery: SarNative.isIgnoringBatteryOptimizations(),
  };
}

export function PermissionChecklist({ onChange }: { onChange?: (allOk: boolean) => void }) {
  const [state, setState] = useState<Record<Key, boolean> | null>(null);

  const check = useCallback(async () => {
    const next = await readPermissions();
    setState(next);
    onChange?.(Object.values(next).every(Boolean));
  }, [onChange]);

  useEffect(() => {
    void check();
    // Settings screens are separate activities: re-check when the user comes back.
    const sub = AppState.addEventListener('change', (s) => s === 'active' && void check());
    return () => sub.remove();
  }, [check]);

  return (
    <View style={{ gap: space.s }}>
      {ITEMS.map((it) => {
        const ok = state?.[it.key] ?? false;
        return (
          <View key={it.key} style={[st.row, ok && st.rowOk]}>
            <View style={[st.icon, { backgroundColor: alpha(ok ? colors.ok : colors.warn, 0.15) }]}>
              <Icon name={ok ? 'check' : it.icon} size={20} color={ok ? colors.ok : colors.warn} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={st.title}>{it.title}</Text>
              <Text style={st.why}>{it.why}</Text>
            </View>
            {!ok && state && (
              <Button title="Allow" size="s" variant="primary"
                      onPress={async () => { await it.fix(); setTimeout(() => void check(), 800); }} />
            )}
          </View>
        );
      })}
    </View>
  );
}

const st = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: space.m, backgroundColor: colors.surface,
         borderRadius: radii.m, padding: space.m, borderWidth: 1, borderColor: colors.border },
  rowOk: { borderColor: alpha(colors.ok, 0.3) },
  icon: { width: 40, height: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
  title: { color: colors.text, fontSize: 15, fontWeight: font.bold },
  why: { color: colors.dim, fontSize: 13, marginTop: 2 },
});
