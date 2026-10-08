import { bearingDeg, cardinal, describeFrom, formatDistance, haversineM, relativeBearing } from '../src/core/geo';

describe('geo', () => {
  it('one degree of latitude is ~111 km', () => {
    expect(haversineM(7, 80, 8, 80)).toBeCloseTo(111_195, -2);
  });

  it('bearings point the right way', () => {
    expect(bearingDeg(7, 80, 7.01, 80)).toBeCloseTo(0, 1);
    expect(bearingDeg(7, 80, 7, 80.01)).toBeCloseTo(90, 1);
    expect(bearingDeg(7, 80, 6.99, 80)).toBeCloseTo(180, 1);
    expect(cardinal(44)).toBe('NE');
    expect(cardinal(359)).toBe('N');
  });

  it('formats distances for the field', () => {
    expect(formatDistance(42.4)).toBe('42 m');
    expect(formatDistance(1534)).toBe('1.53 km');
    expect(formatDistance(23_400)).toBe('23.4 km');
  });

  it('turns the compass arrow by the phone heading', () => {
    expect(relativeBearing(90, null)).toBe(90);
    expect(relativeBearing(90, 80)).toBe(10);
    expect(relativeBearing(10, 350)).toBe(20);
  });

  it('describes a target from my position', () => {
    const d = describeFrom({ lat: 7.29, lon: 80.63 }, { lat: 7.2909, lon: 80.63 })!;
    expect(Math.round(d.distance)).toBe(100);
    expect(d.text).toMatch(/^100 m N \(0°\)$/);
    expect(describeFrom(null, { lat: 7, lon: 80 })).toBeNull();
  });
});
