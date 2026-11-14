/** Full-screen alert (9.3 #3): shown over the lock screen; siren stops when opened. */

import { router, useLocalSearchParams } from 'expo-router';
import { useEffect } from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import SarNative from '../../../modules/sar-native';
import { ageText, describeFrom } from '../../core/geo';
import { engine } from '../../engine/engine';
import { store, useStore, useVictim } from '../../state/store';
import { Button, CompassArrow } from '../../ui/components';
import { colors, space } from '../../ui/theme';

export default function AlertScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const v = useVictim(id);
  const me = useStore((s) => s.me);
  const insets = useSafeAreaInsets();

  useEffect(() => {
    SarNative.setShowWhenLocked(true);
    if (id) void engine.markOpened(id); // opened_at receipt + stops the siren repeats
    return () => {
      SarNative.setShowWhenLocked(false);
      if (store.get().alertVictimId === id) store.set({ alertVictimId: null });
    };
  }, [id]);

  // Hidden by the base (claimed by another team / dismissed) while we looked at it.
  useEffect(() => {
    if (!v && id) {
      store.toast('That alert was withdrawn or taken by another team.');
      router.back();
    }
  }, [v, id]);

  if (!v) return <View style={st.page} />;
  const where = describeFrom(me, v);

  return (
    <View style={[st.page, { paddingTop: insets.top + space.l, paddingBottom: insets.bottom + space.l }]}>
      <Text style={st.kicker}>NEW VICTIM</Text>
      <Text style={st.code}>{v.display_code}</Text>
      {v.thumb_local_path ? (
        <Image source={{ uri: v.thumb_local_path }} style={st.thumb} resizeMode="contain" />
      ) : (
        <View style={[st.thumb, st.noThumb]}><Text style={st.dim}>No image yet</Text></View>
      )}
      <View style={st.row}>
        {where && <CompassArrow bearing={where.bearing} size={84} />}
        <View style={{ flex: 1, gap: 4 }}>
          <Text style={st.big}>{where ? where.text : 'Waiting for GPS'}</Text>
          <Text style={st.dim}>
            {v.max_confidence != null ? `${Math.round(v.max_confidence * 100)}% sure` : ''}
            {` · detected ${ageText(v.first_detected_at)}`}
          </Text>
          {v.position_error_m ? <Text style={st.dim}>{`position ±${Math.round(v.position_error_m)} m`}</Text> : null}
        </View>
      </View>
      <View style={{ flex: 1 }} />
      <Button title="ACCEPT - WE ARE GOING" variant="primary" style={{ minHeight: 72 }}
              onPress={async () => { await engine.accept(v.id); router.replace(`/victim/${v.id}`); }} />
      <Button title="View on map" style={{ marginTop: space.m }}
              onPress={() => router.replace({ pathname: '/map', params: { focus: v.id } })} />
    </View>
  );
}

const st = StyleSheet.create({
  page: { flex: 1, backgroundColor: '#3a0d0f', paddingHorizontal: space.l },
  kicker: { color: '#ff8a80', fontWeight: '900', letterSpacing: 3, fontSize: 16 },
  code: { color: '#fff', fontSize: 52, fontWeight: '900' },
  thumb: { width: '100%', height: 240, borderRadius: 12, backgroundColor: '#000', marginVertical: space.l },
  noThumb: { alignItems: 'center', justifyContent: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', gap: space.l },
  big: { color: '#fff', fontSize: 22, fontWeight: '800' },
  dim: { color: '#f3c2bf', fontSize: 15 },
});
