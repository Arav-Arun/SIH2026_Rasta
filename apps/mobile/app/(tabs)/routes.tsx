import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useFocusEffect } from 'expo-router';
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  AlertTriangle,
  CheckCircle2,
  CloudOff,
  MapPin,
  Navigation,
  ShieldQuestion,
} from 'lucide-react-native';

import { Theme } from '../../constants/theme';
import { MinimalCard } from '../../components/ui/MinimalCard';
import { RouteLineMap } from '../../components/driver/RouteLineMap';
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';
import {
  acknowledgeRevision,
  readCachedRoute,
  refreshRoute,
  type DriverRouteView,
} from '../../services/driverRoute';
import {
  formatAge,
  formatDistance,
  formatEtaRange,
  secondsSince,
  type RoutePack,
  type RoutePackFreshness,
} from '../../services/routePack';

/** The route this driver is following. */
export default function RoutesScreen() {
  const crew = useCrew();
  const t = useT();
  const [view, setView] = useState<DriverRouteView | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);

  const isLocal = crew.mode === 'local_only';

  // A session without an account has no trips, so there is nothing to ask the
  // control room for.
  const refresh = useCallback(async () => {
    setView(
      isLocal
        ? await readCachedRoute(new Date())
        : await refreshRoute(new Date()),
    );
  }, [isLocal]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const cached = await readCachedRoute(new Date());
      if (!cancelled) setView(cached);
      if (isLocal) return;
      const fresh = await refreshRoute(new Date());
      if (!cancelled) setView(fresh);
    })();
    return () => {
      cancelled = true;
    };
  }, [isLocal]);

  // Coming back to the tab asks the control room again, as pulling down does.
  // The first focus is the mount, which the effect above already covers.
  const seenFocus = useRef(false);
  useFocusEffect(
    useCallback(() => {
      if (!seenFocus.current) {
        seenFocus.current = true;
        return;
      }
      void refresh();
    }, [refresh]),
  );

  if (!view) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={Theme.colors.brand} />
        <Text style={styles.emptyBody}>{t('mobile.routes.reading')}</Text>
      </View>
    );
  }

  async function accept() {
    if (!view?.revision) return;
    setBusy(true);
    try {
      await acknowledgeRevision(view.revision.pack, new Date());
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={async () => {
            setRefreshing(true);
            await refresh();
            setRefreshing(false);
          }}
          tintColor={Theme.colors.brand}
        />
      }
    >
      {view.offlineReason && (
        <View style={styles.offlineBox} accessibilityRole="alert">
          <CloudOff size={16} color={Theme.colors.caution} />
          <Text style={styles.offlineText}>
            {view.pack
              ? t('mobile.routes.showingSaved', { reason: view.offlineReason })
              : t(view.offlineReason)}
          </Text>
        </View>
      )}

      {view.revision && (
        <RevisionNotice
          incoming={view.revision.pack}
          busy={busy}
          onAccept={accept}
        />
      )}

      {view.pack ? (
        <RouteCard pack={view.pack} freshness={view.freshness} />
      ) : (
        <AbsenceCard absence={view.absence} localOnly={isLocal} />
      )}

      {view.trip && (
        <MinimalCard>
          <Text style={styles.sectionLabel}>{t('mobile.routes.thisTrip')}</Text>
          <Text style={styles.reference}>
            {view.trip.consignment_reference ?? view.trip.consignment_id}
          </Text>
          <Text style={styles.meta}>
            {t('mobile.routes.vehicleStatus', {
              vehicle:
                view.trip.vehicle_registration ??
                t('mobile.routes.vehicleUnnamed'),
              status: t(`mobile.trips.status.${view.trip.status}`),
            })}
          </Text>
        </MinimalCard>
      )}
    </ScrollView>
  );
}

/** How far to trust the saved route; the words are mobile.routes.fresh.<state>. */
const FRESHNESS_TONE: Record<RoutePackFreshness, 'good' | 'warn' | 'bad'> = {
  confirmed_current: 'good',
  cached_unverified: 'warn',
  cached_offline: 'warn',
  replaced: 'bad',
  withdrawn: 'bad',
  none: 'warn',
};

