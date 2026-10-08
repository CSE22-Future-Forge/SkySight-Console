/** Pairing (ARCHITECTURE.md 9.3 #1): scan -> Wi-Fi -> pair -> team -> map -> link -> phone setup. */

import { CameraView, useCameraPermissions } from 'expo-camera';
import * as Location from 'expo-location';
import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { PermissionsAndroid, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import SarNative from '../../modules/sar-native';
import { normalizeServerUrl, parsePairingCode } from '../core/api';
import type { PairingPayload, Team } from '../core/types';
import { engine } from '../engine/engine';
import { deviceLabel, downloadMap, pairDevice, type PairingProgress, type StepId, testLink, TeamRequired } from '../engine/pairing';
import { Button } from '../ui/components';
import { colors, radius, space } from '../ui/theme';

const STEPS: { id: StepId; title: string }[] = [
  { id: 'wifi', title: 'Join SAR-NET Wi-Fi' },
  { id: 'pair', title: 'Register with the base' },
  { id: 'snapshot', title: 'Load the manifest' },
  { id: 'map', title: 'Download offline map' },
  { id: 'link', title: 'Test live alerts' },
];

type Phase = 'scan' | 'team' | 'progress' | 'setup';

export default function Pairing() {
  const [phase, setPhase] = useState<Phase>('scan');
  const [permission, requestPermission] = useCameraPermissions();
  const [code, setCode] = useState<PairingPayload | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [steps, setSteps] = useState<Record<string, PairingProgress>>({});
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const [manualUrl, setManualUrl] = useState('');
  const [manualToken, setManualToken] = useState('');
  const [label, setLabel] = useState(deviceLabel());
  const scanned = useRef(false);

  const progress = (p: PairingProgress) => setSteps((prev) => ({ ...prev, [p.step]: p }));

  async function run(payload: PairingPayload, teamId: string | null) {
    setError(null);
    setPhase('progress');
    try {
      await pairDevice(payload, teamId, label, progress);
      await downloadMap(progress);
      await testLink(progress);
      setPhase('setup');
    } catch (e) {
      if (e instanceof TeamRequired) {
        setTeams(e.teams);
        setPhase('team');
        return;
      }
      setError((e as Error).message);
    }
  }

  function onScanned(text: string) {
    if (scanned.current) return;
    try {
      const payload = parsePairingCode(text);
      scanned.current = true;
      setCode(payload);
      void run(payload, null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function onManual() {
    const payload = { server_url: normalizeServerUrl(manualUrl), pairing_token: manualToken.trim() };
    if (!payload.server_url || payload.pairing_token.length < 8) {
      setError('Enter the server address and the code shown under the QR on the laptop.');
      return;
    }
    setCode(payload);
    void run(payload, null);
  }

  async function finish() {
    await engine.boot();
    await engine.start();
    router.replace('/map');
  }

  // ----------------------------------------------------------------- views ---
  if (phase === 'scan') {
    return (
      <ScrollView style={st.page} contentContainerStyle={st.content}>
        <Text style={st.h1}>Scan the pairing code</Text>
        <Text style={st.p}>On the laptop: Pair phone. Join the SAR-NET Wi-Fi first.</Text>
        <View style={st.field}>
          <Text style={st.label}>Phone name (shown to the coordinator)</Text>
          <TextInput style={st.input} value={label} onChangeText={setLabel} />
        </View>
        {!manual && (permission?.granted ? (
          <View style={st.camera}>
            <CameraView style={StyleSheet.absoluteFill} facing="back"
                        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
                        // raw = the exact QR text. data is ML Kit's displayValue, which for our JSON
                        // (it contains "http://...") is only the URL.
                        onBarcodeScanned={(r) => onScanned(r.raw ?? r.data)} />
            <View style={st.frame} />
          </View>
        ) : (
          <Button title="Allow camera to scan" variant="primary" onPress={() => void requestPermission()} />
        ))}
        {manual && (
          <View style={{ gap: space.m }}>
            <View style={st.field}>
              <Text style={st.label}>Server address</Text>
              <TextInput style={st.input} value={manualUrl} onChangeText={setManualUrl} autoCapitalize="none"
                         autoCorrect={false} keyboardType="url" placeholder="As shown on the laptop, e.g. 192.168.50.10"
                         placeholderTextColor={colors.muted} />
            </View>
            <View style={st.field}>
              <Text style={st.label}>Code</Text>
              <TextInput style={st.input} value={manualToken} onChangeText={setManualToken} autoCapitalize="none"
                         autoCorrect={false} />
            </View>
            <Button title="Pair" variant="primary" onPress={onManual} />
          </View>
        )}
        <Pressable onPress={() => setManual(!manual)}>
          <Text style={st.link}>{manual ? 'Scan the QR code instead' : 'Type the code instead'}</Text>
        </Pressable>
        {error && <Text style={st.error}>{error}</Text>}
      </ScrollView>
    );
  }

  if (phase === 'team') {
    return (
      <ScrollView style={st.page} contentContainerStyle={st.content}>
        <Text style={st.h1}>Which team are you in?</Text>
        {teams.map((t) => (
          <Button key={t.id} title={`${t.name}${t.call_sign ? `  (${t.call_sign})` : ''}`}
                  style={{ borderLeftWidth: 8, borderLeftColor: t.color_hex }}
                  onPress={() => code && void run(code, t.id)} />
        ))}
        {teams.length === 0 && <Text style={st.p}>The base has no active teams yet. Ask the coordinator.</Text>}
      </ScrollView>
    );
  }

  if (phase === 'progress') {
    return (
      <ScrollView style={st.page} contentContainerStyle={st.content}>
        <Text style={st.h1}>Pairing</Text>
        {STEPS.map(({ id, title }) => {
          const p = steps[id];
          const icon = !p ? '○' : p.state === 'done' ? '✔' : p.state === 'error' ? '✖'
            : p.state === 'warn' ? '!' : '…';
          const color = !p ? colors.muted : p.state === 'done' ? colors.ok : p.state === 'error' ? colors.danger
            : p.state === 'warn' ? colors.warn : colors.accent;
          return (
            <View key={id} style={st.step}>
              <Text style={[st.stepIcon, { color }]}>{icon}</Text>
              <View style={{ flex: 1 }}>
                <Text style={st.stepTitle}>{title}</Text>
                {p?.detail ? <Text style={st.stepDetail}>{p.detail}</Text> : null}
                {id === 'map' && p?.fraction != null && p.state === 'running' && (
                  <View style={st.bar}><View style={[st.barFill, { width: `${Math.round(p.fraction * 100)}%` }]} /></View>
                )}
              </View>
            </View>
          );
        })}
        {error && (
          <>
            <Text style={st.error}>{error}</Text>
            <Text style={st.p}>The map download resumes where it stopped.</Text>
            <Button title="Try again" variant="primary" onPress={() => code && void run(code, null)} />
            <Button title="Scan a new code" onPress={() => { scanned.current = false; setPhase('scan'); }} />
          </>
        )}
      </ScrollView>
    );
  }

  return <PhoneSetup onDone={() => void finish()} />;
}

/** Permissions the field phone needs (9.4: battery-optimisation exemption at setup). */
function PhoneSetup({ onDone }: { onDone: () => void }) {
  const [state, setState] = useState({ location: false, notifications: false, fullScreen: false, battery: false });

  async function check() {
    const loc = await Location.getForegroundPermissionsAsync();
    const notif = Platform.OS === 'android' && (Platform.Version as number) >= 33
      ? await PermissionsAndroid.check('android.permission.POST_NOTIFICATIONS' as any) : true;
    setState({
      location: loc.granted,
      notifications: notif,
      fullScreen: SarNative.canUseFullScreenIntent(),
      battery: SarNative.isIgnoringBatteryOptimizations(),
    });
  }

  const items: { key: keyof typeof state; title: string; why: string; fix: () => Promise<unknown> | void }[] = [
    { key: 'location', title: 'Location', why: 'Shares your position with the base; stops the drone flagging your team.',
      fix: () => Location.requestForegroundPermissionsAsync() },
    { key: 'notifications', title: 'Notifications', why: 'Siren alarm for new victims.',
      fix: () => PermissionsAndroid.request('android.permission.POST_NOTIFICATIONS' as any) },
    { key: 'fullScreen', title: 'Full-screen alerts', why: 'Shows the alarm over the lock screen.',
      fix: () => SarNative.openFullScreenIntentSettings() },
    { key: 'battery', title: 'Ignore battery optimisation', why: 'Keeps the link to the base open with the screen off.',
      fix: () => SarNative.requestIgnoreBatteryOptimizations() },
  ];

  return (
    <ScrollView style={st.page} contentContainerStyle={st.content} onLayout={() => void check()}>
      <Text style={st.h1}>Phone setup</Text>
      <Text style={st.p}>Paired. Allow these so alerts reach you in the field.</Text>
      {items.map((it) => (
        <View key={it.key} style={st.setupRow}>
          <View style={{ flex: 1 }}>
            <Text style={st.stepTitle}>{state[it.key] ? '✔ ' : ''}{it.title}</Text>
            <Text style={st.stepDetail}>{it.why}</Text>
          </View>
          {!state[it.key] && (
            <Button title="Allow" onPress={async () => { await it.fix(); setTimeout(() => void check(), 800); }} />
          )}
        </View>
      ))}
      <Button title="Recheck" onPress={() => void check()} />
      <Button title="Start" variant="primary" onPress={onDone} />
    </ScrollView>
  );
}

const st = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.l, gap: space.m },
  h1: { color: colors.text, fontSize: 24, fontWeight: '800' },
  p: { color: colors.dim, fontSize: 15 },
  link: { color: colors.accent, fontSize: 15, paddingVertical: space.m, textAlign: 'center' },
  error: { color: '#ff8a80', fontSize: 15, fontWeight: '600' },
  camera: { height: 320, borderRadius: radius, overflow: 'hidden', backgroundColor: '#000' },
  frame: { position: 'absolute', left: '15%', right: '15%', top: '12%', bottom: '12%', borderColor: '#fff',
           borderWidth: 3, borderRadius: 14 },
  field: { gap: 4 },
  label: { color: colors.dim, fontSize: 13 },
  input: { backgroundColor: colors.panel2, color: colors.text, borderColor: colors.border, borderWidth: 1,
           borderRadius: radius, paddingHorizontal: space.m, minHeight: 48, fontSize: 16 },
  step: { flexDirection: 'row', gap: space.m, backgroundColor: colors.panel, borderRadius: radius, padding: space.m,
          borderWidth: 1, borderColor: colors.border },
  stepIcon: { fontSize: 20, width: 24, textAlign: 'center', fontWeight: '800' },
  stepTitle: { color: colors.text, fontSize: 16, fontWeight: '700' },
  stepDetail: { color: colors.dim, fontSize: 13, marginTop: 2 },
  bar: { height: 8, backgroundColor: colors.panel2, borderRadius: 4, marginTop: 8, overflow: 'hidden' },
  barFill: { height: 8, backgroundColor: colors.accent },
  setupRow: { flexDirection: 'row', alignItems: 'center', gap: space.m, backgroundColor: colors.panel,
              borderRadius: radius, padding: space.m, borderWidth: 1, borderColor: colors.border },
});
