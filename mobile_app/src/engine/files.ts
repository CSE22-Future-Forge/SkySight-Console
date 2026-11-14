/** Where the phone keeps the offline map, style and thumbnails. */

import { Directory, File, Paths } from 'expo-file-system';

export const MAP_FILE = 'srilanka.pmtiles';

export const mapFile = () => new File(Paths.document, MAP_FILE);
export const styleFile = () => new File(Paths.document, 'style.json');
export const thumbsDir = () => {
  const d = new Directory(Paths.document, 'thumbs');
  if (!d.exists) d.create({ intermediates: true });
  return d;
};
export const thumbFile = (victimId: string) => new File(thumbsDir(), `${victimId}.jpg`);

/** Native paths for SarNative (it accepts file:// URIs too, this just normalises). */
export const nativePath = (f: File) => f.uri;

export function deleteAllLocalFiles() {
  for (const f of [mapFile(), styleFile()]) if (f.exists) f.delete();
  const thumbs = new Directory(Paths.document, 'thumbs');
  if (thumbs.exists) thumbs.delete();
}
