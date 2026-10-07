import { useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Camera, MapPinned, WifiOff } from 'lucide-react-native';
import { BrandMark } from '../../components/ui/BrandMark';
import { LanguagePicker } from '../../components/ui/LanguagePicker';
import { useT } from '../../contexts/LocaleContext';
import { Theme } from '../../constants/theme';

const PANELS = [
  {
    key: 'map',
    title: 'mobile.onboarding.mapTitle',
    body: 'mobile.onboarding.mapBody',
    icon: MapPinned,
  },
  {
    key: 'capture',
    title: 'mobile.onboarding.captureTitle',
    body: 'mobile.onboarding.captureBody',
    icon: Camera,
  },
  {
    key: 'offline',
    title: 'mobile.onboarding.offlineTitle',
    body: 'mobile.onboarding.offlineBody',
    icon: WifiOff,
  },
];

export default function OnboardingIntro() {
  const router = useRouter();
  const t = useT();
  const [index, setIndex] = useState(0);
  const panel = PANELS[index];
  const Icon = panel.icon;
  const isLast = index === PANELS.length - 1;

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <View style={styles.topBar}>
        <BrandMark />
        <TouchableOpacity
          onPress={() => router.push('/onboarding/sign-in')}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel={t('mobile.onboarding.skipLabel')}
        >
          <Text style={styles.skip}>{t('mobile.onboarding.skip')}</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.body}>
        {/* A fixed-height block, so the icon and title sit at the same height
            on every page whatever the length of the text. */}
        <View style={styles.panel}>
          <Icon size={28} color={Theme.colors.brand} />
          <Text style={styles.title}>{t(panel.title)}</Text>
          <Text style={styles.text}>{t(panel.body)}</Text>
        </View>
        {/* Chosen first, so everything after reads in it. */}
        {index === 0 ? <LanguagePicker /> : null}
      </View>

      <View style={styles.footer}>
        <View
          style={styles.dots}
          accessible
          accessibilityLabel={t('mobile.onboarding.page', {
            page: index + 1,
            total: PANELS.length,
          })}
        >
          {PANELS.map((item, i) => (
            <View
              key={item.key}
              style={[styles.dot, i === index && styles.dotActive]}
            />
          ))}
        </View>

        <TouchableOpacity
          style={styles.button}
          onPress={() =>
            isLast ? router.push('/onboarding/sign-in') : setIndex(index + 1)
          }
          accessibilityRole="button"
        >
          <Text style={styles.buttonText}>
            {isLast
              ? t('mobile.onboarding.getStarted')
              : t('mobile.onboarding.next')}
          </Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Theme.colors.bg },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Theme.spacing.xl,
    paddingVertical: Theme.spacing.md,
  },
  skip: { fontSize: 14, color: Theme.colors.textMuted },
  body: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: Theme.spacing.xl,
  },
  panel: { minHeight: 240 },
  title: {
    fontSize: 26,
    lineHeight: 32,
    fontWeight: '700',
    color: Theme.colors.text,
    marginTop: Theme.spacing.xl,
    marginBottom: Theme.spacing.md,
  },
  text: { fontSize: 15, lineHeight: 22, color: Theme.colors.textMuted },
  footer: {
    paddingHorizontal: Theme.spacing.xl,
    paddingBottom: Theme.spacing.lg,
    gap: Theme.spacing.lg,
  },
  dots: { flexDirection: 'row', gap: 6, justifyContent: 'center' },
  dot: {
    width: 6,
    height: 6,
    borderRadius: Theme.radius.full,
    backgroundColor: Theme.colors.border,
  },
  dotActive: { backgroundColor: Theme.colors.brand },
  button: {
    alignItems: 'center',
    backgroundColor: Theme.colors.brand,
    paddingVertical: 14,
    borderRadius: Theme.radius.md,
  },
  buttonText: { fontSize: 15, fontWeight: '700', color: '#FFFFFF' },
});