function RouteCard({
  pack,
  freshness,
}: {
  pack: RoutePack;
  freshness: RoutePackFreshness;
}) {
  const t = useT();
  const tone = FRESHNESS_TONE[freshness];
  const eta = formatEtaRange(pack.eta_range_seconds);
  const verifiedAge = t(formatAge(secondsSince(pack.verified_at, new Date())));

  return (
    <MinimalCard>
      <View style={styles.cardHead}>
        <Text style={styles.sectionLabel}>{t('mobile.routes.approved')}</Text>
        <Text style={[styles.badge, styles[`badge_${tone}`]]}>
          {t(`mobile.routes.fresh.${freshness}.label`)}
        </Text>
      </View>

      <View style={styles.figures}>
        <View style={styles.figure}>
          <Text style={styles.figureValue}>
            {formatDistance(pack.distance_m)}
          </Text>
          <Text style={styles.figureLabel}>{t('mobile.routes.distance')}</Text>
        </View>
        <View style={styles.figure}>
          {/* An ETA is a range. A single number would read as a promise about
              road whose state nobody has observed. */}
          <Text style={styles.figureValue}>
            {eta ? t(eta) : t('mobile.routes.notEstimated')}
          </Text>
          <Text style={styles.figureLabel}>
            {t('mobile.routes.timeOnRoad')}
          </Text>
        </View>
        <View style={styles.figure}>
          <Text style={styles.figureValue}>{pack.segment_ids.length}</Text>
          <Text style={styles.figureLabel}>{t('mobile.routes.sections')}</Text>
        </View>
      </View>

      {/* Drawn only when the server actually sent geometry. A straight line
          between endpoints would be a road that does not exist. */}
      <View style={styles.map}>
        <RouteLineMap geometry={pack.geometry} />
      </View>

      {freshness === 'none' ? null : (
        <Text style={styles.stateBody}>
          {t(`mobile.routes.fresh.${freshness}.body`)}
        </Text>
      )}
      <Text style={styles.meta}>
        {t('mobile.routes.lastConfirmed', { when: verifiedAge })}
      </Text>

      {pack.recommendation && (
        <View style={styles.reasonBox}>
          <Navigation size={14} color={Theme.colors.telemetry} />
          <Text style={styles.reasonText}>{pack.recommendation}</Text>
        </View>
      )}

      <View style={styles.caveats}>
        {pack.avoided_closures > 0 && (
          <Caveat
            tone="good"
            icon={<CheckCircle2 size={14} color={Theme.colors.passable} />}
            text={t('mobile.routes.avoids', { count: pack.avoided_closures })}
          />
        )}
        {pack.unknown_constraints > 0 && (
          <Caveat
            tone="warn"
            icon={<ShieldQuestion size={14} color={Theme.colors.caution} />}
            text={t('mobile.routes.unverifiedLimits', {
              count: pack.unknown_constraints,
            })}
          />
        )}
        {pack.segments_without_a_risk_score > 0 && (
          <Caveat
            tone="warn"
            icon={<ShieldQuestion size={14} color={Theme.colors.caution} />}
            text={t('mobile.routes.noRiskScore', {
              count: pack.segments_without_a_risk_score,
            })}
          />
        )}
      </View>
    </MinimalCard>
  );
}

function Caveat({
  tone,
  icon,
  text,
}: {
  tone: 'good' | 'warn';
  icon: React.ReactNode;
  text: string;
}) {
  return (
    <View style={styles.caveatRow}>
      {icon}
      <Text style={[styles.caveatText, tone === 'good' && styles.caveatGood]}>
        {text}
      </Text>
    </View>
  );
}

