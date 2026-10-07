import { useCallback, useEffect, useState } from 'react';
import {
  Image,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {
  Camera,
  CircleSlash,
  Clock,
  Crosshair,
  Navigation,
} from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { HazardReport } from '../../types';
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';
import {
  getAllHazards,
  subscribeToOutbox,
} from '../../services/offlineStorage';
import { MinimalCard } from '../../components/ui/MinimalCard';

/**
 * The reports filed from this phone under this vehicle's crew code, and what
 * became of each.
 */
export default function CrewScreen() {
  const crew = useCrew();
  const t = useT();
  const [reports, setReports] = useState<HazardReport[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const all = await getAllHazards();
    setReports(all.filter((r) => r.crewCode === crew.crewCode));
  }, [crew.crewCode]);

  useEffect(() => {
    void load();
    return subscribeToOutbox(() => void load());
  }, [load]);

  const isObserver = crew.role === 'observer';

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={async () => {
            setRefreshing(true);
            await load();
            setRefreshing(false);
          }}
          tintColor={Theme.colors.brand}
        />
      }
    >
      <MinimalCard style={styles.crewCard}>
        <Text style={styles.crewLabel}>{t('mobile.crew.crewCode')}</Text>
        <Text style={styles.crewCode}>{crew.crewCode}</Text>
        <Text style={styles.crewHint}>
          {isObserver
            ? t('mobile.crewTab.hintObserver')
            : t('mobile.crewTab.hintDriver')}
        </Text>
      </MinimalCard>

      {reports.length === 0 ? (
        <MinimalCard style={styles.empty}>
          <CircleSlash size={22} color={Theme.colors.textDim} />
          <Text style={styles.emptyTitle}>
            {t('mobile.crewTab.nothingYet')}
          </Text>
          <Text style={styles.emptyBody}>
            {isObserver
              ? t('mobile.crewTab.emptyObserver')
              : t('mobile.crewTab.emptyDriver')}
          </Text>
        </MinimalCard>
      ) : (
        reports.map((report) => (
          <MinimalCard key={report.idempotencyKey} style={styles.reportCard}>
            <View style={styles.reportHead}>
              <View
                style={[
                  styles.severityPill,
                  report.severity === 'critical'
                    ? styles.pillCritical
                    : report.severity === 'high'
                      ? styles.pillHigh
                      : styles.pillModerate,
                ]}
              >
                <Text style={styles.severityPillText}>
                  {t(`mobile.severity.${report.severity}`).toUpperCase()}
                </Text>
              </View>
              <Text style={styles.corridorTag}>{report.corridorCode}</Text>
            </View>

            <Text style={styles.reportTitle}>
              {t(`mobile.category.${report.category}`)}
            </Text>
            <Text style={styles.reportLocation}>{report.locationName}</Text>

            {report.photoUri && (
              <Image
                source={{ uri: report.photoUri }}
                style={styles.reportPhoto}
              />
            )}

            {report.notes && (
              <Text style={styles.reportNotes}>{report.notes}</Text>
            )}

            <View style={styles.metaGrid}>
              <View style={styles.metaRow}>
                {report.reporterRole === 'observer' ? (
                  <Camera size={12} color={Theme.colors.textMuted} />
                ) : (
                  <Navigation size={12} color={Theme.colors.textMuted} />
                )}
                <Text style={styles.metaText}>
                  {report.reporterRole
                    ? t('mobile.crewTab.byRole', {
                        name:
                          report.reporterName ??
                          t('mobile.crewTab.unattributed'),
                        role: t(`mobile.seat.${report.reporterRole}`),
                      })
                    : (report.reporterName ?? t('mobile.crewTab.unattributed'))}
                </Text>
              </View>

              <View style={styles.metaRow}>
                <Clock size={12} color={Theme.colors.textMuted} />
                <Text style={styles.metaText}>
                  {new Date(
                    report.capturedAtIso ?? report.reportedAt,
                  ).toLocaleString()}
                </Text>
              </View>

              {report.accuracyMeters !== undefined && (
                <View style={styles.metaRow}>
                  <Crosshair size={12} color={Theme.colors.textMuted} />
                  <Text style={styles.metaText}>
                    {t('mobile.crewTab.accuracy', {
                      metres: report.accuracyMeters,
                    })}
                  </Text>
                </View>
              )}
            </View>

            <View
              style={[
                styles.syncStrip,
                report.controlRoomIncidentId
                  ? styles.syncSynced
                  : styles.syncQueued,
              ]}
            >
              <Text
                style={[
                  styles.syncText,
                  report.controlRoomIncidentId
                    ? styles.syncTextSynced
                    : styles.syncTextQueued,
                ]}
              >
                {t(deliveryLine(report))}
              </Text>
            </View>
          </MinimalCard>
        ))
      )}
    </ScrollView>
  );
}

