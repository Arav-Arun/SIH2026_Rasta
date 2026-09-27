import React, { useCallback, useEffect, useState } from 'react';
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

  if (!view) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={Theme.colors.brand} />
        <Text style={styles.emptyBody}>Reading the route on this device…</Text>
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
              ? `Showing the route saved on this device. ${view.offlineReason}`
              : view.offlineReason}
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
          <Text style={styles.sectionLabel}>This trip</Text>
          <Text style={styles.reference}>
            {view.trip.consignment_reference ?? view.trip.consignment_id}
          </Text>
          <Text style={styles.meta}>
            {view.trip.vehicle_registration ?? 'Vehicle not named'},{' '}
            {view.trip.status.replace(/_/g, ' ')}
          </Text>
        </MinimalCard>
      )}
    </ScrollView>
  );
}

const FRESHNESS_TEXT: Record<
  RoutePackFreshness,
  { label: string; body: string; tone: 'good' | 'warn' | 'bad' }
> = {
  confirmed_current: {
    label: 'Confirmed with the control room',
    body: 'This is the route the control room has approved.',
    tone: 'good',
  },
  cached_unverified: {
    label: 'Saved on this device, not just confirmed',
    body: 'Pull down to check with the control room that it still stands.',
    tone: 'warn',
  },
  cached_offline: {
    label: 'Saved on this device',
    body: 'The control room cannot be reached, so this may have changed since.',
    tone: 'warn',
  },
  replaced: {
    label: 'Replaced by the control room',
    body: 'Do not follow this route. Contact the control room for the new one.',
    tone: 'bad',
  },
  withdrawn: {
    label: 'Withdrawn by the control room',
    body: 'This route was rejected. Contact the control room before setting off.',
    tone: 'bad',
  },
  none: { label: 'No route', body: '', tone: 'warn' },
};

function RouteCard({
  pack,
  freshness,
}: {
  pack: RoutePack;
  freshness: RoutePackFreshness;
}) {
  const state = FRESHNESS_TEXT[freshness];
  const eta = formatEtaRange(pack.eta_range_seconds);
  const verifiedAge = formatAge(secondsSince(pack.verified_at, new Date()));

  return (
    <MinimalCard>
      <View style={styles.cardHead}>
        <Text style={styles.sectionLabel}>Approved route</Text>
        <Text style={[styles.badge, styles[`badge_${state.tone}`]]}>
          {state.label}
        </Text>
      </View>

      <View style={styles.figures}>
        <View style={styles.figure}>
          <Text style={styles.figureValue}>
            {formatDistance(pack.distance_m)}
          </Text>
          <Text style={styles.figureLabel}>Distance</Text>
        </View>
        <View style={styles.figure}>
          {/* An ETA is a range. A single number would read as a promise about
              road whose state nobody has observed. */}
          <Text style={styles.figureValue}>{eta ?? 'Not estimated'}</Text>
          <Text style={styles.figureLabel}>Time on the road</Text>
        </View>
        <View style={styles.figure}>
          <Text style={styles.figureValue}>{pack.segment_ids.length}</Text>
          <Text style={styles.figureLabel}>Road sections</Text>
        </View>
      </View>

      {/* Drawn only when the server actually sent geometry. A straight line
          between endpoints would be a road that does not exist. */}
      <View style={styles.map}>
        <RouteLineMap geometry={pack.geometry} />
      </View>

      <Text style={styles.stateBody}>{state.body}</Text>
      <Text style={styles.meta}>Last confirmed {verifiedAge}</Text>

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
            text={`Avoids ${pack.avoided_closures} confirmed ${
              pack.avoided_closures === 1 ? 'closure' : 'closures'
            }.`}
          />
        )}
        {pack.unknown_constraints > 0 && (
          <Caveat
            tone="warn"
            icon={<ShieldQuestion size={14} color={Theme.colors.caution} />}
            text={`${pack.unknown_constraints} limit${
              pack.unknown_constraints === 1 ? '' : 's'
            } on this route are unverified. Check them on the ground.`}
          />
        )}
        {pack.segments_without_a_risk_score > 0 && (
          <Caveat
            tone="warn"
            icon={<ShieldQuestion size={14} color={Theme.colors.caution} />}
            text={`${pack.segments_without_a_risk_score} sections have no risk score recorded. That is unknown, not safe.`}
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
  const eta = formatEtaRange(incoming.eta_range_seconds);
  return (
    <View style={styles.revisionBox}>
      <View style={styles.revisionHead}>
        <AlertTriangle size={16} color={Theme.colors.caution} />
        <Text style={styles.revisionTitle}>
          The control room changed your route
        </Text>
      </View>
      <Text style={styles.revisionBody}>
        You are still shown the route you were following. The new one is{' '}
        {formatDistance(incoming.distance_m)}
        {eta ? ` and about ${eta}` : ''}
        {incoming.unknown_constraints > 0
          ? `, with ${incoming.unknown_constraints} unverified limit${
              incoming.unknown_constraints === 1 ? '' : 's'
            }`
          : ''}
        .
      </Text>
      <TouchableOpacity
        style={[styles.action, busy && styles.actionBusy]}
        disabled={busy}
        onPress={onAccept}
        accessibilityRole="button"
        accessibilityLabel="Acknowledge the new route and follow it"
      >
        <Text style={styles.actionText}>
          {busy ? 'Saving…' : 'I have read the new route'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const ABSENCE_TEXT: Record<string, { title: string; body: string }> = {
  no_trip: {
    title: 'No trip assigned',
    body: 'A route appears once the control room assigns you a load and approves a route for it.',
  },
  no_route_plan: {
    title: 'No approved route yet',
    body: 'You have a trip, but the control room has not approved a route for it. You may be dispatched without one; ask them before setting off.',
  },
  plan_unreadable: {
    title: 'The route could not be read',
    body: 'The control room has a plan for this trip but no chosen route on it. Ask them to approve one.',
  },
};

function AbsenceCard({
  absence,
  localOnly,
}: {
  absence: DriverRouteView['absence'];
  localOnly: boolean;
}) {
  const text = ABSENCE_TEXT[absence ?? 'no_trip'];
  return (
    <MinimalCard>
      <View style={styles.empty}>
        <MapPin size={22} color={Theme.colors.textDim} />
        <Text style={styles.emptyTitle}>{text.title}</Text>
        <Text style={styles.emptyBody}>
          {localOnly
            ? 'Sign in with your district account to receive trips and routes.'
            : text.body}
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
