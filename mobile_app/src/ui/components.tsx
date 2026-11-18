/** Shared UI kit: one look for every screen (theme.ts holds the tokens). */

import MaterialCommunityIcons from '@expo/vector-icons/MaterialCommunityIcons';
import { type ComponentProps, type ReactNode, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, Animated, Easing, Image, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { relativeBearing } from '../core/geo';
import type { CachedVictim } from '../core/types';
import { useStore } from '../state/store';
import {
  alpha, colors, font, pendingLabel, radii, shadow, space, statusColor, statusIcon, statusLabel, touch, type,
} from './theme';

export type IconName = ComponentProps<typeof MaterialCommunityIcons>['name'];

export function Icon({ name, size = 22, color = colors.text }: {
  name: IconName; size?: number; color?: ComponentProps<typeof MaterialCommunityIcons>['color'];
}) {
  return <MaterialCommunityIcons name={name} size={size} color={color} />;
}

/** Re-render every ``everyMs`` so "2 min ago" texts stay true. */
export function useNow(everyMs = 15_000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

// ------------------------------------------------------------------ buttons ---
type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'default' | 'ok';

const VARIANTS: Record<Variant, { bg: string; fg: string; border: string }> = {
  primary: { bg: colors.accent, fg: '#fff', border: colors.accent },
  secondary: { bg: colors.surface2, fg: colors.text, border: colors.border },
  default: { bg: colors.surface2, fg: colors.text, border: colors.border },
  ghost: { bg: 'transparent', fg: colors.dim, border: colors.border },
  danger: { bg: alpha(colors.danger, 0.14), fg: '#FCA5A5', border: alpha(colors.danger, 0.5) },
  success: { bg: colors.ok, fg: '#04210F', border: colors.ok },
  ok: { bg: colors.ok, fg: '#04210F', border: colors.ok },
};

export function Button({ title, onPress, variant = 'secondary', icon, disabled, busy, style, size = 'm' }: {
  title: string;
  onPress: () => void;
  variant?: Variant;
  icon?: IconName;
  disabled?: boolean;
  busy?: boolean;
  style?: StyleProp<ViewStyle>;
  size?: 's' | 'm' | 'l';
}) {
  const v = VARIANTS[variant];
  const height = size === 's' ? 40 : size === 'l' ? 64 : touch;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!(disabled || busy), busy: !!busy }}
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [
        s.button,
        { minHeight: height, backgroundColor: v.bg, borderColor: v.border, opacity: disabled ? 0.45 : 1,
          transform: [{ scale: pressed ? 0.98 : 1 }] },
        pressed && { backgroundColor: alpha(v.bg === 'transparent' ? colors.surface3 : v.bg, 0.85) },
        style,
      ]}>
      {busy ? <ActivityIndicator color={v.fg} /> : (
        <View style={s.buttonInner}>
          {icon && <Icon name={icon} size={size === 's' ? 18 : 21} color={v.fg} />}
          <Text style={[s.buttonText, { color: v.fg, fontSize: size === 's' ? 14 : size === 'l' ? 18 : 16 }]}
                numberOfLines={1}>{title}</Text>
        </View>
      )}
    </Pressable>
  );
}

export function IconButton({ icon, onPress, color = colors.text, active, label, style }: {
  icon: IconName; onPress: () => void; color?: string; active?: boolean; label: string; style?: StyleProp<ViewStyle>;
}) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress}
               style={({ pressed }) => [s.iconButton, active && s.iconButtonActive, pressed && { opacity: 0.7 }, style]}>
      <Icon name={icon} size={24} color={active ? colors.accent : color} />
    </Pressable>
  );
}

// -------------------------------------------------------------- containers ---
export function Card({ children, style, onPress, tone }: {
  children: ReactNode; style?: StyleProp<ViewStyle>; onPress?: () => void; tone?: string;
}) {
  const toneStyle = tone ? { borderColor: alpha(tone, 0.45), backgroundColor: alpha(tone, 0.08) } : null;
  if (!onPress) return <View style={[s.card, toneStyle, style]}>{children}</View>;
  return (
    <Pressable onPress={onPress}
               style={({ pressed }) => [s.card, toneStyle, pressed && { backgroundColor: colors.surface2 }, style]}>
      {children}
    </Pressable>
  );
}

