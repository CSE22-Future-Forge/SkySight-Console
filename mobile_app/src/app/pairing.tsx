/** Pairing (ARCHITECTURE.md 9.3 #1): scan -> Wi-Fi -> pair -> team -> map -> link -> phone setup. */

import { CameraView, useCameraPermissions } from 'expo-camera';
import { router } from 'expo-router';
import { useRef, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { normalizeServerUrl, parsePairingCode } from '../core/api';
import type { PairingPayload, Team } from '../core/types';
import { engine } from '../engine/engine';
import { deviceLabel, downloadMap, pairDevice, type PairingProgress, type StepId, testLink, TeamRequired } from '../engine/pairing';
import { Button, Card, Icon, type IconName, ProgressBar, TeamBadge } from '../ui/components';
import { PermissionChecklist } from '../ui/permissions';
import { alpha, colors, font, radii, space, type } from '../ui/theme';

const STEPS: { id: StepId; title: string; icon: IconName }[] = [
  { id: 'wifi', title: 'Join SAR-NET Wi-Fi', icon: 'wifi' },
  { id: 'pair', title: 'Register with the base', icon: 'shield-check-outline' },
  { id: 'snapshot', title: 'Load the manifest', icon: 'clipboard-check-outline' },
  { id: 'map', title: 'Download offline map', icon: 'map-outline' },
  { id: 'link', title: 'Test live alerts', icon: 'access-point' },
];

type Phase = 'scan' | 'team' | 'progress' | 'setup';

export default function Pairing() {
  const insets = useSafeAreaInsets();
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
    router.replace('/home');
  }

  // ----------------------------------------------------------------- views ---
  const pad = { paddingTop: insets.top + space.l, paddingBottom: insets.bottom + space.xl };

  if (phase === 'scan') {
    return (
      <ScrollView style={st.page} contentContainerStyle={[st.content, pad]} keyboardShouldPersistTaps="handled">
        <View style={st.brand}>
          <View style={st.logo}><Icon name="radar" size={34} color={colors.accent} /></View>
          <View style={{ flex: 1 }}>
            <Text style={st.h1}>Pair with the SAR base</Text>
            <Text style={st.p}>Join the SAR-NET Wi-Fi, then on the laptop choose Pair phone.</Text>
          </View>
        </View>

        <View style={st.field}>
          <Text style={st.label}>PHONE NAME (SHOWN TO THE COORDINATOR)</Text>
          <TextInput style={st.input} value={label} onChangeText={setLabel} />
        </View>

        {!manual && (permission?.granted ? (
          <View style={st.camera}>
            <CameraView style={StyleSheet.absoluteFill} facing="back"
                        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
                        // raw = the exact QR text. data is ML Kit's displayValue, which for our JSON
                        // (it contains "http://...") is only the URL.
                        onBarcodeScanned={(r) => onScanned(r.raw ?? r.data)} />
            <View style={st.frame}>
              <View style={[st.corner, st.tl]} />
              <View style={[st.corner, st.tr]} />
              <View style={[st.corner, st.bl]} />
              <View style={[st.corner, st.br]} />
            </View>
            <View style={st.cameraHint}>
              <Icon name="qrcode-scan" size={16} color={colors.text} />
              <Text style={st.cameraHintText}>Point at the QR code on the laptop</Text>
            </View>
          </View>
        ) : (
          <Card style={{ alignItems: 'center', gap: space.m, paddingVertical: space.xl }}>
            <Icon name="camera-outline" size={40} color={colors.dim} />
            <Text style={[st.p, { textAlign: 'center' }]}>The camera is only used to read the pairing code.</Text>
            <Button title="Allow camera to scan" icon="qrcode-scan" variant="primary" style={{ alignSelf: 'stretch' }}
                    onPress={() => void requestPermission()} />
          </Card>
        ))}
        {manual && (
          <Card style={{ gap: space.m }}>
            <View style={st.field}>
              <Text style={st.label}>SERVER ADDRESS</Text>
              <TextInput style={st.input} value={manualUrl} onChangeText={setManualUrl} autoCapitalize="none"
                         autoCorrect={false} keyboardType="url" placeholder="As shown on the laptop, e.g. 192.168.50.10"
                         placeholderTextColor={colors.muted} />
            </View>
            <View style={st.field}>
              <Text style={st.label}>CODE</Text>
              <TextInput style={st.input} value={manualToken} onChangeText={setManualToken} autoCapitalize="none"
                         autoCorrect={false} />
            </View>
            <Button title="Pair" icon="link-variant" variant="primary" onPress={onManual} />
          </Card>
        )}
        <Pressable onPress={() => setManual(!manual)} style={st.switch}>
          <Icon name={manual ? 'qrcode-scan' : 'keyboard-outline'} size={18} color={colors.accent} />
          <Text style={st.link}>{manual ? 'Scan the QR code instead' : 'Type the code instead'}</Text>
        </Pressable>
        {error && <ErrorBox text={error} />}
      </ScrollView>
    );
  }

  if (phase === 'team') {
    return (
      <ScrollView style={st.page} contentContainerStyle={[st.content, pad]}>
        <Text style={st.h1}>Which team are you in?</Text>
        <Text style={st.p}>Alerts, jobs and the map are shared with your team.</Text>
        {teams.map((t) => (
          <Card key={t.id} onPress={() => code && void run(code, t.id)} style={st.teamRow}>
            <TeamBadge color={t.color_hex} label={t.call_sign ?? t.name.slice(0, 2)} size={46} />
            <Text style={st.teamName}>{t.name}</Text>
            <Icon name="chevron-right" size={24} color={colors.muted} />
          </Card>
        ))}
        {teams.length === 0 && <Text style={st.p}>The base has no active teams yet. Ask the coordinator.</Text>}
      </ScrollView>
    );
  }

  if (phase === 'progress') {
    return (
      <ScrollView style={st.page} contentContainerStyle={[st.content, pad]}>
        <Text style={st.h1}>Setting up this phone</Text>
        <Text style={st.p}>Stay on SAR-NET until every step is done.</Text>
        <Card style={{ gap: 0 }}>
          {STEPS.map(({ id, title, icon }, i) => {
            const p = steps[id];
            const tone = !p ? colors.muted : p.state === 'done' ? colors.ok : p.state === 'error' ? colors.danger
              : p.state === 'warn' ? colors.warn : colors.accent;
            const glyph: IconName = !p ? icon : p.state === 'done' ? 'check' : p.state === 'error' ? 'close'
              : p.state === 'warn' ? 'alert-circle' : icon;
            return (
              <View key={id} style={st.step}>
                <View style={st.stepRail}>
                  <View style={[st.stepIcon, { backgroundColor: alpha(tone, 0.16), borderColor: alpha(tone, 0.6) }]}>
                    <Icon name={glyph} size={18} color={tone} />
                  </View>
                  {i < STEPS.length - 1 && <View style={st.stepLine} />}
                </View>
                <View style={{ flex: 1, paddingBottom: space.l, gap: 4 }}>
                  <Text style={[st.stepTitle, !p && { color: colors.dim }]}>{title}</Text>
                  {p?.detail ? <Text style={st.stepDetail}>{p.detail}</Text> : null}
                  {id === 'map' && p?.fraction != null && p.state === 'running' && <ProgressBar fraction={p.fraction} />}
                </View>
              </View>
            );
          })}
        </Card>
        {error && (
          <>
            <ErrorBox text={error} />
            <Text style={st.p}>The map download resumes where it stopped.</Text>
            <Button title="Try again" icon="sync" variant="primary" onPress={() => code && void run(code, null)} />
            <Button title="Scan a new code" icon="qrcode-scan"
                    onPress={() => { scanned.current = false; setPhase('scan'); }} />
          </>
        )}
      </ScrollView>
    );
  }

  return <PhoneSetup onDone={() => void finish()} pad={pad} />;
}

