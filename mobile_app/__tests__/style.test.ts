import { normalizeServerUrl, parsePairingCode } from '../src/core/api';
import { mapAction, phoneStyle, PHONE_GLYPHS } from '../src/engine/mapStyle';

describe('phoneStyle', () => {
  it('points the vector source at the local PMTiles file and glyphs at the APK assets', () => {
    const server = JSON.stringify({
      version: 8,
      glyphs: 'http://192.168.50.10:8000/maps/glyphs/{fontstack}/{range}.pbf',
      sources: { openmaptiles: { type: 'vector', url: 'pmtiles://http://192.168.50.10:8000/maps/srilanka.pmtiles' } },
      layers: [{ id: 'background', type: 'background' }],
    });
    const style = phoneStyle(server, 'file:///data/user/0/lk.ac.mrt.sar.rescue/files/srilanka.pmtiles');
    expect(style.sources.openmaptiles.url).toBe('pmtiles://file:///data/user/0/lk.ac.mrt.sar.rescue/files/srilanka.pmtiles');
    expect(style.glyphs).toBe(PHONE_GLYPHS);
    expect(JSON.stringify(style)).not.toContain('192.168.50.10');
  });
});

describe('mapAction (no re-download on re-pair)', () => {
  const manifest = { size: 108_858_347, sha256: 'fce9a6' };
  it('keeps a verified map', () => {
    expect(mapAction({ exists: true, size: manifest.size, knownSha: 'fce9a6' }, manifest)).toBe('keep');
  });
  it('re-pairing (checksum forgotten) verifies the local file instead of downloading', () => {
    expect(mapAction({ exists: true, size: manifest.size, knownSha: null }, manifest)).toBe('verify');
  });
  it('downloads when missing or a different size (newer map on the base)', () => {
    expect(mapAction({ exists: false, size: null, knownSha: null }, manifest)).toBe('download');
    expect(mapAction({ exists: true, size: 5, knownSha: 'fce9a6' }, manifest)).toBe('download');
  });
});

describe('normalizeServerUrl (typed addresses)', () => {
  it.each([
    ['192.168.1.2', 'http://192.168.1.2:8000'],
    ['192.168.1.2:9000', 'http://192.168.1.2:9000'],
    [' http://192.168.50.10:8000/ ', 'http://192.168.50.10:8000'],
    ['HTTP://10.0.0.5', 'HTTP://10.0.0.5:8000'],
    ['', ''],
  ])('%s -> %s', (input, out) => {
    expect(normalizeServerUrl(input)).toBe(out);
  });
});

describe('parsePairingCode', () => {
  it('accepts the QR payload from the base', () => {
    expect(parsePairingCode('{"server_url":"http://192.168.50.10:8000/","pairing_token":"abcdefgh12","ssid":"SAR-NET"}'))
      .toEqual({ server_url: 'http://192.168.50.10:8000', pairing_token: 'abcdefgh12', ssid: 'SAR-NET' });
  });
  it('rejects other QR codes and shows what was read', () => {
    expect(() => parsePairingCode('http://192.168.1.2:8000')).toThrow(/not a SAR base.*192\.168\.1\.2/);
    expect(() => parsePairingCode('{"server_url":"x","pairing_token":"abcdefgh12"}')).toThrow(/server address/);
  });
});
