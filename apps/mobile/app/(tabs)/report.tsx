import { useRef, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  Alert,
} from 'react-native';
import {
  AlertTriangle,
  Camera,
  MapPin,
  Send,
  CheckCircle,
  Mountain,
  Waves,
  Hammer,
  type LucideIcon,
} from 'lucide-react-native';
import { useRouter } from 'expo-router';
import { Theme } from '../../constants/theme';
import { CaptureDeliveryReceipt, HazardCategory, RiskTier } from '../../types';
import { getNetworkMode } from '../../services/offlineStorage';
import {
  capturePhoto,
  captureGpsFix,
  publishObservation,
} from '../../services/capturePipeline';
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';
import { MinimalCard } from '../../components/ui/MinimalCard';
import { ReportSuccessModal } from '../../components/capture/ReportSuccessModal';

export default function ReportScreen() {
  const router = useRouter();
  const crew = useCrew();
  const t = useT();
  const scrollRef = useRef<ScrollView>(null);
  const [selectedCategory, setSelectedCategory] =
    useState<HazardCategory>('landslide');
  const [selectedCorridor, setSelectedCorridor] = useState<string>('NH-6');
  const [locationName, setLocationName] = useState<string>('');
  const [notes, setNotes] = useState<string>('');
  const [severity, setSeverity] = useState<RiskTier>('high');
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [photoRefusal, setPhotoRefusal] = useState<string | null>(null);
  const [submittedAlert, setSubmittedAlert] = useState<string | null>(null);
  const [showSuccessModal, setShowSuccessModal] = useState<boolean>(false);
  const [lastReceipt, setLastReceipt] = useState<CaptureDeliveryReceipt | null>(
    null,
  );

  const categories: { id: HazardCategory; icon: LucideIcon }[] = [
    { id: 'landslide', icon: Mountain },
    { id: 'boulder_fall', icon: AlertTriangle },
    { id: 'bridge_overwash', icon: Waves },
    { id: 'road_crack', icon: Hammer },
    { id: 'flash_flood', icon: Waves },
  ];

  const corridors = ['NH-6', 'NH-10', 'NH-29', 'NH-27'];

  async function attachPhoto() {
    setPhotoRefusal(null);
    const result = await capturePhoto();
    if (result.ok) {
      setPhotoUri(result.uri);
      return;
    }
    setPhotoUri(null);
    setPhotoRefusal(result.reason);
  }

  /** File the report with the position the device actually has. */
  async function handleSubmit() {
    setSubmitting(true);
    setSubmittedAlert(null);

    try {
      const catLabel = t(`mobile.category.${selectedCategory}`);
      const gps = await captureGpsFix();
      if (!gps.ok) {
        Alert.alert(
          t('mobile.report.noPositionTitle'),
          t('mobile.report.noPositionBody', { reason: gps.reason }),
        );
        return;
      }

      const mode = await getNetworkMode();
      const { receipt } = await publishObservation(
        {
          category: selectedCategory,
          categoryLabel: catLabel,
          corridorCode: selectedCorridor,
          locationName: locationName.trim() || 'Not described',
          severity,
          notes: notes.trim() || undefined,
          photoUri: photoUri ?? undefined,
          latitude: gps.fix.latitude,
          longitude: gps.fix.longitude,
          altitudeMeters: gps.fix.altitudeMeters,
          accuracyMeters: gps.fix.accuracyMeters,
          fixTakenAt: gps.fix.takenAt,
        },
        crew,
      );

      // What the receipt says happened, rather than a claim that it did.
      setLastReceipt(receipt);
      setShowSuccessModal(true);

      setSubmittedAlert(
        receipt.controlRoomDb
          ? t('mobile.report.sentAlert')
          : mode === 'dead_zone'
            ? t('mobile.report.heldAlert')
            : t('mobile.report.savedAlert', {
                note: receipt.notes[0] ? t(receipt.notes[0]) : '',
              }).trim(),
      );

      setNotes('');
      setPhotoUri(null);
      setLocationName('');

      setTimeout(() => {
        scrollRef.current?.scrollTo({ y: 0, animated: true });
      }, 100);
    } catch {
      Alert.alert(t('mobile.report.errorTitle'), t('mobile.report.saveFailed'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View style={styles.container}>
      <ReportSuccessModal
        visible={showSuccessModal}
        receipt={lastReceipt}
        categoryLabel={t(`mobile.category.${selectedCategory}`)}
        corridorCode={selectedCorridor}
        onDismiss={() => {
          setShowSuccessModal(false);
          scrollRef.current?.scrollTo({ y: 0, animated: true });
        }}
        onGoHome={() => router.push('/(tabs)')}
      />

      <ScrollView
        ref={scrollRef}
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
      >
        {submittedAlert && (
          <View style={styles.alertNotification}>
            <View style={styles.alertTopRow}>
              <CheckCircle size={18} color={Theme.colors.passable} />
              <Text style={styles.alertNotificationText}>{submittedAlert}</Text>
            </View>
            <TouchableOpacity
              style={styles.viewOnRadarBtn}
              onPress={() => router.push('/(tabs)')}
              accessibilityRole="button"
            >
              <Text style={styles.viewOnRadarText}>
                {t('mobile.report.seeReports')}
              </Text>
            </TouchableOpacity>
          </View>
        )}

        <Text style={styles.sectionTitle}>{t('mobile.receipt.hazard')}</Text>
        <View style={styles.categoryGrid}>
          {categories.map((cat) => {
            const isSelected = selectedCategory === cat.id;
            const Icon = cat.icon;

            return (
              <TouchableOpacity
                key={cat.id}
                style={[
                  styles.categoryCard,
                  isSelected && styles.categoryCardSelected,
                ]}
                onPress={() => setSelectedCategory(cat.id)}
              >
                <Icon
                  size={20}
                  color={
                    isSelected ? Theme.colors.telemetry : Theme.colors.textMuted
                  }
                />
                <Text
                  style={[
                    styles.categoryText,
                    isSelected && styles.categoryTextSelected,
                  ]}
                >
                  {t(`mobile.category.${cat.id}`)}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.sectionTitle}>{t('mobile.report.corridor')}</Text>
        <View style={styles.corridorRow}>
          {corridors.map((c) => (
            <TouchableOpacity
              key={c}
              style={[
                styles.corridorBtn,
                selectedCorridor === c && styles.corridorBtnSelected,
              ]}
              onPress={() => setSelectedCorridor(c)}
            >
              <Text
                style={[
                  styles.corridorBtnText,
                  selectedCorridor === c && styles.corridorBtnTextSelected,
                ]}
              >
                {c}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Location */}
        <MinimalCard style={styles.locationCard}>
          <View style={styles.gpsRow}>
            <MapPin size={14} color={Theme.colors.telemetry} />
            <Text style={styles.gpsCoordText}>
              {t('mobile.report.gpsOnSend')}
            </Text>
          </View>

          <Text style={styles.inputLabel}>{t('mobile.report.landmark')}</Text>
          <TextInput
            style={styles.textInput}
            value={locationName}
            onChangeText={setLocationName}
            placeholder={t('mobile.report.landmarkPlaceholder')}
            placeholderTextColor={Theme.colors.textDim}
          />
        </MinimalCard>

        <Text style={styles.sectionTitle}>{t('mobile.report.severity')}</Text>
        <View style={styles.severityRow}>
          {(['moderate', 'high', 'critical'] as RiskTier[]).map((lvl) => (
            <TouchableOpacity
              key={lvl}
              style={[
                styles.severityBtn,
                severity === lvl &&
                  (lvl === 'critical'
                    ? styles.sevCriticalActive
                    : lvl === 'high'
                      ? styles.sevHighActive
                      : styles.sevModActive),
              ]}
              onPress={() => setSeverity(lvl)}
            >
              <Text
                style={[
                  styles.severityBtnText,
                  severity === lvl && styles.severityBtnTextActive,
                ]}
              >
                {t(`mobile.severity.${lvl}`).toUpperCase()}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* The camera. The button used to toggle a boolean and file a photo
         * path for an image that was never taken. */}
        <MinimalCard style={styles.photoCard}>
          <View style={styles.photoRow}>
            <View style={styles.photoInfo}>
              <Text style={styles.photoTitle}>
                {t('mobile.report.photoTitle')}
              </Text>
              <Text style={styles.photoSub}>
                {photoUri
                  ? t('mobile.report.photoAttached')
                  : photoRefusal
                    ? t(photoRefusal)
                    : t('mobile.report.photoOptional')}
              </Text>
            </View>

            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={
                photoUri
                  ? t('mobile.report.retakeLabel')
                  : t('mobile.report.takeLabel')
              }
              style={[
                styles.cameraBtn,
                photoUri ? styles.cameraBtnAttached : null,
              ]}
              onPress={() => void attachPhoto()}
            >
              <Camera size={18} color="#FFF" />
              <Text style={styles.cameraBtnText}>
                {photoUri
                  ? t('mobile.report.retake')
                  : t('mobile.tabs.capture')}
              </Text>
            </TouchableOpacity>
          </View>
        </MinimalCard>

        <Text style={styles.sectionTitle}>{t('mobile.report.notes')}</Text>
        <TextInput
          style={[styles.textInput, styles.textArea]}
          value={notes}
          onChangeText={setNotes}
          placeholder={t('mobile.report.notesPlaceholder')}
          placeholderTextColor={Theme.colors.textDim}
          multiline
          numberOfLines={3}
        />

        {/* Big Submit Button */}
        <TouchableOpacity
          style={styles.submitBtn}
          onPress={handleSubmit}
          disabled={submitting}
        >
          <Send size={18} color="#FFF" />
          <Text style={styles.submitBtnText}>
            {submitting ? t('mobile.routes.saving') : t('mobile.report.send')}
          </Text>
        </TouchableOpacity>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Theme.colors.bg,
  },
  scroll: {
    flex: 1,
  },
  scrollContent: {
    padding: Theme.spacing.md,
    paddingBottom: Theme.spacing.xxl,
  },
  alertNotification: {
    backgroundColor: Theme.colors.passableBg,
    borderColor: Theme.colors.passableBorder,
    borderWidth: 1,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    marginBottom: Theme.spacing.md,
  },
  alertTopRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  alertNotificationText: {
    flex: 1,
    fontSize: 12,
    color: Theme.colors.text,
    lineHeight: 16,
    fontWeight: '600',
  },
  viewOnRadarBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderColor: Theme.colors.border,
    borderWidth: 1,
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: Theme.radius.md,
  },
  viewOnRadarText: {
    fontSize: 13,
    fontWeight: '600',
    color: Theme.colors.brand,
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: '800',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginTop: 10,
    marginBottom: 8,
  },
  categoryGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 10,
  },
  categoryCard: {
    width: '48%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Theme.colors.surface,
    borderColor: Theme.colors.border,
    borderWidth: 1,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    minHeight: 52,
  },
  categoryCardSelected: {
    borderColor: Theme.colors.telemetry,
    backgroundColor: Theme.colors.telemetryBg,
  },
  categoryText: {
    fontSize: 12,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    flex: 1,
  },
  categoryTextSelected: {
    color: Theme.colors.text,
  },
  corridorRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 10,
  },
  corridorBtn: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 10,
    backgroundColor: Theme.colors.surface,
    borderColor: Theme.colors.border,
    borderWidth: 1,
    borderRadius: Theme.radius.sm,
  },
  corridorBtnSelected: {
    borderColor: Theme.colors.telemetry,
    backgroundColor: Theme.colors.telemetryBg,
  },
  corridorBtnText: {
    fontSize: 13,
    fontWeight: '800',
    fontFamily: 'monospace',
    color: Theme.colors.textMuted,
  },
  corridorBtnTextSelected: {
    color: Theme.colors.telemetry,
  },
  locationCard: {
    marginBottom: 10,
  },
  gpsRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginBottom: 10,
  },
  gpsCoordText: {
    fontSize: 12,
    color: Theme.colors.textMuted,
  },
  inputLabel: {
    fontSize: 10,
    color: Theme.colors.textDim,
    fontWeight: '700',
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  textInput: {
    backgroundColor: Theme.colors.surfaceElevated,
    borderColor: Theme.colors.border,
    borderWidth: 1,
    borderRadius: Theme.radius.sm,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: Theme.colors.text,
    fontSize: 13,
  },
  textArea: {
    height: 70,
    textAlignVertical: 'top',
    marginBottom: 16,
  },
  severityRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 10,
  },
  severityBtn: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 10,
    backgroundColor: Theme.colors.surface,
    borderColor: Theme.colors.border,
    borderWidth: 1,
    borderRadius: Theme.radius.sm,
  },
  sevModActive: {
    backgroundColor: Theme.colors.cautionBg,
    borderColor: Theme.colors.cautionBorder,
  },
  sevHighActive: {
    backgroundColor: Theme.colors.blockedBg,
    borderColor: Theme.colors.blockedBorder,
  },
  sevCriticalActive: {
    backgroundColor: 'rgba(239, 68, 68, 0.3)',
    borderColor: Theme.colors.blocked,
  },
  severityBtnText: {
    fontSize: 11,
    fontWeight: '800',
    color: Theme.colors.textMuted,
  },
  severityBtnTextActive: {
    color: Theme.colors.text,
  },
  photoCard: {
    marginBottom: 10,
  },
  photoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  photoInfo: {
    flex: 1,
  },
  photoTitle: {
    fontSize: 12,
    fontWeight: '800',
    color: Theme.colors.text,
  },
  photoSub: {
    fontSize: 10,
    color: Theme.colors.textMuted,
    marginTop: 2,
  },
  cameraBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: Theme.colors.brand,
    paddingHorizontal: 14,
    paddingVertical: 9,
    borderRadius: Theme.radius.sm,
  },
  cameraBtnAttached: {
    backgroundColor: Theme.colors.passable,
  },
  cameraBtnText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#FFF',
  },
  submitBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: Theme.colors.brand,
    paddingVertical: 16,
    borderRadius: Theme.radius.md,
    marginTop: 8,
  },
  submitBtnText: {
    fontSize: 14,
    fontWeight: '900',
    color: '#FFF',
    letterSpacing: 0.5,
  },
});
