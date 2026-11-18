/** Full-screen alert (9.3 #3): shown over the lock screen; siren stops when opened. */

import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useRef } from 'react';
import { Animated, Easing, Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import SarNative from '../../../modules/sar-native';
import { ageText, describeFrom, formatDistance } from '../../core/geo';
import { engine } from '../../engine/engine';
import { store, useStore, useVictim } from '../../state/store';
import { Button, CompassArrow, Icon } from '../../ui/components';
import { alpha, colors, font, radii, space } from '../../ui/theme';

function close() {
  if (router.canGoBack()) router.back();
  else router.replace('/home');
}

export default function AlertScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const v = useVictim(id);
  const me = useStore((s) => s.me);
  const teamId = useStore((s) => s.teamId);
  const insets = useSafeAreaInsets();
  const ring = useRef(new Animated.Value(0)).current;
  const closing = useRef(false);

  useEffect(() => {
    SarNative.setShowWhenLocked(true);
    store.set({ alertOnScreen: id ?? null });
    if (id) void engine.markOpened(id); // opened_at receipt + stops the siren repeats
    return () => {
      SarNative.setShowWhenLocked(false);
      if (store.get().alertOnScreen === id) store.set({ alertOnScreen: null });
    };
  }, [id]);

  useEffect(() => {
    const loop = Animated.loop(Animated.timing(ring, { toValue: 1, duration: 1400, easing: Easing.out(Easing.quad),
                                                       useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [ring]);

  // The victim stopped needing this phone while the alert was up: say why and get out of the way.
  useEffect(() => {
    if (!id || closing.current) return;
    let reason: string | null = null;
    if (!v) reason = 'That alert was withdrawn or taken by another team.';
    else if (v.status === 'ongoing' && v.assigned_team_id === teamId && v.local_pending_action !== 'accept') {
      reason = `Your team is already on ${v.display_code} (accepted from another phone).`;
    } else if (v.status !== 'open' && v.local_pending_action !== 'accept') {
      reason = `${v.display_code} no longer needs a team.`;
    }
    if (reason) {
      closing.current = true;
      store.toast(reason);
      close();
    }
  }, [v, id, teamId]);

  if (!v) return <View style={st.page} />;
  const where = describeFrom(me, v);

  return (
    <View style={[st.page, { paddingTop: insets.top + space.l, paddingBottom: insets.bottom + space.l }]}>
      <View style={st.head}>
        <View style={st.bell}>
          <Animated.View style={[st.ring, {
            opacity: ring.interpolate({ inputRange: [0, 1], outputRange: [0.55, 0] }),
            transform: [{ scale: ring.interpolate({ inputRange: [0, 1], outputRange: [1, 1.9] }) }],
          }]} />
          <Icon name="bell-ring" size={30} color="#fff" />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={st.kicker}>NEW VICTIM</Text>
          <Text style={st.code}>{v.display_code}</Text>
        </View>
        <Pressable hitSlop={12} onPress={close} accessibilityLabel="Not now">
          <Icon name="close" size={28} color="#FECACA" />
        </Pressable>
      </View>

      <View style={st.imageCard}>
        {v.thumb_local_path ? (
          <Image source={{ uri: v.thumb_local_path }} style={st.image} resizeMode="contain" />
        ) : (
          <View style={[st.image, st.noImage]}>
            <Icon name="image-off-outline" size={36} color="#FCA5A5" />
            <Text style={st.dim}>Image still downloading</Text>
          </View>
        )}
        <View style={st.chips}>
          {v.max_confidence != null && <Chip icon="target" text={`${Math.round(v.max_confidence * 100)}% sure`} />}
          <Chip icon="clock-outline" text={`found ${ageText(v.first_detected_at)}`} />
          {v.position_error_m ? <Chip icon="crosshairs" text={`±${Math.round(v.position_error_m)} m`} /> : null}
        </View>
      </View>

      <View style={st.whereCard}>
        {where ? <CompassArrow bearing={where.bearing} size={84} /> : (
          <View style={st.noGps}><Icon name="crosshairs-question" size={34} color="#FCA5A5" /></View>
        )}
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={st.distance}>{where ? formatDistance(where.distance) : 'No GPS fix'}</Text>
          <Text style={st.dim}>{where ? where.text : 'Waiting for your position'}</Text>
        </View>
      </View>

      <View style={{ flex: 1 }} />
      <Button title="ACCEPT · WE ARE GOING" icon="run-fast" variant="primary" size="l"
              style={st.accept}
              onPress={async () => {
                closing.current = true;
                await engine.accept(v.id);
                router.replace(`/victim/${v.id}`);
              }} />
      <View style={st.row}>
        <Button title="View on map" icon="map-marker-radius" style={[st.secondary, { flex: 1 }]}
                onPress={() => {
                  closing.current = true;
                  close();
                  router.navigate({ pathname: '/map', params: { focus: v.id, at: String(Date.now()) } });
                }} />
        <Button title="Not now" style={[st.secondary, { flex: 1 }]} onPress={() => { closing.current = true; close(); }} />
      </View>
    </View>
  );
}

function Chip({ icon, text }: { icon: 'target' | 'clock-outline' | 'crosshairs'; text: string }) {
  return (
    <View style={st.chip}>
      <Icon name={icon} size={14} color="#FECACA" />
      <Text style={st.chipText}>{text}</Text>
    </View>
  );
}

const st = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.alarmBg, paddingHorizontal: space.l, gap: space.l },
  head: { flexDirection: 'row', alignItems: 'center', gap: space.l },
  bell: { width: 64, height: 64, borderRadius: 32, backgroundColor: colors.danger, alignItems: 'center',
          justifyContent: 'center' },
  ring: { position: 'absolute', width: 64, height: 64, borderRadius: 32, backgroundColor: colors.danger },
  kicker: { color: '#FCA5A5', fontWeight: font.black, letterSpacing: 3, fontSize: 14 },
  code: { color: '#fff', fontSize: 44, fontWeight: font.black, letterSpacing: -1 },
  imageCard: { backgroundColor: alpha('#ffffff', 0.05), borderRadius: radii.l, borderWidth: 1,
               borderColor: alpha(colors.danger, 0.35), overflow: 'hidden' },
  image: { width: '100%', height: 230, backgroundColor: '#000' },
  noImage: { alignItems: 'center', justifyContent: 'center', gap: space.s },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: space.s, padding: space.m },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: alpha(colors.danger, 0.18),
          borderRadius: radii.pill, paddingHorizontal: 10, paddingVertical: 5 },
  chipText: { color: '#FEE2E2', fontSize: 13, fontWeight: font.semibold },
  whereCard: { flexDirection: 'row', alignItems: 'center', gap: space.l, backgroundColor: alpha('#ffffff', 0.05),
               borderRadius: radii.l, padding: space.l, borderWidth: 1, borderColor: alpha('#ffffff', 0.08) },
  noGps: { width: 84, height: 84, borderRadius: 42, backgroundColor: alpha(colors.danger, 0.15), alignItems: 'center',
           justifyContent: 'center' },
  distance: { color: '#fff', fontSize: 32, fontWeight: font.heavy, letterSpacing: -0.5 },
  dim: { color: '#FECACA', fontSize: 14 },
  accept: { backgroundColor: colors.danger, borderColor: '#F87171' },
  secondary: { backgroundColor: alpha('#ffffff', 0.06), borderColor: alpha('#ffffff', 0.14) },
  row: { flexDirection: 'row', gap: space.m },
});
