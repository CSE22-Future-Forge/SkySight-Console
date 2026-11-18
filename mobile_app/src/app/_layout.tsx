import { router, Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { AppState, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import SarNative from '../../modules/sar-native';
import type { NotificationOpen } from '../../modules/sar-native';
import { engine } from '../engine/engine';
import { store, unseenAlerts } from '../state/store';
import { Toast } from '../ui/components';
import { colors, font } from '../ui/theme';

/**
 * Full-screen alerts. Exactly one alert screen at a time: the oldest alarm nobody has looked
 * at yet. Opening it marks it seen, so the next one (several victims can be found together)
 * follows when it closes. Re-checked whenever the store changes or the app comes back to the
 * foreground, so an alarm that rang in the background is shown when the user returns.
 */
let pushedAlert: string | null = null;
let pushedAt = 0;
/** A push that never mounted (navigator not ready yet) is retried after this long. */
const PUSH_RETRY_MS = 3000;

function showAlert(id: string) {
  const s = store.get();
  if (s.alertOnScreen === id) return;
  if (pushedAlert === id && Date.now() - pushedAt < PUSH_RETRY_MS) return;
  try {
    if (s.alertOnScreen) router.replace(`/alert/${id}`);
    else router.push(`/alert/${id}`);
    pushedAlert = id;
    pushedAt = Date.now();
  } catch {
    pushedAlert = null; // navigation not mounted yet; the next store change retries
  }
}

function presentNextAlert() {
  const s = store.get();
  if (!s.paired || AppState.currentState !== 'active') return;
  if (s.alertOnScreen) {
    pushedAlert = null; // that push landed
    return;
  }
  const next = unseenAlerts(s.victims)[0];
  if (next) showAlert(next.id);
  else pushedAlert = null;
}

/** Notification tap / action / full-screen intent -> the right screen. */
async function routeNotification(n: NotificationOpen | null) {
  if (!n?.victimId) return;
  const id = n.victimId;
  if (n.action === 'accept') {
    await engine.accept(id);
    router.push(`/victim/${id}`);
  } else if (n.action === 'map') {
    await engine.markOpened(id);
    router.navigate({ pathname: '/map', params: { focus: id } });
  } else {
    const v = store.get().victims.find((x) => x.id === id);
    if (v && v.status === 'open' && !v.local_pending_action) showAlert(id);
    else router.push(`/victim/${id}`);
  }
}

export default function RootLayout() {
  useEffect(() => {
    let alive = true;
    (async () => {
      const paired = await engine.boot();
      if (!alive) return;
      if (paired) {
        await engine.start();
        await routeNotification(SarNative.takeLaunchAction());
      }
    })();
    const open = SarNative.addListener('onNotificationOpen', (n) => void routeNotification(n));
    const unsub = store.subscribe(presentNextAlert);
    const appState = AppState.addEventListener('change', (st) => {
      if (st === 'active') presentNextAlert();
    });
    return () => {
      alive = false;
      open.remove();
      unsub();
      appState.remove();
    };
  }, []);

  return (
    <SafeAreaProvider>
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <StatusBar style="light" />
        <Stack screenOptions={{
          headerStyle: { backgroundColor: colors.bg },
          headerShadowVisible: false,
          headerTintColor: colors.text,
          headerTitleStyle: { fontWeight: font.bold, fontSize: 18 },
          contentStyle: { backgroundColor: colors.bg },
          animation: 'slide_from_right',
        }}>
          <Stack.Screen name="index" options={{ headerShown: false }} />
          <Stack.Screen name="pairing" options={{ headerShown: false }} />
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          <Stack.Screen name="alert/[id]" options={{ headerShown: false, presentation: 'fullScreenModal',
                                                     animation: 'fade', gestureEnabled: false }} />
          <Stack.Screen name="victim/[id]" options={{ title: 'Victim' }} />
          <Stack.Screen name="complete/[id]" options={{ title: 'Rescue report', presentation: 'modal' }} />
        </Stack>
        <Toast />
      </View>
    </SafeAreaProvider>
  );
}
