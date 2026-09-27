import { Link, Stack } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';

import { Theme } from '@/constants/theme';

export default function NotFoundScreen() {
  return (
    <>
      <Stack.Screen options={{ title: 'Not found' }} />
      <View style={styles.container}>
        <Text style={styles.title}>This screen does not exist.</Text>
        <Link href="/" style={styles.link}>
          <Text style={styles.linkText}>Go to the home screen</Text>
        </Link>
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Theme.spacing.xl,
    backgroundColor: Theme.colors.bg,
  },
  title: {
    fontSize: Theme.typography.title,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  link: {
    marginTop: Theme.spacing.lg,
    paddingVertical: Theme.spacing.lg,
  },
  linkText: {
    fontSize: Theme.typography.body,
    color: Theme.colors.telemetry,
  },
});