/** What became of a report, from what the API said, never assumed. */
function deliveryLine(report: HazardReport): string {
  if (report.controlRoomIncidentId) {
    if (report.evidenceStatus === 'verified') {
      return 'mobile.crewTab.filedPhotoVerified';
    }
    if (
      report.evidenceStatus === 'not_sent' ||
      report.evidenceStatus === 'uploaded'
    ) {
      return 'mobile.crewTab.filedPhotoSending';
    }
    if (
      report.evidenceStatus === 'rejected' ||
      report.evidenceStatus === 'failed'
    ) {
      return 'mobile.crewTab.filedPhotoRejected';
    }
    return 'mobile.crewTab.filed';
  }
  if (report.syncStatus === 'failed') return 'mobile.crewTab.refused';
  return 'mobile.crewTab.notSent';
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Theme.colors.bg },
  content: { padding: Theme.spacing.lg, paddingBottom: Theme.spacing.xxl },

  crewCard: { alignItems: 'flex-start' },
  crewLabel: {
    fontSize: 10.5,
    fontWeight: '800',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  crewCode: {
    fontSize: 22,
    fontFamily: 'monospace',
    fontWeight: '700',
    color: Theme.colors.text,
    letterSpacing: 1.5,
    marginVertical: 6,
  },
  crewHint: {
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.textMuted,
  },

  empty: {
    alignItems: 'center',
    paddingVertical: Theme.spacing.xxl,
    marginTop: Theme.spacing.md,
  },
  emptyTitle: {
    fontSize: 15,
    fontWeight: '800',
    color: Theme.colors.text,
    marginTop: Theme.spacing.md,
  },
  emptyBody: {
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.textMuted,
    textAlign: 'center',
    marginTop: 5,
  },

  reportCard: { marginBottom: Theme.spacing.md, padding: Theme.spacing.lg },
  reportHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: Theme.spacing.md,
  },
  severityPill: {
    paddingHorizontal: 9,
    paddingVertical: 4,
    borderRadius: Theme.radius.xs,
  },
  pillCritical: { backgroundColor: Theme.colors.blockedBg },
  pillHigh: { backgroundColor: Theme.colors.cautionBg },
  pillModerate: { backgroundColor: Theme.colors.telemetryBg },
  severityPillText: {
    fontSize: 9.5,
    fontWeight: '900',
    letterSpacing: 0.6,
    color: Theme.colors.text,
  },
  corridorTag: {
    fontSize: 12,
    fontFamily: 'monospace',
    fontWeight: '800',
    color: Theme.colors.textMuted,
  },
  reportTitle: { fontSize: 15, fontWeight: '800', color: Theme.colors.text },
  reportLocation: {
    fontSize: 12.5,
    color: Theme.colors.textMuted,
    marginTop: 3,
  },
  reportPhoto: {
    width: '100%',
    height: 150,
    borderRadius: Theme.radius.md,
    marginTop: Theme.spacing.md,
    backgroundColor: Theme.colors.surfaceElevated,
  },
  reportNotes: {
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.text,
    marginTop: Theme.spacing.md,
    fontStyle: 'italic',
  },
  metaGrid: { marginTop: Theme.spacing.md, gap: 6 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  metaText: { flex: 1, fontSize: 11.5, color: Theme.colors.textMuted },
  syncStrip: {
    marginTop: Theme.spacing.md,
    paddingVertical: 8,
    paddingHorizontal: Theme.spacing.md,
    borderRadius: Theme.radius.sm,
  },
  syncSynced: { backgroundColor: Theme.colors.passableBg },
  syncQueued: { backgroundColor: Theme.colors.cautionBg },
  syncText: { fontSize: 11.5, fontWeight: '700' },
  syncTextSynced: { color: Theme.colors.passable },
  syncTextQueued: { color: Theme.colors.caution },
});
