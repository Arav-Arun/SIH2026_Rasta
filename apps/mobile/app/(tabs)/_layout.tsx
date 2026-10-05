import { Redirect, Tabs } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  AlertTriangle,
  Camera,
  ClipboardList,
  Compass,
  RefreshCw,
  Package,
  Route,
  Siren,
  Users,
} from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { useSession } from '../../contexts/SessionContext';
import { CrewHeaderBadge } from '../../components/crew/CrewHeaderBadge';
import { BrandMark } from '../../components/ui/BrandMark';

export default function TabLayout() {
  const { session } = useSession();
  const insets = useSafeAreaInsets();

  // Every tab screen reads the crew session and cannot render without one.
  if (!session) return <Redirect href="/onboarding" />;

  const isObserver = session.role === 'observer';

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: Theme.colors.brand,
        tabBarInactiveTintColor: Theme.colors.textMuted,
        tabBarStyle: {
          backgroundColor: Theme.colors.surface,
          borderTopColor: Theme.colors.border,
          borderTopWidth: 1,
          height: 64 + insets.bottom,
          paddingTop: 4,
          paddingBottom: 4 + insets.bottom,
        },
        tabBarLabelStyle: { fontSize: 11, fontWeight: '600' },
        headerStyle: {
          backgroundColor: Theme.colors.surface,
          borderBottomColor: Theme.colors.border,
          borderBottomWidth: 1,
        },
        headerShadowVisible: false,
        headerTitleStyle: {
          color: Theme.colors.text,
          fontSize: 16,
          fontWeight: '700',
        },
        headerTintColor: Theme.colors.text,
        headerRight: () => <CrewHeaderBadge />,
        headerRightContainerStyle: { paddingRight: 12 },
      }}
    >
      {/* Observer-first: capture leads the bar for the person holding the camera. */}
      <Tabs.Screen
        name="capture"
        options={{
          title: 'Capture',
          headerTitle: 'Report a disruption',
          href: isObserver ? '/capture' : null,
          tabBarIcon: ({ color, size }) => (
            <Camera size={size - 2} color={color} />
          ),
        }}
      />

      {/* Everyone lands here after signing in, so it keeps its tab for both seats. */}
      <Tabs.Screen
        name="index"
        options={{
          title: 'Radar',
          headerTitle: () => <BrandMark />,
          tabBarIcon: ({ color, size }) => (
            <Compass size={size - 2} color={color} />
          ),
        }}
      />

      {/* Only the driver plans and follows a route. */}
      <Tabs.Screen
        name="routes"
        options={{
          title: 'Routes',
          headerTitle: 'Routes',
          href: isObserver ? null : '/routes',
          tabBarIcon: ({ color, size }) => (
            <Route size={size - 2} color={color} />
          ),
        }}
      />

      {/* The load is the driver's job: the observer never signs for a delivery. */}
      <Tabs.Screen
        name="trips"
        options={{
          title: 'Load',
          headerTitle: 'Your load',
          href: isObserver ? null : '/trips',
          tabBarIcon: ({ color, size }) => (
            <Package size={size - 2} color={color} />
          ),
        }}
      />

      {/* Assigned inspections are field work, so only the observer seat. */}
      <Tabs.Screen
        name="tasks"
        options={{
          title: 'Tasks',
          headerTitle: 'Assigned inspections',
          href: isObserver ? '/tasks' : null,
          tabBarIcon: ({ color, size }) => (
            <ClipboardList size={size - 2} color={color} />
          ),
        }}
      />

      <Tabs.Screen
        name="crew"
        options={{
          title: 'Crew',
          headerTitle: isObserver ? 'Sent from this cab' : 'From your observer',
          tabBarIcon: ({ color, size }) => (
            <Users size={size - 2} color={color} />
          ),
        }}
      />

      <Tabs.Screen
        name="sync"
        options={{
          title: 'Outbox',
          headerTitle: 'Offline reports',
          tabBarIcon: ({ color, size }) => (
            <RefreshCw size={size - 2} color={color} />
          ),
        }}
      />

      <Tabs.Screen
        name="sos"
        options={{
          title: 'SOS',
          headerTitle: 'Emergency help',
          tabBarActiveTintColor: Theme.colors.blocked,
          tabBarIcon: ({ color, size }) => (
            <Siren size={size - 2} color={color} />
          ),
        }}
      />

      {/* Reachable from the Radar quick actions, but off the bar to keep it to five. */}
      <Tabs.Screen
        name="report"
        options={{
          title: 'Report',
          headerTitle: 'Report hazard',
          href: null,
          tabBarIcon: ({ color, size }) => (
            <AlertTriangle size={size - 2} color={color} />
          ),
        }}
      />
    </Tabs>
  );
}
