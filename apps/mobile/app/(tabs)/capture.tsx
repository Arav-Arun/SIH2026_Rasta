import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  AlertTriangle,
  Camera,
  Crosshair,
  Hammer,
  Mountain,
  RefreshCw,
  Send,
  Waves,
  X,
  type LucideIcon,
} from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { CaptureDeliveryReceipt, HazardCategory, RiskTier } from '../../types';
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';
import {
  GpsFix,
  captureGpsFix,
  capturePhoto,
  publishObservation,
} from '../../services/capturePipeline';
import { useRouter } from 'expo-router';
import { MinimalCard } from '../../components/ui/MinimalCard';
import { DeliveryReceiptCard } from '../../components/capture/DeliveryReceiptCard';
import { ReportSuccessModal } from '../../components/capture/ReportSuccessModal';
import { ManualPositionEntry } from '../../components/capture/ManualPositionEntry';
import {
  type PositionSource,
  clearDraft,
  loadDraft,
  saveDraft,
} from '../../services/reportDraft';

const CATEGORIES: { id: HazardCategory; icon: LucideIcon }[] = [
  { id: 'landslide', icon: Mountain },
  { id: 'boulder_fall', icon: AlertTriangle },
  { id: 'bridge_overwash', icon: Waves },
  { id: 'road_crack', icon: Hammer },
  { id: 'flash_flood', icon: Waves },
  { id: 'heavy_jam', icon: AlertTriangle },
];

const CORRIDORS = ['NH-6', 'NH-10', 'NH-29', 'NH-27'];

