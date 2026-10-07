import { useCallback, useEffect, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  AlertTriangle,
  Inbox,
  MapPin,
  RefreshCw,
  WifiOff,
} from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { HazardReport } from '../../types';
import { MinimalCard } from '../../components/ui/MinimalCard';
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';
import { QuickActionGrid } from '../../components/ui/QuickActionGrid';
import {
  getAllHazards,
  subscribeToOutbox,
} from '../../services/offlineStorage';
import { pendingWork } from '../../services/reportOutbox';
import { newUuid } from '../../services/ids';
import { message, type Message } from '../../services/i18n';
import { formatAge } from '../../services/routePack';

/** A report's state in words, from what the API said. */
function reportState(report: HazardReport): string {
  if (report.syncStatus === 'failed') return 'mobile.home.state.refused';
  if (report.controlRoomIncidentId) {
    return report.evidenceStatus === 'not_sent' ||
      report.evidenceStatus === 'uploaded'
      ? 'mobile.home.state.filedPhotoSending'
      : 'mobile.home.state.filed';
  }
  return 'mobile.home.state.notSent';
}
import {
  AlertRecord,
  MeResponse,
  acknowledgeAlert,
  getMe,
  listAlerts,
} from '../../services/rastaApi';

/**
 * What the control room has told this officer, and what this phone has sent.
 */

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready'; me: MeResponse; alerts: AlertRecord[]; asOf: string }
  | { kind: 'refused'; reason: string; offline: boolean };

const SEVERITY_STYLE = {
  critical: {
    fg: Theme.colors.blocked,
    bg: Theme.colors.blockedBg,
    border: Theme.colors.blockedBorder,
  },
  warning: {
    fg: Theme.colors.caution,
    bg: Theme.colors.cautionBg,
    border: Theme.colors.cautionBorder,
  },
  info: {
    fg: Theme.colors.telemetry,
    bg: Theme.colors.telemetryBg,
    border: Theme.colors.telemetryBorder,
  },
} as const;

/** The server names the title by key, as the web inbox does; a type no
 *  catalogue knows yet reads as its own words rather than as a key. */
function titleFor(alert: AlertRecord, t: (key: string) => string): string {
  const key = alert.title_key || `alert.${alert.type}`;
  const title = t(key);
  return title === key
    ? alert.type.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
    : title;
}

function sinceLabel(iso: string, now: number): Message {
  return formatAge(
    Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000)),
  );
}

/** The part of an alert's payload worth a line on a phone. */
function detailFor(alert: AlertRecord): Message | null {
  const payload = alert.payload ?? {};
  const segments = payload.segment_ids;
  if (alert.type === 'trip_route_invalidated') {
    const reference = payload.consignment_reference;
    if (payload.route_replaced_automatically !== false) return null;
    return typeof reference === 'string'
      ? message('mobile.home.detail.newRouteFor', { reference })
      : 'mobile.home.detail.newRoute';
  }
  if (alert.type === 'facility_isolated') {
    const name = payload.facility_name ?? payload.facility_id;
    return typeof name === 'string'
      ? message('mobile.home.detail.noRouteTo', { name })
      : null;
  }
  if (Array.isArray(segments)) {
    return message('mobile.home.detail.segments', { count: segments.length });
  }
  return null;
}

