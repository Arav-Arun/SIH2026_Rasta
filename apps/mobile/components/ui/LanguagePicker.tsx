import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';

import { Theme } from '../../constants/theme';
import { useLocale } from '../../contexts/LocaleContext';
import { catalogueMeta, hasAppStrings, LOCALES } from '../../services/i18n';

/**
 * The app's languages by their own names. A machine draft says so: until a
 * native speaker has reviewed it, some text may be wrong or still in English.
 */
export function LanguagePicker() {
  const { locale, setLocale, t } = useLocale();
  const reviewed = catalogueMeta(locale).reviewed;

  return (
    <View style={styles.wrap}>
      <Text style={styles.label}>{t('mobile.language.label')}</Text>
      <View style={styles.row} accessibilityRole="radiogroup">
        {LOCALES.map((code) => {
          const selected = code === locale;
          return (
            <TouchableOpacity
              key={code}
              onPress={() => setLocale(code)}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              accessibilityLabel={catalogueMeta(code).label}
              style={[styles.chip, selected && styles.chipSelected]}
            >
              <Text
                style={[styles.chipText, selected && styles.chipTextSelected]}
              >
                {catalogueMeta(code).nativeLabel}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
      {reviewed ? null : (
        <Text style={styles.note}>
          {hasAppStrings(locale)
            ? t('mobile.language.unreviewed')
            : t('mobile.language.notTranslated')}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Theme.spacing.sm },
  label: {
    fontSize: Theme.typography.caption,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: Theme.spacing.sm },
  chip: {
    borderWidth: 1,
    borderColor: Theme.colors.border,
    backgroundColor: Theme.colors.surface,
    borderRadius: Theme.radius.full,
    paddingHorizontal: Theme.spacing.md,
    paddingVertical: 6,
  },
  chipSelected: {
    borderColor: Theme.colors.brand,
    backgroundColor: Theme.colors.telemetryBg,
  },
  chipText: { fontSize: 14, color: Theme.colors.text },
  chipTextSelected: { color: Theme.colors.brand, fontWeight: '700' },
  note: {
    fontSize: Theme.typography.caption,
    lineHeight: 17,
    color: Theme.colors.caution,
  },
});
