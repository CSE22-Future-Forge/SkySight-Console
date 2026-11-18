/** Bottom tabs: Dashboard, Map, Jobs, Team. Full-screen flows (alert, victim, report) sit above them. */

import { Tabs } from 'expo-router/js-tabs';
import type { ColorValue } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useStore } from '../../state/store';
import { Icon, type IconName } from '../../ui/components';
import { colors, font } from '../../ui/theme';

function tabIcon(active: IconName, idle: IconName) {
  return ({ color, focused }: { color: ColorValue; focused: boolean }) => (
    <Icon name={focused ? active : idle} size={25} color={color} />
  );
}

export default function TabsLayout() {
  const insets = useSafeAreaInsets();
  const open = useStore((s) => s.victims.filter((v) => v.status === 'open' && !v.local_pending_action).length);
  const active = useStore((s) => s.victims.filter((v) => v.status === 'ongoing' && v.assigned_team_id === s.teamId).length);

  return (
    <Tabs screenOptions={{
      headerShown: false,
      tabBarActiveTintColor: colors.accent,
      tabBarInactiveTintColor: colors.muted,
      tabBarLabelStyle: { fontSize: 12, fontWeight: font.bold },
      tabBarStyle: {
        backgroundColor: colors.surface,
        borderTopColor: colors.border,
        height: 64 + insets.bottom,
        paddingTop: 8,
        paddingBottom: insets.bottom + 6,
      },
      tabBarBadgeStyle: { backgroundColor: colors.danger, color: '#fff', fontWeight: font.heavy, fontSize: 11 },
    }}>
      <Tabs.Screen name="home" options={{ title: 'Dashboard', tabBarIcon: tabIcon('view-dashboard', 'view-dashboard-outline') }} />
      <Tabs.Screen name="map" options={{ title: 'Map', tabBarIcon: tabIcon('map', 'map-outline'),
                                         tabBarBadge: open || undefined }} />
      <Tabs.Screen name="jobs" options={{ title: 'Jobs', tabBarIcon: tabIcon('clipboard-check', 'clipboard-check-outline'),
                                          tabBarBadge: active || undefined,
                                          tabBarBadgeStyle: { backgroundColor: colors.warn, color: '#1A1203' } }} />
      <Tabs.Screen name="settings" options={{ title: 'Team', tabBarIcon: tabIcon('account-group', 'account-group-outline') }} />
    </Tabs>
  );
}