export default function FieldHomeScreen() {
  const crew = useCrew();
  const t = useT();
  const isLocal = crew.mode === 'local_only';
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [mine, setMine] = useState<HazardReport[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [acknowledging, setAcknowledging] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    setMine(await getAllHazards());
    // Without an account there is nobody at the control room to ask.
    if (isLocal) return;

    const me = await getMe();
    if (!me.ok) {
      setState({
        kind: 'refused',
        reason: me.reason,
        offline: me.code === 'network_error' || me.code === 'timeout',
      });
      return;
    }
    const alerts = await listAlerts({ limit: 30 });
    if (!alerts.ok) {
      setState({
        kind: 'refused',
        reason: alerts.reason,
        offline: alerts.code === 'network_error' || alerts.code === 'timeout',
      });
      return;
    }
    setState({
      kind: 'ready',
      me: me.data,
      alerts: alerts.data.alerts,
      asOf: alerts.data.as_of,
    });
    setNow(Date.now());
  }, [isLocal]);

  // Alerts are asked for again whenever the tab comes back into view.
  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  useEffect(
    () =>
      subscribeToOutbox(() => {
        void getAllHazards().then(setMine);
      }),
    [],
  );

  async function acknowledge(alert: AlertRecord) {
    setAcknowledging(alert.id);
    // The API takes only a UUID as an Idempotency-Key.
    const result = await acknowledgeAlert(alert.id, newUuid());
    setAcknowledging(null);
    if (result.ok) await load();
  }

  const district = state.kind === 'ready' ? state.me.districts[0] : null;
  const unacknowledged =
    state.kind === 'ready'
      ? state.alerts.filter((a) => !a.acknowledged_at)
      : [];
  const queued = mine.filter((report) => pendingWork(report) !== null);

  return (
    <View style={styles.container}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={async () => {
              setRefreshing(true);
              await load();
              setRefreshing(false);
            }}
            tintColor={Theme.colors.telemetry}
          />
        }
      >
        <View style={styles.intro}>
          <Text style={styles.title}>
            {district ? district.name : t('mobile.home.title')}
          </Text>
        </View>

        {isLocal ? (
          <MinimalCard style={styles.noticeCard}>
            <Text style={styles.noticeBody}>
              {t('mobile.home.localNotice')}
            </Text>
          </MinimalCard>
        ) : null}

        {state.kind === 'refused' ? (
          <MinimalCard style={styles.noticeCard}>
            <View style={styles.noticeRow}>
              {state.offline ? (
                <WifiOff size={18} color={Theme.colors.caution} />
              ) : (
                <AlertTriangle size={18} color={Theme.colors.blocked} />
              )}
              <Text style={styles.noticeTitle}>
                {state.offline
                  ? t('mobile.home.offlineTitle')
                  : t('mobile.home.nothingShown')}
              </Text>
            </View>
            <Text style={styles.noticeBody}>{t(state.reason)}</Text>
            <Text style={styles.noticeBody}>
              {t('mobile.home.keptOnPhone')}
            </Text>
            <TouchableOpacity
              accessibilityRole="button"
              accessibilityLabel={t('mobile.home.retryLabel')}
              style={styles.retry}
              onPress={() => void load()}
            >
              <RefreshCw size={14} color={Theme.colors.telemetry} />
              <Text style={styles.retryText}>{t('mobile.home.retry')}</Text>
            </TouchableOpacity>
          </MinimalCard>
        ) : null}

        <QuickActionGrid />

        {state.kind === 'ready' || (state.kind === 'loading' && !isLocal) ? (
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>
              {t('mobile.home.alertsHeading')}
            </Text>
            {state.kind === 'ready' ? (
              <Text style={styles.sectionCount}>
                {t('mobile.home.unread', {
                  unread: unacknowledged.length,
                  total: state.alerts.length,
                })}
              </Text>
            ) : null}
          </View>
        ) : null}

        {state.kind === 'loading' && !isLocal ? (
          <MinimalCard style={styles.emptyCard}>
            <Text style={styles.emptyBody}>
              {t('mobile.home.loadingAlerts')}
            </Text>
          </MinimalCard>
        ) : null}

        {state.kind === 'ready' && state.alerts.length === 0 ? (
          <MinimalCard style={styles.emptyCard}>
            <View style={styles.noticeRow}>
              <Inbox size={18} color={Theme.colors.textMuted} />
              <Text style={styles.noticeTitle}>
                {t('mobile.home.noAlerts')}
              </Text>
            </View>
          </MinimalCard>
        ) : null}

        {state.kind === 'ready'
          ? state.alerts.map((alert) => {
              const tone =
                SEVERITY_STYLE[alert.severity] ?? SEVERITY_STYLE.info;
              const detail = detailFor(alert);
              return (
                <MinimalCard
                  key={alert.id}
                  style={[styles.alertCard, { borderColor: tone.border }]}
                >
                  <View style={styles.alertTop}>
                    <View
                      style={[
                        styles.severityChip,
                        { backgroundColor: tone.bg },
                      ]}
                    >
                      <Text style={[styles.severityText, { color: tone.fg }]}>
                        {t(`alerts.severity.${alert.severity}`).toUpperCase()}
                      </Text>
                    </View>
                    <Text style={styles.alertAge}>
                      {t(sinceLabel(alert.valid_from, now))}
                    </Text>
                  </View>
                  <Text style={styles.alertTitle}>{titleFor(alert, t)}</Text>
                  {detail ? (
                    <Text style={styles.alertDetail}>{t(detail)}</Text>
                  ) : null}
                  {!alert.valid_now ? (
                    <Text style={styles.alertExpired}>
                      {t('mobile.home.expired')}
                    </Text>
                  ) : null}
                  {alert.acknowledged_at ? (
                    <Text style={styles.alertAcked}>
                      {t('mobile.home.acknowledgedAgo', {
                        when: t(sinceLabel(alert.acknowledged_at, now)),
                      })}
                    </Text>
                  ) : (
                    <TouchableOpacity
                      accessibilityRole="button"
                      accessibilityLabel={t('mobile.home.acknowledgeLabel', {
                        title: titleFor(alert, t),
                      })}
                      style={styles.ackButton}
                      disabled={acknowledging === alert.id}
                      onPress={() => void acknowledge(alert)}
                    >
                      <Text style={styles.ackButtonText}>
                        {acknowledging === alert.id
                          ? t('mobile.home.sending')
                          : t('alerts.acknowledge')}
                      </Text>
                    </TouchableOpacity>
                  )}
                </MinimalCard>
              );
            })
          : null}

        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>
            {t('mobile.home.yourReports')}
          </Text>
          <Text style={styles.sectionCount}>
            {queued.length
              ? t('mobile.home.waiting', { count: queued.length })
              : t('mobile.home.filed', { count: mine.length })}
          </Text>
        </View>

        {mine.length === 0 ? (
          <MinimalCard style={styles.emptyCard}>
            <Text style={styles.emptyBody}>{t('mobile.home.noReports')}</Text>
          </MinimalCard>
        ) : (
          mine.slice(0, 8).map((report) => (
            <MinimalCard key={report.id} style={styles.reportCard}>
              <View style={styles.alertTop}>
                <Text style={styles.reportCategory}>
                  {t(`mobile.category.${report.category}`)}
                </Text>
                <Text style={styles.alertAge}>{t(reportState(report))}</Text>
              </View>
              <View style={styles.noticeRow}>
                <MapPin size={13} color={Theme.colors.textMuted} />
                <Text style={styles.reportLocation}>{report.locationName}</Text>
              </View>
              {report.offlineRecorded ? (
                <Text style={styles.reportOffline}>
                  {t('mobile.home.savedWhileHeld')}
                </Text>
              ) : null}
            </MinimalCard>
          ))
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Theme.colors.bg },
  scroll: { flex: 1 },
  content: {
    padding: Theme.spacing.lg,
    paddingBottom: Theme.spacing.xxl,
    gap: Theme.spacing.md,
  },
  intro: { gap: Theme.spacing.xs, marginBottom: Theme.spacing.xs },
  title: {
    fontSize: Theme.typography.title,
    fontWeight: '700',
    color: Theme.colors.text,
  },

  noticeCard: {
    gap: Theme.spacing.sm,
    borderColor: Theme.colors.cautionBorder,
  },
  noticeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Theme.spacing.sm,
  },
  noticeTitle: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '600',
    color: Theme.colors.text,
  },
  noticeBody: {
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
    lineHeight: 19,
  },
  retry: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Theme.spacing.xs,
    alignSelf: 'flex-start',
    paddingVertical: Theme.spacing.sm,
    paddingHorizontal: Theme.spacing.md,
    borderRadius: Theme.radius.sm,
    backgroundColor: Theme.colors.telemetryBg,
  },
  retryText: {
    fontSize: Theme.typography.body,
    fontWeight: '600',
    color: Theme.colors.telemetry,
  },

  sectionHeader: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    marginTop: Theme.spacing.md,
  },
  sectionTitle: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  sectionCount: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.textDim,
  },

  emptyCard: { gap: Theme.spacing.sm },
  emptyBody: {
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
    lineHeight: 19,
  },

  alertCard: { gap: Theme.spacing.sm, borderWidth: 1 },
  alertTop: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  severityChip: {
    paddingHorizontal: Theme.spacing.sm,
    paddingVertical: 2,
    borderRadius: Theme.radius.xs,
  },
  severityText: {
    fontSize: Theme.typography.micro,
    fontWeight: '700',
    letterSpacing: 0.6,
  },
  alertAge: { fontSize: Theme.typography.caption, color: Theme.colors.textDim },
  alertTitle: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '600',
    color: Theme.colors.text,
  },
  alertDetail: {
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
    lineHeight: 19,
  },
  alertExpired: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.caution,
  },
  alertAcked: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.passable,
  },
  ackButton: {
    alignSelf: 'flex-start',
    paddingVertical: Theme.spacing.sm,
    paddingHorizontal: Theme.spacing.lg,
    borderRadius: Theme.radius.sm,
    backgroundColor: Theme.colors.telemetryBg,
    borderWidth: 1,
    borderColor: Theme.colors.telemetryBorder,
    minHeight: 44,
    justifyContent: 'center',
  },
  ackButtonText: {
    fontSize: Theme.typography.body,
    fontWeight: '600',
    color: Theme.colors.telemetry,
  },

  reportCard: { gap: Theme.spacing.xs },
  reportCategory: {
    fontSize: Theme.typography.body,
    fontWeight: '600',
    color: Theme.colors.text,
  },
  reportLocation: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
    flex: 1,
  },
  reportOffline: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.caution,
  },
});