function ErrorBox({ text }: { text: string }) {
  return (
    <View style={st.error}>
      <Icon name="alert-octagon" size={20} color={colors.danger} />
      <Text style={st.errorText}>{text}</Text>
    </View>
  );
}

/** Permissions the field phone needs (9.4: battery-optimisation exemption at setup). */
function PhoneSetup({ onDone, pad }: { onDone: () => void; pad: { paddingTop: number; paddingBottom: number } }) {
  const [allOk, setAllOk] = useState(false);
  return (
    <ScrollView style={st.page} contentContainerStyle={[st.content, pad]}>
      <View style={st.doneBadge}><Icon name="check-circle" size={40} color={colors.ok} /></View>
      <Text style={[st.h1, { textAlign: 'center' }]}>Paired with the base</Text>
      <Text style={[st.p, { textAlign: 'center' }]}>
        Allow these so alerts reach you in the field, even with the screen off.
      </Text>
      <PermissionChecklist onChange={setAllOk} />
      <Button title={allOk ? 'Start' : 'Start anyway'} icon="arrow-right" variant={allOk ? 'primary' : 'secondary'}
              size="l" onPress={onDone} style={{ marginTop: space.s }} />
    </ScrollView>
  );
}

const CORNER = 28;

const st = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  content: { paddingHorizontal: space.l, gap: space.m },
  brand: { flexDirection: 'row', alignItems: 'center', gap: space.l, marginBottom: space.s },
  logo: { width: 64, height: 64, borderRadius: 20, backgroundColor: alpha(colors.accent, 0.14), borderWidth: 1,
          borderColor: alpha(colors.accent, 0.4), alignItems: 'center', justifyContent: 'center' },
  h1: { ...type.h1, color: colors.text },
  p: { color: colors.dim, fontSize: 15, lineHeight: 21 },
  switch: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space.s, paddingVertical: space.m },
  link: { color: colors.accent, fontSize: 15, fontWeight: font.bold },
  error: { flexDirection: 'row', alignItems: 'center', gap: space.s, backgroundColor: alpha(colors.danger, 0.12),
           borderColor: alpha(colors.danger, 0.5), borderWidth: 1, borderRadius: radii.m, padding: space.m },
  errorText: { color: '#FCA5A5', fontSize: 14, fontWeight: font.semibold, flex: 1 },
  camera: { height: 340, borderRadius: radii.l, overflow: 'hidden', backgroundColor: '#000' },
  frame: { position: 'absolute', left: '14%', right: '14%', top: '12%', bottom: '20%' },
  corner: { position: 'absolute', width: CORNER, height: CORNER, borderColor: colors.accent },
  tl: { top: 0, left: 0, borderTopWidth: 4, borderLeftWidth: 4, borderTopLeftRadius: 12 },
  tr: { top: 0, right: 0, borderTopWidth: 4, borderRightWidth: 4, borderTopRightRadius: 12 },
  bl: { bottom: 0, left: 0, borderBottomWidth: 4, borderLeftWidth: 4, borderBottomLeftRadius: 12 },
  br: { bottom: 0, right: 0, borderBottomWidth: 4, borderRightWidth: 4, borderBottomRightRadius: 12 },
  cameraHint: { position: 'absolute', bottom: space.m, alignSelf: 'center', flexDirection: 'row', alignItems: 'center',
                gap: 6, backgroundColor: alpha('#000000', 0.6), borderRadius: radii.pill, paddingHorizontal: 12,
                paddingVertical: 6 },
  cameraHintText: { color: colors.text, fontSize: 13, fontWeight: font.semibold },
  field: { gap: 6 },
  label: { ...type.overline, color: colors.muted, fontSize: 11 },
  input: { backgroundColor: colors.surface, color: colors.text, borderColor: colors.border, borderWidth: 1,
           borderRadius: radii.m, paddingHorizontal: space.m, minHeight: 50, fontSize: 16 },
  teamRow: { flexDirection: 'row', alignItems: 'center', gap: space.m },
  teamName: { ...type.h3, color: colors.text, flex: 1 },
  step: { flexDirection: 'row', gap: space.m },
  stepRail: { alignItems: 'center', width: 36 },
  stepIcon: { width: 36, height: 36, borderRadius: 18, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  stepLine: { flex: 1, width: 2, backgroundColor: colors.border, marginVertical: 4 },
  stepTitle: { color: colors.text, fontSize: 16, fontWeight: font.bold, marginTop: 7 },
  stepDetail: { color: colors.dim, fontSize: 13 },
  doneBadge: { alignSelf: 'center', width: 76, height: 76, borderRadius: 38, backgroundColor: alpha(colors.ok, 0.14),
               alignItems: 'center', justifyContent: 'center', marginBottom: space.s },
});
