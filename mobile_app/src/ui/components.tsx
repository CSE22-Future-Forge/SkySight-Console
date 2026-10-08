import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native';

import { relativeBearing } from '../core/geo';
import type { CachedVictim } from '../core/types';
import { useStore } from '../state/store';
import { colors, pendingLabel, radius, space, statusColor, statusLabel, touch } from './theme';

export function Button({ title, onPress, variant = 'default', disabled, busy, style }: {
  title: string;
  onPress: () => void;
  variant?: 'default' | 'primary' | 'danger' | 'ok';
  disabled?: boolean;
  busy?: boolean;
  style?: ViewStyle;
}) {
  const bg = { default: colors.panel2, primary: colors.accent, danger: '#7a1f22', ok: '#1f5a27' }[variant];
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [s.button, { backgroundColor: bg, opacity: disabled ? 0.45 : pressed ? 0.75 : 1 }, style]}>
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={s.buttonText}>{title}</Text>}
    </Pressable>
  );
}

/** Green Live / amber Reconnecting / red "Offline - N queued" (9.3). */
export function ConnectionPill() {
  const conn = useStore((st) => st.conn);
  const queued = useStore((st) => st.queued);
  const { color, text } =
    conn === 'live' ? { color: colors.ok, text: queued ? `Live - sending ${queued}` : 'Live' }
      : conn === 'connecting' ? { color: colors.warn, text: 'Reconnecting' }
        : conn === 'revoked' ? { color: colors.danger, text: 'Removed by base' }
          : { color: colors.danger, text: `Offline - ${queued} queued` };
  return (
    <View style={[s.pill, { borderColor: color, backgroundColor: color + '33' }]}>
      <View style={[s.dot, { backgroundColor: color }]} />
      <Text style={[s.pillText, { color }]}>{text}</Text>
    </View>
  );
}

export function StatusChip({ v }: { v: CachedVictim }) {
  const color = statusColor[v.status] ?? colors.dim;
  return (
    <View style={[s.chip, { borderColor: color, backgroundColor: color + '33' }]}>
      <Text style={[s.chipText, { color }]}>{statusLabel[v.status] ?? v.status}</Text>
    </View>
  );
}

export function PendingBanner({ v }: { v: CachedVictim }) {
  if (!v.local_pending_action) return null;
  return (
    <View style={s.pending}>
      <Text style={s.pendingText}>{pendingLabel[v.local_pending_action]}</Text>
    </View>
  );
}

/** Arrow pointing at the target, corrected for the compass heading when available. */
export function CompassArrow({ bearing, size = 96 }: { bearing: number; size?: number }) {
  const heading = useStore((st) => st.compass);
  const rot = relativeBearing(bearing, heading);
  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <View style={[s.compassRing, { width: size, height: size, borderRadius: size / 2 }]} />
      <View style={{ transform: [{ rotate: `${rot}deg` }] }}>
        <View style={[s.arrowHead, { borderLeftWidth: size / 6, borderRightWidth: size / 6, borderBottomWidth: size / 2.4 }]} />
        <View style={[s.arrowTail, { width: size / 12, height: size / 5 }]} />
      </View>
      {heading == null && <Text style={s.compassHint}>bearing from north</Text>}
    </View>
  );
}

/** Snackbar for store.toast messages. */
export function Toast() {
  const toast = useStore((st) => st.toast);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!toast) return;
    setVisible(true);
    const t = setTimeout(() => setVisible(false), 6000);
    return () => clearTimeout(t);
  }, [toast]);
  if (!visible || !toast) return null;
  return (
    <Pressable style={s.toast} onPress={() => setVisible(false)}>
      <Text style={s.toastText}>{toast.text}</Text>
    </Pressable>
  );
}

export const s = StyleSheet.create({
  button: { minHeight: touch, borderRadius: radius, paddingHorizontal: space.l, alignItems: 'center',
            justifyContent: 'center', borderWidth: 1, borderColor: colors.border },
  buttonText: { color: '#fff', fontSize: 17, fontWeight: '700', letterSpacing: 0.3 },
  pill: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderRadius: 16, paddingHorizontal: 10,
          paddingVertical: 5, alignSelf: 'flex-start' },
  dot: { width: 9, height: 9, borderRadius: 5, marginRight: 6 },
  pillText: { fontWeight: '700', fontSize: 13 },
  chip: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 9, paddingVertical: 2, alignSelf: 'flex-start' },
  chipText: { fontWeight: '800', fontSize: 12, letterSpacing: 0.5 },
  pending: { backgroundColor: '#5a4300', borderColor: colors.warn, borderWidth: 1, borderRadius: radius, padding: space.m },
  pendingText: { color: '#ffe0a3', fontWeight: '700' },
  compassRing: { position: 'absolute', borderWidth: 2, borderColor: colors.border, backgroundColor: colors.panel },
  arrowHead: { width: 0, height: 0, borderLeftColor: 'transparent', borderRightColor: 'transparent',
               borderBottomColor: colors.accent, alignSelf: 'center' },
  arrowTail: { backgroundColor: colors.accent, alignSelf: 'center' },
  compassHint: { position: 'absolute', bottom: -18, color: colors.muted, fontSize: 10 },
  toast: { position: 'absolute', left: space.l, right: space.l, bottom: space.xl * 2, backgroundColor: '#263449',
           borderColor: colors.border, borderWidth: 1, borderRadius: radius, padding: space.l, elevation: 6 },
  toastText: { color: colors.text, fontSize: 15 },
});
