import { Link, Stack } from 'expo-router';
import { StyleSheet, Text, View } from 'react-native';

import { Theme } from '@/constants/theme';
import { useT } from '@/contexts/LocaleContext';

export default function NotFoundScreen() {
  const t = useT();
  return (
    <>
      <Stack.Screen options={{ title: t('mobile.notFound.title') }} />
      <View style={styles.container}>
        <Text style={styles.title}>{t('mobile.notFound.body')}</Text>
        <Link href="/" style={styles.link}>
          <Text style={styles.linkText}>{t('mobile.notFound.home')}</Text>
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
