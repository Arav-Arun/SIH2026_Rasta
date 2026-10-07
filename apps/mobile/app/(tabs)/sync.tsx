import { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  Alert,
} from 'react-native';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Database,
  PauseCircle,
  PlayCircle,
  RefreshCw,
} from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { HazardReport } from '../../types';
import {
  getNetworkMode,
  setNetworkMode,
  getAllHazards,
  removeReport,
  syncOutboxQueue,
  subscribeToOutbox,
  NetworkMode,
} from '../../services/offlineStorage';
import { pendingWork } from '../../services/reportOutbox';
import { MinimalCard } from '../../components/ui/MinimalCard';
import { TrackerPanel } from '../../components/driver/TrackerPanel';
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';

function when(report: HazardReport): string {
  const date = new Date(report.reportedAt);
  return Number.isNaN(date.getTime())
    ? report.reportedAt
    : date.toLocaleString();
}

/**
 * The phone's outbox: what is waiting to reach the control room, what it
 * refused, and what it has accepted, each in the words the API used.
 */
export default function SyncScreen() {
  const crew = useCrew();
  const t = useT();
  const [mode, setMode] = useState<NetworkMode>('online');
  const [reports, setReports] = useState<HazardReport[]>([]);
  const [syncing, setSyncing] = useState<boolean>(false);

  const loadData = useCallback(async () => {
    const [currentMode, all] = await Promise.all([
      getNetworkMode(),
      getAllHazards(),
    ]);
    setMode(currentMode);
    setReports(all);
  }, []);

  useEffect(() => {
    void loadData();
    return subscribeToOutbox(() => void loadData());
  }, [loadData]);

  const sendNow = useCallback(async () => {
    setSyncing(true);
    try {
      const result = await syncOutboxQueue();
      const parts = [
        t('mobile.outboxTab.accepted', { count: result.sent }),
        result.waiting
          ? t('mobile.outboxTab.stillWaiting', { count: result.waiting })
          : null,
        result.refused
          ? t('mobile.outboxTab.refusedCount', { count: result.refused })
          : null,
      ].filter(Boolean);
      Alert.alert(
        result.waiting === 0 && result.refused === 0
          ? t('mobile.outboxTab.sent')
          : t('mobile.outboxTab.notFullySent'),
        `${parts.join(', ')}.${result.reason && result.waiting ? `\n\n${t(result.reason)}` : ''}`,
      );
    } finally {
      setSyncing(false);
      void loadData();
    }
  }, [loadData, t]);

  async function toggleHold() {
    const next = mode === 'online' ? 'dead_zone' : 'online';
    await setNetworkMode(next);
    setMode(next);
    if (
      next === 'online' &&
      reports.some((report) => pendingWork(report) !== null)
    ) {
      await sendNow();
    }
  }

  function confirmRemove(report: HazardReport) {
    Alert.alert(
      t('mobile.outboxTab.removeTitle'),
      report.controlRoomIncidentId
        ? t('mobile.outboxTab.removeKept')
        : t('mobile.outboxTab.removeLost'),
      [
        { text: t('mobile.outboxTab.keep'), style: 'cancel' },
        {
          text: t('mobile.outboxTab.remove'),
          style: 'destructive',
          onPress: () => void removeReport(report.id),
        },
      ],
    );
  }

  const waiting = reports.filter((report) => pendingWork(report) !== null);
  const refused = reports.filter((report) => report.syncStatus === 'failed');
  const accepted = reports.filter(
    (report) => report.syncStatus === 'synced' && pendingWork(report) === null,
  );
  const holding = mode === 'dead_zone';

  return (
    <View style={styles.container}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollContent}
      >
        {/* Position reporting first: it is the one queue on this screen that
            the control room is actively waiting on. Only a signed-in driver's
            phone reports a trip's position. */}
        {crew.role === 'driver' && crew.mode !== 'local_only' ? (
          <TrackerPanel />
        ) : null}

        <MinimalCard style={styles.holdCard}>
          <View style={styles.holdHeader}>
            <View style={styles.holdTitleRow}>
              <Database size={16} color={Theme.colors.textMuted} />
              <Text style={styles.holdTitle}>
                {t('mobile.outboxTab.sendingReports')}
              </Text>
            </View>
            <Text
              style={[
                styles.holdState,
                holding ? styles.holdStateHeld : styles.holdStateSending,
              ]}
            >
              {holding
                ? t('mobile.outboxTab.onHold')
                : t('mobile.outboxTab.sending')}
            </Text>
          </View>

          <Text style={styles.holdText}>
            {holding
              ? t('mobile.outboxTab.heldText')
              : t('mobile.outboxTab.sendingText')}
          </Text>

          <TouchableOpacity
            style={styles.holdButton}
            onPress={() => void toggleHold()}
            accessibilityRole="button"
          >
            {holding ? (
              <PlayCircle size={18} color={Theme.colors.brand} />
            ) : (
              <PauseCircle size={18} color={Theme.colors.brand} />
            )}
            <Text style={styles.holdButtonText}>
              {holding
                ? t('mobile.outboxTab.release')
                : t('mobile.outboxTab.hold')}
            </Text>
          </TouchableOpacity>
        </MinimalCard>

        <View style={styles.queueHeaderRow}>
          <Text style={styles.sectionHeading}>
            {t('mobile.outboxTab.waitingHeading', { count: waiting.length })}
          </Text>
          {waiting.length > 0 && !holding && (
            <TouchableOpacity
              style={styles.syncBtn}
              onPress={() => void sendNow()}
              disabled={syncing}
              accessibilityRole="button"
            >
              <RefreshCw size={13} color="#FFF" />
              <Text style={styles.syncBtnText}>
                {syncing
                  ? t('mobile.home.sending')
                  : t('mobile.tracker.sendNow')}
              </Text>
            </TouchableOpacity>
          )}
        </View>

        {waiting.length === 0 ? (
          <View style={styles.emptyOutbox}>
            <CheckCircle2 size={24} color={Theme.colors.passable} />
            <Text style={styles.emptyTitle}>
              {t('mobile.outboxTab.nothingWaiting')}
            </Text>
          </View>
        ) : (
          waiting.map((item) => {
            const photoOnly = pendingWork(item) === 'evidence';
            const reason = photoOnly
              ? item.filing?.evidenceError
              : item.filing?.lastError;
            return (
              <MinimalCard key={item.id} style={styles.queueItemCard}>
                <View style={styles.queueItemTop}>
                  <View style={styles.queueTitleGroup}>
                    <Text style={styles.queueCategory}>
                      {t(`mobile.category.${item.category}`)}
                    </Text>
                    <Text style={styles.queueLoc}>
                      {item.corridorCode}, {item.locationName}
                    </Text>
                  </View>
                  <View style={styles.queuedBadge}>
                    <Clock size={11} color={Theme.colors.caution} />
                    <Text style={styles.queuedBadgeText}>
                      {(photoOnly
                        ? t('mobile.outboxTab.photoWaiting')
                        : t('mobile.outboxTab.notSent')
                      ).toUpperCase()}
                    </Text>
                  </View>
                </View>

                {photoOnly ? (
                  <Text style={styles.reasonText}>
                    {t('mobile.outboxTab.photoOnly')}
                  </Text>
                ) : null}
                {reason ? (
                  <Text style={styles.reasonText}>{t(reason)}</Text>
                ) : null}
                {item.notes ? (
                  <Text style={styles.queueNotes}>{item.notes}</Text>
                ) : null}

                <View style={styles.queueFooter}>
                  <Text style={styles.idempText}>
                    {item.filing?.attempts
                      ? t('mobile.outboxTab.attempts', {
                          count: item.filing.attempts,
                        })
                      : t('mobile.outboxTab.notTried')}
                  </Text>
                  <Text style={styles.queueTime}>{when(item)}</Text>
                </View>
              </MinimalCard>
            );
          })
        )}

        {refused.length > 0 && (
          <>
            <Text
              style={[
                styles.sectionHeading,
                { marginTop: 20, marginBottom: 10 },
              ]}
            >
              {t('mobile.outboxTab.refusedHeading', { count: refused.length })}
            </Text>
            {refused.map((item) => (
              <MinimalCard key={item.id} style={styles.failedCard}>
                <View style={styles.queueItemTop}>
                  <View style={styles.queueTitleGroup}>
                    <Text style={styles.queueCategory}>
                      {t(`mobile.category.${item.category}`)}
                    </Text>
                    <Text style={styles.queueLoc}>
                      {item.corridorCode}, {item.locationName}
                    </Text>
                  </View>
                  <View style={styles.failedBadge}>
                    <AlertTriangle size={11} color={Theme.colors.blocked} />
                    <Text style={styles.failedBadgeText}>
                      {t('mobile.outboxTab.refusedBadge').toUpperCase()}
                    </Text>
                  </View>
                </View>
                <Text style={styles.reasonText}>
                  {t(item.filing?.lastError ?? 'mobile.outboxTab.notAccepted')}
                </Text>
                <TouchableOpacity
                  style={styles.removeBtn}
                  onPress={() => confirmRemove(item)}
                  accessibilityRole="button"
                >
                  <Text style={styles.removeBtnText}>
                    {t('mobile.outboxTab.removeFromPhone')}
                  </Text>
                </TouchableOpacity>
              </MinimalCard>
            ))}
          </>
        )}

        <Text
          style={[styles.sectionHeading, { marginTop: 20, marginBottom: 10 }]}
        >
          {t('mobile.outboxTab.acceptedHeading', { count: accepted.length })}
        </Text>

        {accepted.slice(0, 5).map((item) => (
          <MinimalCard key={item.id} style={styles.syncedCard}>
            <View style={styles.syncedTop}>
              <Text style={styles.syncedTitle}>
                {t(`mobile.category.${item.category}`)}
              </Text>
              <View style={styles.syncedBadge}>
                <CheckCircle2 size={11} color={Theme.colors.passable} />
                <Text style={styles.syncedBadgeText}>
                  {t('mobile.outboxTab.filedBadge').toUpperCase()}
                </Text>
              </View>
            </View>
            <Text style={styles.syncedLoc}>
              {item.corridorCode}, {item.locationName}
            </Text>
            <Text style={styles.syncedDetail}>
              {[
                item.controlRoomIncidentId
                  ? t('mobile.outboxTab.reference', {
                      id: item.controlRoomIncidentId.slice(0, 8),
                    })
                  : null,
                t(`mobile.outboxTab.photo.${item.evidenceStatus ?? 'none'}`),
              ]
                .filter(Boolean)
                .join(', ')}
            </Text>
            {item.filing?.evidenceError ? (
              <Text style={styles.syncedDetail}>
                {t(item.filing.evidenceError)}
              </Text>
            ) : null}
          </MinimalCard>
        ))}
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
  holdCard: { marginBottom: Theme.spacing.md },
  holdHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  holdTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  holdTitle: { fontSize: 14, fontWeight: '700', color: Theme.colors.text },
  holdState: { fontSize: 12, fontWeight: '600' },
  holdStateSending: { color: Theme.colors.passable },
  holdStateHeld: { color: Theme.colors.caution },
  holdText: {
    fontSize: 13,
    color: Theme.colors.textMuted,
    lineHeight: 18,
    marginBottom: Theme.spacing.md,
  },
  holdButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 12,
    borderRadius: Theme.radius.md,
    borderWidth: 1,
    borderColor: Theme.colors.border,
  },
  holdButtonText: {
    fontSize: 14,
    fontWeight: '600',
    color: Theme.colors.brand,
  },
  queueHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10,
    marginTop: 6,
  },
  sectionHeading: {
    fontSize: 12,
    fontWeight: '800',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  syncBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    backgroundColor: Theme.colors.brand,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: Theme.radius.sm,
  },
  syncBtnText: {
    fontSize: 11,
    fontWeight: '800',
    color: '#FFF',
  },
  emptyOutbox: {
    alignItems: 'center',
    justifyContent: 'center',
    padding: Theme.spacing.lg,
    backgroundColor: Theme.colors.surface,
    borderColor: Theme.colors.border,
    borderWidth: 1,
    borderRadius: Theme.radius.md,
    marginBottom: Theme.spacing.md,
  },
  emptyTitle: {
    fontSize: 14,
    fontWeight: '800',
    color: Theme.colors.text,
    marginTop: 8,
  },
  queueItemCard: {
    marginBottom: 8,
    borderColor: Theme.colors.cautionBorder,
    backgroundColor: 'rgba(245, 158, 11, 0.05)',
  },
  queueItemTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 4,
  },
  queueTitleGroup: {
    flex: 1,
  },
  queueCategory: {
    fontSize: 13,
    fontWeight: '800',
    color: Theme.colors.text,
  },
  queueLoc: {
    fontSize: 11,
    color: Theme.colors.telemetry,
    fontWeight: '600',
  },
  queuedBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: Theme.colors.cautionBg,
    borderColor: Theme.colors.cautionBorder,
    borderWidth: 1,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: Theme.radius.xs,
  },
  queuedBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    color: Theme.colors.caution,
  },
  queueNotes: {
    fontSize: 11,
    color: Theme.colors.textMuted,
    lineHeight: 15,
    marginVertical: 4,
  },
  queueFooter: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    borderTopWidth: 1,
    borderTopColor: Theme.colors.borderSubtle,
    paddingTop: 4,
    marginTop: 4,
  },
  idempText: {
    fontSize: 9,
    fontFamily: 'monospace',
    color: Theme.colors.textDim,
  },
  queueTime: {
    fontSize: 9,
    color: Theme.colors.textDim,
  },
  syncedCard: {
    marginBottom: 8,
    backgroundColor: Theme.colors.surfaceElevated,
  },
  syncedTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 2,
  },
  syncedTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  syncedBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 3,
  },
  syncedBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    color: Theme.colors.passable,
  },
  syncedLoc: {
    fontSize: 11,
    color: Theme.colors.textMuted,
  },
  syncedDetail: {
    fontSize: 10.5,
    color: Theme.colors.textMuted,
    marginTop: 4,
    lineHeight: 15,
  },
  reasonText: {
    fontSize: 11,
    color: Theme.colors.text,
    lineHeight: 16,
    marginTop: 4,
  },
  failedCard: {
    marginBottom: 8,
    borderColor: Theme.colors.blocked,
  },
  failedBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    borderColor: Theme.colors.blocked,
    borderWidth: 1,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: Theme.radius.xs,
  },
  failedBadgeText: {
    fontSize: 9,
    fontWeight: '800',
    color: Theme.colors.blocked,
  },
  removeBtn: {
    alignSelf: 'flex-start',
    marginTop: 8,
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: Theme.radius.sm,
    borderWidth: 1,
    borderColor: Theme.colors.border,
  },
  removeBtnText: {
    fontSize: 11,
    fontWeight: '700',
    color: Theme.colors.text,
  },
});