function RevisionNotice({
  incoming,
  busy,
  onAccept,
}: {
  incoming: RoutePack;
  busy: boolean;
  onAccept: () => void;
}) {
  const t = useT();
  const eta = formatEtaRange(incoming.eta_range_seconds);
  return (
    <View style={styles.revisionBox}>
      <View style={styles.revisionHead}>
        <AlertTriangle size={16} color={Theme.colors.caution} />
        <Text style={styles.revisionTitle}>
          {t('mobile.routes.changedTitle')}
        </Text>
      </View>
      <Text style={styles.revisionBody}>
        {eta
          ? t('mobile.routes.changedBodyEta', {
              distance: formatDistance(incoming.distance_m),
              eta: t(eta),
            })
          : t('mobile.routes.changedBody', {
              distance: formatDistance(incoming.distance_m),
            })}
        {incoming.unknown_constraints > 0
          ? ` ${t('mobile.routes.changedLimits', {
              count: incoming.unknown_constraints,
            })}`
          : ''}
      </Text>
      <TouchableOpacity
        style={[styles.action, busy && styles.actionBusy]}
        disabled={busy}
        onPress={onAccept}
        accessibilityRole="button"
        accessibilityLabel={t('mobile.routes.acknowledgeLabel')}
      >
        <Text style={styles.actionText}>
          {busy ? t('mobile.routes.saving') : t('mobile.routes.readNew')}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

function AbsenceCard({
  absence,
  localOnly,
}: {
  absence: DriverRouteView['absence'];
  localOnly: boolean;
}) {
  const t = useT();
  const kind = absence ?? 'no_trip';
  return (
    <MinimalCard>
      <View style={styles.empty}>
        <MapPin size={22} color={Theme.colors.textDim} />
        <Text style={styles.emptyTitle}>
          {t(`mobile.routes.absence.${kind}.title`)}
        </Text>
        <Text style={styles.emptyBody}>
          {localOnly
            ? t('mobile.routes.signInForRoutes')
            : t(`mobile.routes.absence.${kind}.body`)}
        </Text>
      </View>
    </MinimalCard>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Theme.colors.bg },
  content: {
    padding: Theme.spacing.lg,
    paddingBottom: Theme.spacing.xxl,
    gap: Theme.spacing.md,
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Theme.spacing.xl,
    gap: Theme.spacing.sm,
    backgroundColor: Theme.colors.bg,
  },
  empty: { alignItems: 'center', gap: Theme.spacing.sm },
  emptyTitle: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  emptyBody: {
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
    textAlign: 'center',
    lineHeight: 20,
  },
  cardHead: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: Theme.spacing.sm,
  },
  sectionLabel: {
    fontSize: Theme.typography.caption,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  badge: {
    flexShrink: 1,
    fontSize: Theme.typography.micro,
    fontWeight: '700',
    textAlign: 'right',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  badge_good: { color: Theme.colors.passable },
  badge_warn: { color: Theme.colors.caution },
  badge_bad: { color: Theme.colors.blocked },
  figures: {
    flexDirection: 'row',
    gap: Theme.spacing.md,
    marginTop: Theme.spacing.sm,
  },
  figure: { flex: 1, gap: 2 },
  figureValue: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '700',
    color: Theme.colors.text,
    fontVariant: ['tabular-nums'],
  },
  figureLabel: {
    fontSize: Theme.typography.micro,
    color: Theme.colors.textMuted,
  },
  stateBody: {
    marginTop: Theme.spacing.sm,
    fontSize: Theme.typography.body,
    color: Theme.colors.text,
    lineHeight: 20,
  },
  meta: { fontSize: Theme.typography.caption, color: Theme.colors.textMuted },
  reference: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  reasonBox: {
    flexDirection: 'row',
    gap: Theme.spacing.sm,
    alignItems: 'flex-start',
    backgroundColor: Theme.colors.telemetryBg,
    borderWidth: 1,
    borderColor: Theme.colors.telemetryBorder,
    borderRadius: Theme.radius.sm,
    padding: Theme.spacing.sm,
    marginTop: Theme.spacing.sm,
  },
  reasonText: {
    flexShrink: 1,
    fontSize: Theme.typography.caption,
    color: Theme.colors.text,
    lineHeight: 18,
  },
  map: { marginTop: Theme.spacing.md },
  caveats: { marginTop: Theme.spacing.sm, gap: 6 },
  caveatRow: {
    flexDirection: 'row',
    gap: Theme.spacing.sm,
    alignItems: 'flex-start',
  },
  caveatText: {
    flexShrink: 1,
    fontSize: Theme.typography.caption,
    color: Theme.colors.caution,
    lineHeight: 18,
  },
  caveatGood: { color: Theme.colors.passable },
  offlineBox: {
    flexDirection: 'row',
    gap: Theme.spacing.sm,
    alignItems: 'flex-start',
    backgroundColor: Theme.colors.cautionBg,
    borderWidth: 1,
    borderColor: Theme.colors.cautionBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
  },
  offlineText: {
    flexShrink: 1,
    fontSize: Theme.typography.caption,
    color: Theme.colors.text,
    lineHeight: 18,
  },
  revisionBox: {
    backgroundColor: Theme.colors.cautionBg,
    borderWidth: 1,
    borderColor: Theme.colors.cautionBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    gap: Theme.spacing.sm,
  },
  revisionHead: {
    flexDirection: 'row',
    gap: Theme.spacing.sm,
    alignItems: 'center',
  },
  revisionTitle: {
    flexShrink: 1,
    fontSize: Theme.typography.body,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  revisionBody: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.text,
    lineHeight: 18,
  },
  action: {
    backgroundColor: Theme.colors.brand,
    borderRadius: Theme.radius.md,
    paddingVertical: 13,
    alignItems: 'center',
  },
  actionBusy: { opacity: 0.6 },
  actionText: {
    color: '#FFFFFF',
    fontSize: Theme.typography.body,
    fontWeight: '700',
  },
});
