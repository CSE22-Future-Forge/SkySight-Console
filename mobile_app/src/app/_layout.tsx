import { router, Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { AppState, View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import SarNative from '../../modules/sar-native';
import type { NotificationOpen } from '../../modules/sar-native';
import { engine } from '../engine/engine';
import { store } from '../state/store';
import { Toast } from '../ui/components';
import { colors } from '../ui/theme';

/** Notification tap / action / full-screen intent -> the right screen. */
async function routeNotification(n: NotificationOpen | null) {
  if (!n?.victimId) return;
  const id = n.victimId;
  if (n.action === 'accept') {
    await engine.accept(id);
    router.push(`/victim/${id}`);
  } else if (n.action === 'map') {
    await engine.markOpened(id);
    router.push({ pathname: '/map', params: { focus: id } });
  } else {
    const v = store.get().victims.find((x) => x.id === id);
    router.push(v && v.status === 'open' ? `/alert/${id}` : `/victim/${id}`);
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

    // A new alarm while the app is on screen: show the alert screen straight away.
    let lastAlert: string | null = null;
    const unsub = store.subscribe(() => {
      const id = store.get().alertVictimId;
      if (id && id !== lastAlert && AppState.currentState === 'active') {
        lastAlert = id;
        router.push(`/alert/${id}`);
      }
    });
    return () => {
      alive = false;
      open.remove();
      unsub();
    };
  }, []);

  return (
    <SafeAreaProvider>
      <View style={{ flex: 1, backgroundColor: colors.bg }}>
        <StatusBar style="light" />
        <Stack screenOptions={{
          headerStyle: { backgroundColor: colors.panel },
          headerTintColor: colors.text,
          contentStyle: { backgroundColor: colors.bg },
        }}>
          <Stack.Screen name="index" options={{ headerShown: false }} />
          <Stack.Screen name="pairing" options={{ title: 'Pair with SAR base', headerBackVisible: false }} />
          <Stack.Screen name="map" options={{ headerShown: false }} />
          <Stack.Screen name="alert/[id]" options={{ headerShown: false, presentation: 'fullScreenModal',
                                                     animation: 'fade' }} />
          <Stack.Screen name="victim/[id]" options={{ title: 'Victim' }} />
          <Stack.Screen name="complete/[id]" options={{ title: 'Report rescue' }} />
          <Stack.Screen name="jobs" options={{ title: 'My jobs' }} />
        </Stack>
        <Toast />
      </View>
    </SafeAreaProvider>
  );
}