export function SectionHeader({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) {
  return (
    <View style={s.sectionHeader}>
      <Text style={s.sectionTitle}>{title}</Text>
      {action && onAction && (
        <Pressable onPress={onAction} hitSlop={10}>
          <Text style={s.sectionAction}>{action}</Text>
        </Pressable>
      )}
    </View>
  );
}

/** Big screen title for the tab screens. */
export function ScreenHeader({ title, subtitle, right }: { title: string; subtitle?: string | null; right?: ReactNode }) {
  const insets = useSafeAreaInsets();
  return (
    <View style={[s.screenHeader, { paddingTop: insets.top + space.m }]}>
      <View style={{ flex: 1 }}>
        {subtitle ? <Text style={s.screenSubtitle} numberOfLines={1}>{subtitle}</Text> : null}
        <Text style={s.screenTitle} numberOfLines={1}>{title}</Text>
      </View>
      {right}
    </View>
  );
}

export function StatTile({ icon, value, label, tone, hint, onPress }: {
  icon: IconName; value: string | number; label: string; tone: string; hint?: string; onPress?: () => void;
}) {
  return (
    <Pressable onPress={onPress} disabled={!onPress}
               style={({ pressed }) => [s.stat, pressed && { backgroundColor: colors.surface2 }]}>
      <View style={[s.statIcon, { backgroundColor: alpha(tone, 0.16) }]}>
        <Icon name={icon} size={20} color={tone} />
      </View>
      <Text style={s.statValue}>{value}</Text>
      <Text style={s.statLabel} numberOfLines={1}>{label}</Text>
      {hint ? <Text style={s.statHint} numberOfLines={1}>{hint}</Text> : null}
    </Pressable>
  );
}

export function EmptyState({ icon, title, text, children }: { icon: IconName; title: string; text?: string; children?: ReactNode }) {
  return (
    <View style={s.empty}>
      <View style={s.emptyIcon}><Icon name={icon} size={30} color={colors.dim} /></View>
      <Text style={s.emptyTitle}>{title}</Text>
      {text ? <Text style={s.emptyText}>{text}</Text> : null}
      {children}
    </View>
  );
}

export function ProgressBar({ fraction, tone = colors.accent }: { fraction: number; tone?: string }) {
  return (
    <View style={s.bar}>
      <View style={[s.barFill, { width: `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`, backgroundColor: tone }]} />
    </View>
  );
}

export function Field({ label, value, icon }: { label: string; value: string; icon?: IconName }) {
  return (
    <View style={s.field}>
      <View style={s.fieldLabelRow}>
        {icon && <Icon name={icon} size={16} color={colors.muted} />}
        <Text style={s.fieldLabel}>{label}</Text>
      </View>
      <Text style={s.fieldValue} selectable numberOfLines={2}>{value}</Text>
    </View>
  );
}

// ------------------------------------------------------------------ status ---
/** Green Live / amber Reconnecting / red "Offline - N queued" (9.3). */
export function ConnectionPill({ compact }: { compact?: boolean }) {
  const conn = useStore((st) => st.conn);
  const queued = useStore((st) => st.queued);
  const { color, text, icon } =
    conn === 'live' ? { color: colors.ok, text: queued ? `Live · sending ${queued}` : 'Live', icon: 'access-point' as IconName }
      : conn === 'connecting' ? { color: colors.warn, text: 'Reconnecting', icon: 'sync' as IconName }
        : conn === 'revoked' ? { color: colors.danger, text: 'Removed by base', icon: 'access-point-off' as IconName }
          : { color: colors.danger, text: queued ? `Offline · ${queued} queued` : 'Offline', icon: 'access-point-off' as IconName };
  return (
    <View style={[s.pill, { borderColor: alpha(color, 0.55), backgroundColor: alpha(color, 0.14) }]}>
      {conn === 'live' ? <PulseDot color={color} /> : <Icon name={icon} size={15} color={color} />}
      {!compact && <Text style={[s.pillText, { color }]}>{text}</Text>}
    </View>
  );
}

export function PulseDot({ color, size = 9 }: { color: string; size?: number }) {
  const anim = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const loop = Animated.loop(Animated.timing(anim, { toValue: 1, duration: 1600, easing: Easing.out(Easing.quad),
                                                       useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [anim]);
  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View style={{
        position: 'absolute', width: size, height: size, borderRadius: size, backgroundColor: color,
        opacity: anim.interpolate({ inputRange: [0, 1], outputRange: [0.6, 0] }),
        transform: [{ scale: anim.interpolate({ inputRange: [0, 1], outputRange: [1, 2.6] }) }],
      }} />
      <View style={{ width: size, height: size, borderRadius: size, backgroundColor: color }} />
    </View>
  );
}

export function StatusChip({ v, status }: { v?: CachedVictim; status?: string }) {
  const st = status ?? v?.status ?? 'open';
  const color = statusColor[st] ?? colors.dim;
  return (
    <View style={[s.chip, { borderColor: alpha(color, 0.55), backgroundColor: alpha(color, 0.15) }]}>
      <Icon name={(statusIcon[st] ?? 'circle') as IconName} size={13} color={color} />
      <Text style={[s.chipText, { color }]}>{statusLabel[st] ?? st}</Text>
    </View>
  );
}

export function PendingBanner({ v }: { v: CachedVictim }) {
  if (!v.local_pending_action) return null;
  return (
    <View style={s.pending}>
      <Icon name="progress-clock" size={18} color={colors.warn} />
      <Text style={s.pendingText}>{pendingLabel[v.local_pending_action]}</Text>
    </View>
  );
}

export function TeamBadge({ color, label, size = 40 }: { color: string; label: string; size?: number }) {
  return (
    <View style={[s.teamBadge, { width: size, height: size, borderRadius: size / 2, backgroundColor: alpha(color, 0.2),
                                 borderColor: color }]}>
      <Text style={[s.teamBadgeText, { color, fontSize: size * 0.36 }]} numberOfLines={1}>{label}</Text>
    </View>
  );
}

/** Victim thumbnail or a status-coloured placeholder. */
export function VictimThumb({ v, size = 56 }: { v: CachedVictim; size?: number }) {
  const color = statusColor[v.status] ?? colors.dim;
  if (v.thumb_local_path) {
    return <Image source={{ uri: v.thumb_local_path }} style={[s.thumb, { width: size, height: size, borderColor: color }]} />;
  }
  return (
    <View style={[s.thumb, s.thumbEmpty, { width: size, height: size, borderColor: alpha(color, 0.6),
                                           backgroundColor: alpha(color, 0.12) }]}>
      <Icon name="human-handsup" size={size * 0.48} color={color} />
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
      <View style={[s.compassInner, { width: size * 0.72, height: size * 0.72, borderRadius: size }]} />
      {heading != null && <Text style={[s.compassN, { top: 2 }]}>N</Text>}
      <View style={{ transform: [{ rotate: `${rot}deg` }] }}>
        <Icon name="navigation" size={size * 0.5} color={colors.accent} />
      </View>
      {heading == null && <Text style={s.compassHint}>from north</Text>}
    </View>
  );
}

/** Snackbar for store.toast messages. */
export function Toast() {
  const toast = useStore((st) => st.toast);
  const insets = useSafeAreaInsets();
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!toast) return;
    setVisible(true);
    const t = setTimeout(() => setVisible(false), 5000);
    return () => clearTimeout(t);
  }, [toast]);
  if (!visible || !toast) return null;
  return (
    <Pressable style={[s.toast, { bottom: insets.bottom + 92 }]} onPress={() => setVisible(false)}>
      <Icon name="information-outline" size={20} color={colors.accent} />
      <Text style={s.toastText}>{toast.text}</Text>
    </Pressable>
  );
}

