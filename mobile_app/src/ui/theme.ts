/** Field UI: dark, high contrast, big targets. Status colours match the base's map. */

export const colors = {
  bg: '#0f1722',
  panel: '#162131',
  panel2: '#1d2a3d',
  border: '#2a3a52',
  text: '#e6edf5',
  dim: '#a9b7c9',
  muted: '#6d7f95',
  accent: '#3d8bfd',
  ok: '#43a047',
  warn: '#fb8c00',
  danger: '#e53935',
};

export const statusColor: Record<string, string> = {
  open: '#e53935',
  ongoing: '#fb8c00',
  completed: '#43a047',
  dismissed: '#9e9e9e',
  pending: '#ab47bc',
};

export const statusLabel: Record<string, string> = {
  open: 'OPEN',
  ongoing: 'ON THE WAY',
  completed: 'RESCUED',
  dismissed: 'DISMISSED',
  pending: 'PENDING',
};

export const pendingLabel: Record<string, string> = {
  accept: 'Accept pending - not confirmed by base',
  release: 'Release pending - not confirmed by base',
  complete: 'Rescue report pending - not confirmed by base',
};

export const space = { xs: 4, s: 8, m: 12, l: 16, xl: 24 };
export const radius = 10;
export const touch = 56; // minimum button height
