import { useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AlertTriangle, ArrowLeft } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { CrewRole, VehicleType } from '../../types';
import { useT } from '../../contexts/LocaleContext';
import { useSession } from '../../contexts/SessionContext';
import {
  signInWithCredentials,
  startLocalSession,
} from '../../services/session';

/** Without an account there is no server role, so the person says which seat. */
const SEATS: CrewRole[] = ['driver', 'observer'];

const VEHICLES: VehicleType[] = [
  'suv_4x4',
  'commercial_6w',
  'heavy_freight_12w',
  'car_sedan',
];

export default function SignInScreen() {
  const router = useRouter();
  const t = useT();
  const { adoptSession } = useSession();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [seat, setSeat] = useState<CrewRole>('driver');
  const [crewCode, setCrewCode] = useState('');
  const [vehicleType, setVehicleType] = useState<VehicleType>('commercial_6w');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localMode, setLocalMode] = useState(false);

  async function finish(result: Awaited<ReturnType<typeof startLocalSession>>) {
    if (!result.ok) {
      setError(result.reason);
      return;
    }
    adoptSession(result.session);
    router.replace('/(tabs)');
  }

  async function handleSignIn() {
    setBusy(true);
    setError(null);
    try {
      const result = await signInWithCredentials({
        email,
        password,
        crewCode,
        vehicleType,
      });
      await finish(result);
    } finally {
      setBusy(false);
    }
  }

  async function handleLocalStart() {
    setBusy(true);
    setError(null);
    try {
      const result = await startLocalSession({
        role: seat,
        displayName,
        crewCode,
        vehicleType,
      });
      await finish(result);
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <View style={styles.topBar}>
          <TouchableOpacity
            onPress={() => router.back()}
            hitSlop={12}
            accessibilityRole="button"
            accessibilityLabel={t('mobile.common.back')}
          >
            <ArrowLeft size={20} color={Theme.colors.text} />
          </TouchableOpacity>
        </View>

        <ScrollView
          style={styles.flex}
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.title}>
            {localMode
              ? t('mobile.signIn.titleLocal')
              : t('mobile.signIn.title')}
          </Text>
          <Text style={styles.subtitle}>
            {localMode
              ? t('mobile.signIn.subtitleLocal')
              : t('mobile.signIn.subtitle')}
          </Text>

          {error && (
            <View style={styles.errorBox}>
              <AlertTriangle size={15} color={Theme.colors.blocked} />
              <Text style={styles.errorText}>{t(error)}</Text>
            </View>
          )}

          {localMode ? (
            <>
              <Text style={styles.label}>{t('mobile.signIn.name')}</Text>
              <TextInput
                style={styles.input}
                value={displayName}
                onChangeText={setDisplayName}
                placeholder={t('mobile.signIn.namePlaceholder')}
                placeholderTextColor={Theme.colors.textDim}
                autoCapitalize="words"
              />

              <Text style={styles.label}>{t('mobile.signIn.seat')}</Text>
              <View style={styles.vehicleGrid}>
                {SEATS.map((option) => {
                  const active = seat === option;
                  return (
                    <TouchableOpacity
                      key={option}
                      style={[
                        styles.vehicleChip,
                        active && styles.vehicleChipActive,
                      ]}
                      onPress={() => setSeat(option)}
                      accessibilityRole="radio"
                      accessibilityState={{ selected: active }}
                    >
                      <Text
                        style={[
                          styles.vehicleChipText,
                          active && styles.vehicleChipTextActive,
                        ]}
                      >
                        {t(`mobile.seat.${option}`)}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
            </>
          ) : (
            <>
              <Text style={styles.label}>{t('mobile.signIn.email')}</Text>
              <TextInput
                style={styles.input}
                value={email}
                onChangeText={setEmail}
                placeholder="name@district.gov.in"
                placeholderTextColor={Theme.colors.textDim}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="email-address"
                textContentType="emailAddress"
              />

              <Text style={styles.label}>{t('mobile.signIn.password')}</Text>
              <TextInput
                style={styles.input}
                value={password}
                onChangeText={setPassword}
                placeholder={t('mobile.signIn.passwordPlaceholder')}
                placeholderTextColor={Theme.colors.textDim}
                secureTextEntry
                autoCapitalize="none"
                textContentType="password"
              />
            </>
          )}

          <Text style={styles.label}>{t('mobile.signIn.crewCode')}</Text>
          <TextInput
            style={[styles.input, styles.inputMono]}
            value={crewCode}
            onChangeText={setCrewCode}
            placeholder={t('mobile.signIn.crewCodePlaceholder')}
            placeholderTextColor={Theme.colors.textDim}
            autoCapitalize="characters"
            autoCorrect={false}
          />
          <Text style={styles.hint}>{t('mobile.signIn.crewCodeHint')}</Text>

          <Text style={styles.label}>{t('mobile.signIn.vehicle')}</Text>
          <View style={styles.vehicleGrid}>
            {VEHICLES.map((vehicle) => {
              const active = vehicleType === vehicle;
              return (
                <TouchableOpacity
                  key={vehicle}
                  style={[
                    styles.vehicleChip,
                    active && styles.vehicleChipActive,
                  ]}
                  onPress={() => setVehicleType(vehicle)}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: active }}
                >
                  <Text
                    style={[
                      styles.vehicleChipText,
                      active && styles.vehicleChipTextActive,
                    ]}
                  >
                    {t(`mobile.vehicle.${vehicle}`)}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <TouchableOpacity
            style={styles.primaryButton}
            onPress={localMode ? handleLocalStart : handleSignIn}
            disabled={busy}
            accessibilityRole="button"
          >
            {busy ? (
              <ActivityIndicator color="#FFFFFF" size="small" />
            ) : (
              <Text style={styles.primaryButtonText}>
                {localMode
                  ? t('mobile.signIn.continue')
                  : t('mobile.signIn.submit')}
              </Text>
            )}
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.switchModeButton}
            onPress={() => {
              setLocalMode(!localMode);
              setError(null);
            }}
            accessibilityRole="button"
          >
            <Text style={styles.switchModeText}>
              {localMode
                ? t('mobile.signIn.signInInstead')
                : t('mobile.signIn.tryLocal')}
            </Text>
          </TouchableOpacity>

          {localMode && (
            <View style={styles.localWarning}>
              <Text style={styles.localWarningBody}>
                {t('mobile.signIn.localWarning')}
              </Text>
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Theme.colors.bg },
  flex: { flex: 1 },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Theme.spacing.xl,
    paddingTop: Theme.spacing.sm,
    paddingBottom: Theme.spacing.md,
  },
  content: {
    paddingHorizontal: Theme.spacing.xl,
    paddingBottom: Theme.spacing.xxl,
  },
  title: {
    fontSize: 24,
    lineHeight: 30,
    fontWeight: '700',
    color: Theme.colors.text,
    marginBottom: 6,
  },
  subtitle: {
    fontSize: 13,
    lineHeight: 19,
    color: Theme.colors.textMuted,
    marginBottom: Theme.spacing.xl,
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 9,
    backgroundColor: Theme.colors.blockedBg,
    borderWidth: 1,
    borderColor: Theme.colors.blockedBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    marginBottom: Theme.spacing.lg,
  },
  errorText: {
    flex: 1,
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.blocked,
    fontWeight: '600',
  },
  label: {
    fontSize: 11,
    fontWeight: '800',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    marginBottom: 7,
    marginTop: Theme.spacing.md,
  },
  input: {
    backgroundColor: Theme.colors.surface,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    paddingHorizontal: Theme.spacing.lg,
    paddingVertical: 14,
    fontSize: 14,
    color: Theme.colors.text,
  },
  inputMono: { fontFamily: 'monospace', letterSpacing: 1 },
  hint: {
    fontSize: 11.5,
    lineHeight: 17,
    color: Theme.colors.textDim,
    marginTop: 7,
  },
  vehicleGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  vehicleChip: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: Theme.radius.sm,
    backgroundColor: Theme.colors.surface,
    borderWidth: 1,
    borderColor: Theme.colors.border,
  },
  vehicleChipActive: {
    backgroundColor: Theme.colors.telemetryBg,
    borderColor: Theme.colors.telemetry,
  },
  vehicleChipText: {
    fontSize: 12.5,
    fontWeight: '700',
    color: Theme.colors.textMuted,
  },
  vehicleChipTextActive: { color: Theme.colors.telemetry },
  primaryButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: Theme.colors.brand,
    paddingVertical: 16,
    borderRadius: Theme.radius.md,
    marginTop: Theme.spacing.xl,
    minHeight: 52,
  },
  primaryButtonText: { fontSize: 14, fontWeight: '800', color: '#FFFFFF' },
  switchModeButton: { alignItems: 'center', paddingVertical: Theme.spacing.lg },
  switchModeText: {
    fontSize: 13,
    fontWeight: '700',
    color: Theme.colors.telemetry,
  },
  localWarning: {
    backgroundColor: Theme.colors.cautionBg,
    borderWidth: 1,
    borderColor: Theme.colors.cautionBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.lg,
  },
  localWarningTitle: {
    fontSize: 12.5,
    fontWeight: '800',
    color: Theme.colors.caution,
    marginBottom: 5,
  },
  localWarningBody: {
    fontSize: 12,
    lineHeight: 18,
    color: Theme.colors.textMuted,
  },
});