export default function CaptureScreen() {
  const router = useRouter();
  const crew = useCrew();
  const t = useT();
  const scrollRef = useRef<ScrollView>(null);

  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [fix, setFix] = useState<GpsFix | null>(null);
  const [gpsError, setGpsError] = useState<string | null>(null);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);

  const [category, setCategory] = useState<HazardCategory>('landslide');
  const [corridor, setCorridor] = useState('NH-6');
  const [landmark, setLandmark] = useState('');
  const [severity, setSeverity] = useState<RiskTier>('high');
  const [notes, setNotes] = useState('');

  const [sending, setSending] = useState(false);
  const [receipt, setReceipt] = useState<CaptureDeliveryReceipt | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [showSuccessModal, setShowSuccessModal] = useState(false);

  const [positionSource, setPositionSource] = useState<PositionSource | null>(
    null,
  );
  const [showManualEntry, setShowManualEntry] = useState(false);
  const [draftRestored, setDraftRestored] = useState(false);
  // Nothing is persisted until the stored draft has been read, so restoring
  // cannot be overwritten by the empty initial state.
  const draftLoaded = useRef(false);

  // Restore whatever survived a force-close, once, on mount.
  useEffect(() => {
    let cancelled = false;
    void loadDraft().then((draft) => {
      if (cancelled) return;
      if (draft) {
        setCategory(draft.category);
        setCorridor(draft.corridorCode);
        setLandmark(draft.landmark);
        setSeverity(draft.severity);
        setNotes(draft.notes);
        setPhotoUri(draft.photoUri);
        setPositionSource(draft.positionSource);
        if (draft.latitude !== null && draft.longitude !== null) {
          setFix({
            latitude: draft.latitude,
            longitude: draft.longitude,
            accuracyMeters: draft.accuracyMeters ?? undefined,
            altitudeMeters: draft.altitudeMeters ?? undefined,
            takenAt: draft.fixTakenAt ?? new Date().toISOString(),
          });
        }
        setDraftRestored(true);
      }
      draftLoaded.current = true;
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Autosave. Writing on every change is what makes a force-close survivable.
  useEffect(() => {
    if (!draftLoaded.current) return;
    void saveDraft({
      category,
      corridorCode: corridor,
      landmark,
      severity,
      notes,
      photoUri,
      latitude: fix?.latitude ?? null,
      longitude: fix?.longitude ?? null,
      accuracyMeters: fix?.accuracyMeters ?? null,
      altitudeMeters: fix?.altitudeMeters ?? null,
      fixTakenAt: fix?.takenAt ?? null,
      positionSource,
    });
  }, [
    category,
    corridor,
    landmark,
    severity,
    notes,
    photoUri,
    fix,
    positionSource,
  ]);

  const getFix = useCallback(async () => {
    setLocating(true);
    setGpsError(null);
    const result = await captureGpsFix();
    setLocating(false);

    if (!result.ok) {
      setGpsError(result.reason);
      setFix(null);
      setPositionSource(null);
      // A refused or failed fix is not the end of the report: offer the manual
      // fallback immediately rather than leaving the officer stuck.
      setShowManualEntry(true);
      return;
    }
    setFix(result.fix);
    setPositionSource('device_gps');
    setShowManualEntry(false);
  }, []);

  const takePhoto = useCallback(async () => {
    setPhotoError(null);
    const result = await capturePhoto();

    if (!result.ok) {
      if (!result.cancelled) setPhotoError(result.reason);
      return;
    }

    setPhotoUri(result.uri);
    setReceipt(null);

    // A photo without a position cannot be placed on the map, so take the fix
    // at the same moment rather than whenever the form is submitted.
    if (!fix) void getFix();
  }, [fix, getFix]);

  const useManualPosition = useCallback(
    (position: { latitude: number; longitude: number }) => {
      setFix({
        latitude: position.latitude,
        longitude: position.longitude,
        takenAt: new Date().toISOString(),
      });
      setPositionSource('manual_pin');
      setShowManualEntry(false);
      setGpsError(null);
    },
    [],
  );

  async function handleSend() {
    setSending(true);
    setReceipt(null);
    setSendError(null);
    try {
      const label = t(`mobile.category.${category}`);

      const { receipt: delivery } = await publishObservation(
        {
          category,
          categoryLabel: label,
          corridorCode: corridor,
          // Read by the control room, so it stays in English.
          locationName: landmark.trim() || `${corridor}, landmark not given`,
          severity,
          notes: notes.trim() || undefined,
          photoUri: photoUri ?? undefined,
          latitude: fix?.latitude ?? null,
          longitude: fix?.longitude ?? null,
          altitudeMeters: fix?.altitudeMeters,
          accuracyMeters: fix?.accuracyMeters,
          fixTakenAt: fix?.takenAt,
          positionSource: positionSource ?? undefined,
        },
        crew,
      );

      setReceipt(delivery);
      setShowSuccessModal(true);
      // The report is saved in the outbox now, so the draft is spent.
      await clearDraft();
      setPhotoUri(null);
      setNotes('');
      setLandmark('');
      setFix(null);
      setPositionSource(null);
      setDraftRestored(false);
      setTimeout(() => {
        scrollRef.current?.scrollTo({ y: 0, animated: true });
      }, 100);
    } catch (err) {
      // Nothing was saved: the draft stays, so nothing typed is lost.
      setSendError(
        err instanceof Error ? err.message : 'mobile.report.saveFailed',
      );
    } finally {
      setSending(false);
    }
  }

  // The control room cannot accept a report it cannot place, so Send waits
  // for a position as well as a photo.
  const canSend = !!photoUri && !!fix && !sending;

  return (
    <View style={styles.container}>
      <ReportSuccessModal
        visible={showSuccessModal}
        receipt={receipt}
        categoryLabel={t(`mobile.category.${category}`)}
        corridorCode={corridor}
        onDismiss={() => {
          setShowSuccessModal(false);
          scrollRef.current?.scrollTo({ y: 0, animated: true });
        }}
        onGoHome={() => router.push('/(tabs)')}
      />

      <ScrollView
        ref={scrollRef}
        style={styles.scroll}
        contentContainerStyle={styles.content}
      >
        {crew.mode === 'local_only' && (
          <View style={styles.localBanner}>
            <Text style={styles.localBannerText}>
              {t('mobile.captureTab.localBanner')}
            </Text>
          </View>
        )}

        {draftRestored && !receipt && (
          <View style={styles.draftBanner}>
            <Text style={styles.draftBannerText}>
              {t('mobile.captureTab.draftRestored')}
            </Text>
            <TouchableOpacity
              onPress={async () => {
                await clearDraft();
                setPhotoUri(null);
                setNotes('');
                setLandmark('');
                setFix(null);
                setPositionSource(null);
                setDraftRestored(false);
              }}
              accessibilityRole="button"
            >
              <Text style={styles.draftBannerAction}>
                {t('mobile.captureTab.startOver')}
              </Text>
            </TouchableOpacity>
          </View>
        )}

        {receipt && (
          <DeliveryReceiptCard
            receipt={receipt}
            onDismiss={() => setReceipt(null)}
          />
        )}

        {/* 1. Photo. The whole report hangs off this, so it leads. */}
        <Text style={styles.stepLabel}>{t('mobile.captureTab.photo')}</Text>

        {photoUri ? (
          <View style={styles.photoPreviewWrap}>
            <Image source={{ uri: photoUri }} style={styles.photoPreview} />
            <View style={styles.photoActions}>
              <TouchableOpacity
                style={styles.photoActionBtn}
                onPress={takePhoto}
                accessibilityRole="button"
              >
                <RefreshCw size={14} color={Theme.colors.text} />
                <Text style={styles.photoActionText}>
                  {t('mobile.report.retake')}
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.photoActionBtn}
                onPress={() => setPhotoUri(null)}
                accessibilityRole="button"
              >
                <X size={14} color={Theme.colors.blocked} />
                <Text
                  style={[
                    styles.photoActionText,
                    { color: Theme.colors.blocked },
                  ]}
                >
                  {t('mobile.captureTab.remove')}
                </Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : (
          <TouchableOpacity
            style={styles.cameraTile}
            onPress={takePhoto}
            accessibilityRole="button"
            accessibilityLabel={t('mobile.captureTab.cameraLabel')}
          >
            <View style={styles.cameraIconPlate}>
              <Camera size={26} color="#FFFFFF" />
            </View>
            <Text style={styles.cameraTileTitle}>
              {t('mobile.report.takeLabel')}
            </Text>
            <Text style={styles.cameraTileSub}>
              {t('mobile.captureTab.photoRequired')}
            </Text>
          </TouchableOpacity>
        )}

        {photoError && <Text style={styles.inlineError}>{t(photoError)}</Text>}

        {/* 2. Position */}
        <Text style={styles.stepLabel}>{t('mobile.captureTab.position')}</Text>
        <MinimalCard style={styles.gpsCard}>
          {fix ? (
            <>
              <View style={styles.gpsRow}>
                <Crosshair size={15} color={Theme.colors.telemetry} />
                <Text style={styles.gpsCoords}>
                  {t('mobile.captureTab.coordinates', {
                    latitude: fix.latitude.toFixed(5),
                    longitude: fix.longitude.toFixed(5),
                  })}
                </Text>
              </View>
              <Text style={styles.gpsMeta}>
                {fix.accuracyMeters !== undefined
                  ? t('mobile.captureTab.accuracy', {
                      metres: fix.accuracyMeters,
                    })
                  : t('mobile.captureTab.noAccuracy')}
                {fix.altitudeMeters !== undefined
                  ? `, ${t('mobile.captureTab.altitude', {
                      metres: fix.altitudeMeters,
                    })}`
                  : ''}
              </Text>
              <Text style={styles.gpsMeta}>
                {positionSource === 'manual_pin'
                  ? t('mobile.captureTab.byHand')
                  : t('mobile.captureTab.fixTaken', {
                      time: new Date(fix.takenAt).toLocaleTimeString(),
                    })}
              </Text>
              <TouchableOpacity
                style={styles.gpsRefresh}
                onPress={getFix}
                accessibilityRole="button"
              >
                <RefreshCw size={13} color={Theme.colors.telemetry} />
                <Text style={styles.gpsRefreshText}>
                  {t('mobile.captureTab.newFix')}
                </Text>
              </TouchableOpacity>
            </>
          ) : (
            <>
              <Text style={styles.gpsEmpty}>
                {t(gpsError ?? 'mobile.captureTab.noPosition')}
              </Text>
              <TouchableOpacity
                style={styles.gpsButton}
                onPress={getFix}
                disabled={locating}
                accessibilityRole="button"
              >
                {locating ? (
                  <ActivityIndicator size="small" color="#FFFFFF" />
                ) : (
                  <>
                    <Crosshair size={15} color="#FFFFFF" />
                    <Text style={styles.gpsButtonText}>
                      {t('mobile.captureTab.getFix')}
                    </Text>
                  </>
                )}
              </TouchableOpacity>

              {!showManualEntry && (
                <TouchableOpacity
                  style={styles.manualLink}
                  onPress={() => setShowManualEntry(true)}
                  accessibilityRole="button"
                >
                  <Text style={styles.manualLinkText}>
                    {t('mobile.captureTab.byHandInstead')}
                  </Text>
                </TouchableOpacity>
              )}
            </>
          )}

          {showManualEntry && (
            <ManualPositionEntry
              onSubmit={useManualPosition}
              onCancel={() => setShowManualEntry(false)}
            />
          )}
        </MinimalCard>

        {/* 3. What it is */}
        <Text style={styles.stepLabel}>{t('mobile.captureTab.what')}</Text>
        <View style={styles.categoryGrid}>
          {CATEGORIES.map((item) => {
            const Icon = item.icon;
            const active = category === item.id;
            return (
              <TouchableOpacity
                key={item.id}
                style={[
                  styles.categoryCard,
                  active && styles.categoryCardActive,
                ]}
                onPress={() => setCategory(item.id)}
                accessibilityRole="radio"
                accessibilityState={{ selected: active }}
              >
                <Icon
                  size={18}
                  color={
                    active ? Theme.colors.telemetry : Theme.colors.textMuted
                  }
                />
                <Text
                  style={[
                    styles.categoryText,
                    active && styles.categoryTextActive,
                  ]}
                >
                  {t(`mobile.category.${item.id}`)}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {/* 4. Where */}
        <Text style={styles.stepLabel}>{t('mobile.captureTab.where')}</Text>
        <View style={styles.corridorRow}>
          {CORRIDORS.map((code) => {
            const active = corridor === code;
            return (
              <TouchableOpacity
                key={code}
                style={[
                  styles.corridorChip,
                  active && styles.corridorChipActive,
                ]}
                onPress={() => setCorridor(code)}
                accessibilityRole="radio"
                accessibilityState={{ selected: active }}
              >
                <Text
                  style={[
                    styles.corridorChipText,
                    active && styles.corridorChipTextActive,
                  ]}
                >
                  {code}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <TextInput
          style={styles.input}
          value={landmark}
          onChangeText={setLandmark}
          placeholder={t('mobile.captureTab.landmarkPlaceholder')}
          placeholderTextColor={Theme.colors.textDim}
        />

        {/* 5. How bad */}
        <Text style={styles.stepLabel}>{t('mobile.captureTab.howBad')}</Text>
        <View style={styles.severityRow}>
          {(['moderate', 'high', 'critical'] as RiskTier[]).map((level) => {
            const active = severity === level;
            return (
              <TouchableOpacity
                key={level}
                style={[
                  styles.severityChip,
                  active &&
                    (level === 'critical'
                      ? styles.severityCritical
                      : level === 'high'
                        ? styles.severityHigh
                        : styles.severityModerate),
                ]}
                onPress={() => setSeverity(level)}
                accessibilityRole="radio"
                accessibilityState={{ selected: active }}
              >
                <Text
                  style={[
                    styles.severityText,
                    active && styles.severityTextActive,
                  ]}
                >
                  {t(`mobile.captureTab.level.${level}`)}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <TextInput
          style={[styles.input, styles.textArea]}
          value={notes}
          onChangeText={setNotes}
          placeholder={t('mobile.captureTab.notesPlaceholder')}
          placeholderTextColor={Theme.colors.textDim}
          multiline
          numberOfLines={3}
        />

        <TouchableOpacity
          style={[styles.sendButton, !canSend && styles.sendButtonDisabled]}
          onPress={handleSend}
          disabled={!canSend}
          accessibilityRole="button"
        >
          {sending ? (
            <ActivityIndicator size="small" color="#FFFFFF" />
          ) : (
            <>
              <Send
                size={17}
                color={canSend ? '#FFFFFF' : Theme.colors.textDim}
              />
              <Text
                style={[
                  styles.sendButtonText,
                  !canSend && styles.sendButtonTextDisabled,
                ]}
              >
                {t('mobile.captureTab.send')}
              </Text>
            </>
          )}
        </TouchableOpacity>

        {!photoUri && (
          <Text style={styles.sendHint}>{t('mobile.captureTab.addPhoto')}</Text>
        )}
        {photoUri && !fix && (
          <View style={styles.noFixWarning}>
            <AlertTriangle size={14} color={Theme.colors.caution} />
            <Text style={styles.noFixWarningText}>
              {t('mobile.captureTab.addPosition')}
            </Text>
          </View>
        )}
        {sendError && (
          <View style={styles.noFixWarning} accessibilityRole="alert">
            <AlertTriangle size={14} color={Theme.colors.blocked} />
            <Text style={styles.noFixWarningText}>{t(sendError)}</Text>
          </View>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Theme.colors.bg },
  scroll: { flex: 1 },
  content: { padding: Theme.spacing.lg, paddingBottom: Theme.spacing.xxl },

  localBanner: {
    backgroundColor: Theme.colors.cautionBg,
    borderWidth: 1,
    borderColor: Theme.colors.cautionBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    marginBottom: Theme.spacing.lg,
  },
  localBannerText: {
    fontSize: 12,
    lineHeight: 17,
    color: Theme.colors.caution,
    fontWeight: '600',
  },

  draftBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Theme.spacing.md,
    backgroundColor: Theme.colors.telemetryBg,
    borderWidth: 1,
    borderColor: Theme.colors.telemetryBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    marginBottom: Theme.spacing.lg,
  },
  draftBannerText: {
    flex: 1,
    fontSize: 12,
    lineHeight: 17,
    color: Theme.colors.telemetry,
    fontWeight: '600',
  },
  draftBannerAction: {
    fontSize: 12,
    fontWeight: '800',
    color: Theme.colors.telemetry,
    textDecorationLine: 'underline',
  },
  manualLink: { marginTop: Theme.spacing.md, alignItems: 'center' },
  manualLinkText: {
    fontSize: 12.5,
    fontWeight: '700',
    color: Theme.colors.telemetry,
  },
  stepLabel: {
    fontSize: 11,
    fontWeight: '800',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
    marginTop: Theme.spacing.xl,
    marginBottom: Theme.spacing.md,
  },

  cameraTile: {
    backgroundColor: Theme.colors.surface,
    borderWidth: 1.5,
    borderColor: Theme.colors.brand,
    borderStyle: 'dashed',
    borderRadius: Theme.radius.lg,
    padding: Theme.spacing.xl,
    alignItems: 'center',
  },
  cameraIconPlate: {
    width: 58,
    height: 58,
    borderRadius: Theme.radius.lg,
    backgroundColor: Theme.colors.brand,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: Theme.spacing.md,
  },
  cameraTileTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: Theme.colors.text,
    marginBottom: 5,
  },
  cameraTileSub: {
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.textMuted,
    textAlign: 'center',
  },
  photoPreviewWrap: {
    borderRadius: Theme.radius.lg,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: Theme.colors.border,
    backgroundColor: Theme.colors.surface,
  },
  photoPreview: { width: '100%', height: 210, backgroundColor: '#000' },
  photoActions: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: Theme.colors.borderSubtle,
  },
  photoActionBtn: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 13,
  },
  photoActionText: {
    fontSize: 12.5,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  inlineError: {
    fontSize: 12,
    lineHeight: 17,
    color: Theme.colors.blocked,
    marginTop: Theme.spacing.sm,
    fontWeight: '600',
  },

  gpsCard: { padding: Theme.spacing.lg },
  gpsRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  gpsCoords: {
    fontSize: 15,
    fontFamily: 'monospace',
    fontWeight: '700',
    color: Theme.colors.text,
  },
  gpsMeta: { fontSize: 12, color: Theme.colors.textMuted, marginTop: 4 },
  gpsEmpty: {
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.textMuted,
    marginBottom: Theme.spacing.md,
  },
  gpsButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    backgroundColor: Theme.colors.telemetry,
    paddingVertical: 13,
    borderRadius: Theme.radius.sm,
    minHeight: 44,
  },
  gpsButtonText: { fontSize: 13, fontWeight: '800', color: '#FFFFFF' },
  gpsRefresh: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    marginTop: Theme.spacing.md,
  },
  gpsRefreshText: {
    fontSize: 12.5,
    fontWeight: '700',
    color: Theme.colors.telemetry,
  },

  categoryGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  categoryCard: {
    width: '48.5%',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: Theme.colors.surface,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    minHeight: 54,
  },
  categoryCardActive: {
    borderColor: Theme.colors.telemetry,
    backgroundColor: Theme.colors.telemetryBg,
  },
  categoryText: {
    flex: 1,
    fontSize: 12,
    fontWeight: '700',
    color: Theme.colors.textMuted,
  },
  categoryTextActive: { color: Theme.colors.text },

  corridorRow: { flexDirection: 'row', gap: 8, marginBottom: Theme.spacing.md },
  corridorChip: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: 11,
    backgroundColor: Theme.colors.surface,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.sm,
  },
  corridorChipActive: {
    borderColor: Theme.colors.telemetry,
    backgroundColor: Theme.colors.telemetryBg,
  },
  corridorChipText: {
    fontSize: 13,
    fontWeight: '800',
    fontFamily: 'monospace',
    color: Theme.colors.textMuted,
  },
  corridorChipTextActive: { color: Theme.colors.telemetry },

  input: {
    backgroundColor: Theme.colors.surface,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    paddingHorizontal: Theme.spacing.lg,
    paddingVertical: 13,
    fontSize: 14,
    color: Theme.colors.text,
  },
  textArea: {
    minHeight: 78,
    textAlignVertical: 'top',
    paddingTop: 13,
    marginTop: Theme.spacing.md,
  },

  severityRow: { flexDirection: 'row', gap: 8 },
  severityChip: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 13,
    paddingHorizontal: 6,
    backgroundColor: Theme.colors.surface,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.sm,
    minHeight: 50,
  },
  severityModerate: {
    backgroundColor: Theme.colors.cautionBg,
    borderColor: Theme.colors.cautionBorder,
  },
  severityHigh: {
    backgroundColor: 'rgba(182, 106, 45, 0.2)',
    borderColor: Theme.colors.caution,
  },
  severityCritical: {
    backgroundColor: Theme.colors.blockedBg,
    borderColor: Theme.colors.blocked,
  },
  severityText: {
    fontSize: 11.5,
    fontWeight: '800',
    color: Theme.colors.textMuted,
    textAlign: 'center',
  },
  severityTextActive: { color: Theme.colors.text },

  sendButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 9,
    backgroundColor: Theme.colors.brand,
    paddingVertical: 17,
    borderRadius: Theme.radius.md,
    marginTop: Theme.spacing.lg,
    minHeight: 54,
  },
  sendButtonDisabled: {
    backgroundColor: Theme.colors.surfaceElevated,
    borderWidth: 1,
    borderColor: Theme.colors.border,
  },
  sendButtonText: { fontSize: 14.5, fontWeight: '800', color: '#FFFFFF' },
  sendButtonTextDisabled: { color: Theme.colors.textDim },
  sendHint: {
    fontSize: 12,
    color: Theme.colors.textDim,
    textAlign: 'center',
    marginTop: Theme.spacing.md,
  },
  noFixWarning: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    backgroundColor: Theme.colors.cautionBg,
    borderWidth: 1,
    borderColor: Theme.colors.cautionBorder,
    borderRadius: Theme.radius.sm,
    padding: Theme.spacing.md,
    marginTop: Theme.spacing.md,
  },
  noFixWarningText: {
    flex: 1,
    fontSize: 12,
    lineHeight: 17,
    color: Theme.colors.caution,
  },
});