export const s = StyleSheet.create({
  button: { borderRadius: radii.m, paddingHorizontal: space.l, alignItems: 'center', justifyContent: 'center',
            borderWidth: 1 },
  buttonInner: { flexDirection: 'row', alignItems: 'center', gap: space.s },
  buttonText: { fontWeight: font.bold, letterSpacing: 0.2 },
  iconButton: { width: 50, height: 50, borderRadius: 25, backgroundColor: colors.surface, borderWidth: 1,
                borderColor: colors.border, alignItems: 'center', justifyContent: 'center', ...shadow.float },
  iconButtonActive: { borderColor: colors.accent, backgroundColor: alpha(colors.accent, 0.16) },
  card: { backgroundColor: colors.surface, borderRadius: radii.l, padding: space.l, borderWidth: 1,
          borderColor: colors.border, gap: space.s },
  sectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
                   marginTop: space.s, marginBottom: -space.xs },
  sectionTitle: { ...type.overline, color: colors.muted },
  sectionAction: { color: colors.accent, fontWeight: font.bold, fontSize: 14 },
  screenHeader: { flexDirection: 'row', alignItems: 'flex-end', gap: space.m, paddingHorizontal: space.l,
                  paddingBottom: space.m, backgroundColor: colors.bg },
  screenTitle: { ...type.h1, color: colors.text },
  screenSubtitle: { ...type.small, color: colors.dim, marginBottom: 2 },
  stat: { flex: 1, backgroundColor: colors.surface, borderRadius: radii.l, padding: space.m, borderWidth: 1,
          borderColor: colors.border, gap: 2, minHeight: 112 },
  statIcon: { width: 36, height: 36, borderRadius: 12, alignItems: 'center', justifyContent: 'center',
              marginBottom: space.s },
  statValue: { color: colors.text, fontSize: 28, fontWeight: font.heavy, letterSpacing: -0.5 },
  statLabel: { color: colors.dim, fontSize: 13, fontWeight: font.semibold },
  statHint: { color: colors.muted, fontSize: 11 },
  empty: { alignItems: 'center', paddingVertical: space.xl, paddingHorizontal: space.l, gap: space.s },
  emptyIcon: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.surface2, alignItems: 'center',
               justifyContent: 'center', marginBottom: space.s },
  emptyTitle: { ...type.h3, color: colors.text, textAlign: 'center' },
  emptyText: { ...type.body, color: colors.dim, textAlign: 'center' },
  bar: { height: 8, backgroundColor: colors.surface3, borderRadius: 4, overflow: 'hidden' },
  barFill: { height: 8, borderRadius: 4 },
  field: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: space.m,
           paddingVertical: 6 },
  fieldLabelRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  fieldLabel: { color: colors.dim, fontSize: 14 },
  fieldValue: { color: colors.text, fontSize: 15, fontWeight: font.semibold, flexShrink: 1, textAlign: 'right' },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 7, borderWidth: 1, borderRadius: radii.pill,
          paddingHorizontal: 11, paddingVertical: 6, alignSelf: 'flex-start' },
  pillText: { fontWeight: font.bold, fontSize: 13 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 4, borderWidth: 1, borderRadius: radii.pill,
          paddingHorizontal: 9, paddingVertical: 3, alignSelf: 'flex-start' },
  chipText: { fontWeight: font.heavy, fontSize: 11, letterSpacing: 0.6 },
  pending: { flexDirection: 'row', alignItems: 'center', gap: space.s, backgroundColor: alpha(colors.warn, 0.12),
             borderColor: alpha(colors.warn, 0.5), borderWidth: 1, borderRadius: radii.m, padding: space.m },
  pendingText: { color: '#FCD34D', fontWeight: font.semibold, flex: 1 },
  teamBadge: { borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  teamBadgeText: { fontWeight: font.black },
  thumb: { borderRadius: radii.m, borderWidth: 2, backgroundColor: '#000' },
  thumbEmpty: { alignItems: 'center', justifyContent: 'center' },
  compassRing: { position: 'absolute', borderWidth: 2, borderColor: colors.borderStrong, backgroundColor: colors.surface2 },
  compassInner: { position: 'absolute', borderWidth: 1, borderColor: colors.border },
  compassN: { position: 'absolute', color: colors.danger, fontSize: 11, fontWeight: font.black },
  compassHint: { position: 'absolute', bottom: -18, color: colors.muted, fontSize: 10 },
  toast: { position: 'absolute', left: space.l, right: space.l, flexDirection: 'row', alignItems: 'center',
           gap: space.m, backgroundColor: colors.surface3, borderColor: colors.borderStrong, borderWidth: 1,
           borderRadius: radii.m, padding: space.l, ...shadow.float },
  toastText: { color: colors.text, fontSize: 15, flex: 1 },
});
