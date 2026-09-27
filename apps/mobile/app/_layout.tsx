import '@expo/metro-runtime';
import {
  DefaultTheme,
  Stack,
  ThemeProvider,
  useRouter,
  useSegments,
} from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect } from 'react';
import { StatusBar } from 'expo-status-bar';
import 'react-native-reanimated';
import {
  ActivityIndicator,
  Platform,
  StyleSheet,
  useWindowDimensions,
  View,
} from 'react-native';
import { Theme } from '../constants/theme';
import { SessionProvider, useSession } from '../contexts/SessionContext';
import { OutboxRunner } from '../components/sync/OutboxRunner';

export { ErrorBoundary } from 'expo-router';

export const unstable_settings = {
  initialRouteName: '(tabs)',
};

void SplashScreen.preventAutoHideAsync();

const NAVIGATION_THEME = {
  ...DefaultTheme,
  colors: {
    ...DefaultTheme.colors,
    background: Theme.colors.bg,
    card: Theme.colors.surface,
    text: Theme.colors.text,
    border: Theme.colors.border,
    primary: Theme.colors.brand,
  },
};

/** On a wide browser window the app keeps a phone's width, centred. */
const FRAME_WIDTH = 560;

function AppFrame({ children }: { children: React.ReactNode }) {
  const { width } = useWindowDimensions();
  const framed = Platform.OS === 'web' && width > FRAME_WIDTH;
  return (
    <View style={styles.page}>
      <View style={[styles.frame, framed && styles.framed]}>{children}</View>
    </View>
  );
}

/**
 * Sends the person to onboarding until a crew session exists, and keeps them
 * out of onboarding once it does.
 */
function SessionGate({ children }: { children: React.ReactNode }) {
  const { ready, session } = useSession();
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (!ready) return;

    const inOnboarding = segments[0] === 'onboarding';

    if (!session && !inOnboarding) {
      router.replace('/onboarding');
    } else if (session && inOnboarding) {
      router.replace('/(tabs)');
    }
  }, [ready, session, segments, router]);

  if (!ready) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Theme.colors.brand} />
      </View>
    );
  }

  return <>{children}</>;
}

function RootNavigator() {
  return (
    <SessionGate>
      <OutboxRunner />
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="onboarding" options={{ headerShown: false }} />
      </Stack>
    </SessionGate>
  );
}

export default function RootLayout() {
  useEffect(() => {
    void SplashScreen.hideAsync();
  }, []);

  return (
    <SessionProvider>
      <ThemeProvider value={NAVIGATION_THEME}>
        <StatusBar style="dark" />
        <AppFrame>
          <RootNavigator />
        </AppFrame>
      </ThemeProvider>
    </SessionProvider>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: Theme.colors.surfaceElevated },
  frame: {
    flex: 1,
    width: '100%',
    alignSelf: 'center',
    backgroundColor: Theme.colors.bg,
  },
  framed: {
    maxWidth: FRAME_WIDTH,
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderColor: Theme.colors.border,
  },
  loading: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Theme.colors.bg,
  },
});
