/**
 * Field UI theme: dark "mission control" surfaces, high contrast text, big touch targets.
 * Status colours match the base's map so a red dot means the same thing everywhere.
 */

import type { TextStyle } from 'react-native';

export const colors = {
  bg: '#0A101C',
  surface: '#111A2B',
  surface2: '#172338',
  surface3: '#1E2C46',
  border: '#22314D',
  borderStrong: '#30446B',
  text: '#EDF2FB',
  dim: '#A3B1CA',
  muted: '#67768F',
  accent: '#4C8DFF',
  accentDeep: '#2F6BE0',
  ok: '#22C55E',
  warn: '#F59E0B',
  danger: '#EF4444',
  alarmBg: '#1A0709',
  // older names, still used by the map and a few screens
  panel: '#111A2B',
  panel2: '#172338',
};

/** ``color`` with alpha (0..1) appended, for tinted backgrounds and borders. */
export function alpha(color: string, a: number): string {
  const hex = Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0');
  return `${color.slice(0, 7)}${hex}`;
}

export const statusColor: Record<string, string> = {
  open: '#EF4444',
  ongoing: '#F59E0B',
  completed: '#22C55E',
  dismissed: '#94A3B8',
  pending: '#A855F7',
};

export const statusLabel: Record<string, string> = {
  open: 'OPEN',
  ongoing: 'ON THE WAY',
  completed: 'RESCUED',
  dismissed: 'DISMISSED',
  pending: 'PENDING',
};

export const statusIcon: Record<string, string> = {
  open: 'alert-circle',
  ongoing: 'run-fast',
  completed: 'check-circle',
  dismissed: 'close-circle',
  pending: 'clock-outline',
};

export const pendingLabel: Record<string, string> = {
  accept: 'Accept pending - not confirmed by base yet',
  release: 'Release pending - not confirmed by base yet',
  complete: 'Rescue report pending - not confirmed by base yet',
};

export const space = { xs: 4, s: 8, m: 12, l: 16, xl: 24, xxl: 32 };
export const radii = { s: 10, m: 14, l: 20, xl: 28, pill: 999 };
/** Card radius (kept as a plain number for older styles). */
export const radius = radii.m;
export const touch = 54; // minimum button height

export const font: Record<'regular' | 'medium' | 'semibold' | 'bold' | 'heavy' | 'black', TextStyle['fontWeight']> = {
  regular: '400',
  medium: '500',
  semibold: '600',
  bold: '700',
  heavy: '800',
  black: '900',
};

export const type = {
  display: { fontSize: 34, fontWeight: font.black, letterSpacing: -0.5 } as TextStyle,
  h1: { fontSize: 26, fontWeight: font.heavy, letterSpacing: -0.3 } as TextStyle,
  h2: { fontSize: 20, fontWeight: font.bold } as TextStyle,
  h3: { fontSize: 17, fontWeight: font.bold } as TextStyle,
  body: { fontSize: 15, fontWeight: font.regular } as TextStyle,
  small: { fontSize: 13, fontWeight: font.medium } as TextStyle,
  overline: { fontSize: 12, fontWeight: font.heavy, letterSpacing: 1.4 } as TextStyle,
  mono: { fontSize: 13, fontFamily: 'monospace' } as TextStyle,
};

export const shadow = {
  card: { elevation: 3, shadowColor: '#000', shadowOpacity: 0.25, shadowRadius: 10, shadowOffset: { width: 0, height: 4 } },
  float: { elevation: 8, shadowColor: '#000', shadowOpacity: 0.35, shadowRadius: 16, shadowOffset: { width: 0, height: 8 } },
};
