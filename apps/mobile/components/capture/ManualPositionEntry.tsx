import { useState } from 'react';
import {
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { MapPin } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { useT } from '../../contexts/LocaleContext';

/**
 * Fallback for when the device cannot produce a fix, no sky view under a ridge,
 * a refused permission, a failing GPS chip.
 */
export function ManualPositionEntry({
  onSubmit,
  onCancel,
}: {
  onSubmit: (position: { latitude: number; longitude: number }) => void;
  onCancel: () => void;
}) {
  const t = useT();
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [error, setError] = useState<string | null>(null);

  function handleSubmit() {
    const lat = Number(latitude.trim());
    const lon = Number(longitude.trim());

    if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
      setError('mobile.manual.badLatitude');
      return;
    }
    if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
      setError('mobile.manual.badLongitude');
      return;
    }

    setError(null);
    onSubmit({ latitude: lat, longitude: lon });
  }

  return (
    <View style={styles.container}>
      <View style={styles.headRow}>
        <MapPin size={15} color={Theme.colors.caution} />
        <Text style={styles.title}>{t('mobile.manual.title')}</Text>
      </View>
      <Text style={styles.body}>{t('mobile.manual.body')}</Text>

      <View style={styles.fieldRow}>
        <View style={styles.field}>
          <Text style={styles.label}>{t('mobile.manual.latitude')}</Text>
          <TextInput
            style={styles.input}
            value={latitude}
            onChangeText={setLatitude}
            placeholder="25.5788"
            placeholderTextColor={Theme.colors.textDim}
            keyboardType="numbers-and-punctuation"
            accessibilityLabel={t('mobile.manual.latitude')}
          />
        </View>
        <View style={styles.field}>
          <Text style={styles.label}>{t('mobile.manual.longitude')}</Text>
          <TextInput
            style={styles.input}
            value={longitude}
            onChangeText={setLongitude}
            placeholder="91.8933"
            placeholderTextColor={Theme.colors.textDim}
            keyboardType="numbers-and-punctuation"
            accessibilityLabel={t('mobile.manual.longitude')}
          />
        </View>
      </View>

      {error && <Text style={styles.error}>{t(error)}</Text>}

      <View style={styles.actions}>
        <TouchableOpacity
          style={styles.cancel}
          onPress={onCancel}
          accessibilityRole="button"
        >
          <Text style={styles.cancelText}>{t('confirm.cancel')}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.submit}
          onPress={handleSubmit}
          accessibilityRole="button"
        >
          <Text style={styles.submitText}>{t('mobile.manual.use')}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    backgroundColor: Theme.colors.cautionBg,
    borderWidth: 1,
    borderColor: Theme.colors.cautionBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.lg,
    marginTop: Theme.spacing.md,
  },
  headRow: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  title: { fontSize: 13, fontWeight: '800', color: Theme.colors.caution },
  body: {
    fontSize: 12,
    lineHeight: 17,
    color: Theme.colors.textMuted,
    marginTop: 4,
    marginBottom: Theme.spacing.md,
  },
  fieldRow: { flexDirection: 'row', gap: Theme.spacing.md },
  field: { flex: 1 },
  label: {
    fontSize: 10.5,
    fontWeight: '800',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: 5,
  },
  input: {
    backgroundColor: Theme.colors.surface,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.sm,
    paddingHorizontal: Theme.spacing.md,
    paddingVertical: 11,
    fontSize: 14,
    fontFamily: 'monospace',
    color: Theme.colors.text,
  },
  error: {
    fontSize: 12,
    color: Theme.colors.blocked,
    fontWeight: '600',
    marginTop: Theme.spacing.md,
  },
  actions: {
    flexDirection: 'row',
    gap: Theme.spacing.md,
    marginTop: Theme.spacing.lg,
  },
  cancel: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 12,
    borderRadius: Theme.radius.sm,
    borderWidth: 1,
    borderColor: Theme.colors.border,
  },
  cancelText: {
    fontSize: 13,
    fontWeight: '700',
    color: Theme.colors.textMuted,
  },
  submit: {
    flex: 2,
    alignItems: 'center',
    paddingVertical: 12,
    borderRadius: Theme.radius.sm,
    backgroundColor: Theme.colors.brand,
  },
  submitText: { fontSize: 13, fontWeight: '800', color: '#FFFFFF' },
});
