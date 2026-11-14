/**
 * Turn the base's /maps/style.json into the phone's offline style (ARCHITECTURE.md 8):
 * vector source -> pmtiles://file://<docs>/srilanka.pmtiles, glyphs -> APK assets.
 * Kept pure so it can be unit-tested.
 */

export const PHONE_GLYPHS = 'asset://glyphs/{fontstack}/{range}.pbf';

export function phoneStyle(serverStyleText: string, mapFileUri: string): any {
  const style = JSON.parse(serverStyleText);
  const fileUrl = mapFileUri.startsWith('file://') ? mapFileUri : `file://${mapFileUri}`;
  for (const src of Object.values<any>(style.sources ?? {})) {
    if (src.type === 'vector') src.url = `pmtiles://${fileUrl}`;
  }
  style.glyphs = PHONE_GLYPHS;
  delete style.sprite; // none bundled; the style uses no icons
  return style;
}

export interface LocalMap {
  exists: boolean;
  size: number | null;
  /** Checksum remembered from the last verified download (lost when the phone re-pairs). */
  knownSha: string | null;
}

/**
 * What to do with the map file already on the phone, given the base's /maps/manifest:
 *  - 'keep'   : same size and remembered checksum matches -> no work
 *  - 'verify' : same size but checksum unknown/different -> hash the local file first
 *  - 'download': missing or a different size -> fetch from the base (resumes a .part file)
 */
export function mapAction(local: LocalMap, manifest: { size: number; sha256: string }): 'keep' | 'verify' | 'download' {
  if (!local.exists || local.size !== manifest.size) return 'download';
  return local.knownSha === manifest.sha256 ? 'keep' : 'verify';
}

/** Fallback when no map is installed yet: plain background, still shows markers + GPS. */
export const EMPTY_STYLE = {
  version: 8,
  glyphs: PHONE_GLYPHS,
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#e9e5dc' } }],
};
