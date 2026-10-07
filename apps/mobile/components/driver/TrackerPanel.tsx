import { useCallback, useEffect, useState } from 'react';
import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import {
  Activity,
  AlertTriangle,
  CloudUpload,
  Radio,
  ShieldAlert,
} from 'lucide-react-native';

import { Theme } from '../../constants/theme';
import { MinimalCard } from '../ui/MinimalCard';
import { activeTrip } from '../../services/driverRoute';
import { listTrips, type Trip } from '../../services/rastaApi';
import {
  drainQueue,
  restoreTracker,
  startTracking,
  stopTracking,
  subscribeToTracker,
  type TrackerSnapshot,
} from '../../services/tracker';
import { describeQueue } from '../../services/telemetryQueue';
import { formatAge, secondsSince } from '../../services/routePack';

/** What the tracker is doing, and what it cannot do. */
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';

export function TrackerPanel({ compact = false }: { compact?: boolean }) {
  const crew = useCrew();
  const t = useT();
  const [snapshot, setSnapshot] = useState<TrackerSnapshot | null>(null);
  const [trip, setTrip] = useState<Trip | null>(null);
  const [busy, setBusy] = useState(false);

  const isLocal = crew.mode === 'local_only';

  useEffect(() => {
    void restoreTracker();
    const unsubscribe = subscribeToTracker(setSnapshot);
    void (async () => {
      if (isLocal) return;
      const trips = await listTrips();
      if (trips.ok) setTrip(activeTrip(trips.data.trips));
    })();
    return unsubscribe;
  }, [isLocal]);

  const toggle = useCallback(async () => {
    if (!snapshot) return;
    setBusy(true);
    try {
      if (snapshot.state === 'running' || snapshot.state === 'starting') {
        await stopTracking();
        return;
      }
      const target = trip?.id ?? snapshot.tripId;
      if (target) await startTracking(target);
    } finally {
      setBusy(false);
    }
  }, [snapshot, trip]);

  const send = useCallback(async () => {
    const target = trip?.id ?? snapshot?.tripId;
    if (!target) return;
    setBusy(true);
    try {
      await drainQueue(target);
    } finally {
      setBusy(false);
    }
  }, [snapshot, trip]);

  if (!snapshot) return null;

  const running = snapshot.state === 'running' || snapshot.state === 'starting';
  const trackable = trip?.status === 'active' || trip?.status === 'paused';
  const stats = snapshot.stats;

  return (
    <MinimalCard>
      <View style={styles.head}>
        <Text style={styles.label}>{t('mobile.tracker.heading')}</Text>
        <View style={styles.stateRow}>
          <Radio
            size={13}
            color={running ? Theme.colors.passable : Theme.colors.textDim}
          />
          <Text style={[styles.state, running && styles.stateOn]}>
            {running ? t('mobile.tracker.on') : t('mobile.tracker.off')}
          </Text>
        </View>
      </View>

      {!trackable && (
        <Text style={styles.body}>
          {trip ? t('mobile.tracker.notStarted') : t('mobile.tracker.noTrip')}
        </Text>
      )}

      {snapshot.state === 'permission_denied' && (
        <View style={styles.warnBox} accessibilityRole="alert">
          <ShieldAlert size={14} color={Theme.colors.blocked} />
          <Text style={styles.warnText}>
            {t('mobile.tracker.permissionHelp', {
              message: snapshot.message ?? '',
            })}
          </Text>
        </View>
      )}

      {snapshot.state === 'error' && snapshot.message && (
        <View style={styles.warnBox} accessibilityRole="alert">
          <AlertTriangle size={14} color={Theme.colors.blocked} />
          <Text style={styles.warnText}>{t(snapshot.message)}</Text>
        </View>
      )}

      {/* What switching reporting on means, said before the system asks for
          location permission, not after. */}
      {!running && trackable && (
        <View style={styles.noteBox}>
          <Text style={styles.noteText}>{t('mobile.tracker.consent')}</Text>
        </View>
      )}

      {/* The limit, stated where it matters rather than in a footnote. */}
      {running && (
        <View style={styles.noteBox}>
          <Text style={styles.noteText}>
            {t('mobile.tracker.foregroundOnly')}
          </Text>
        </View>
      )}

      <View style={styles.figures}>
        <View style={styles.figure}>
          <Text style={styles.figureValue}>{stats.queued}</Text>
          <Text style={styles.figureLabel}>{t('mobile.tracker.waiting')}</Text>
        </View>
        <View style={styles.figure}>
          <Text style={styles.figureValue}>{stats.uploaded}</Text>
          <Text style={styles.figureLabel}>{t('mobile.tracker.sent')}</Text>
        </View>
        <View style={styles.figure}>
          <Text style={styles.figureValue}>{stats.rejected}</Text>
          <Text style={styles.figureLabel}>{t('mobile.tracker.refused')}</Text>
        </View>
      </View>

      <Text style={styles.meta}>
        {t('mobile.tracker.lastFix', {
          fix: t(formatAge(secondsSince(snapshot.lastFixAt, new Date()))),
          sent: t(formatAge(secondsSince(stats.last_upload_at, new Date()))),
        })}
      </Text>

      {!compact && (
        <>
          <Text style={styles.meta}>{describeQueue(stats, t)}</Text>
          {Object.entries(stats.rejected_by_reason).length > 0 && (
            <View style={styles.reasons}>
              <Text style={styles.reasonsLabel}>
                {t('mobile.tracker.refusedBy')}
              </Text>
              {Object.entries(stats.rejected_by_reason).map(
                ([reason, count]) => (
                  <Text key={reason} style={styles.reasonLine}>
                    {count} × {reason.replace(/_/g, ' ')}
                  </Text>
                ),
              )}
            </View>
          )}
          {Object.entries(stats.discarded_by_reason).length > 0 && (
            <View style={styles.reasons}>
              <Text style={styles.reasonsLabel}>
                {t('mobile.tracker.dropped')}
              </Text>
              {Object.entries(stats.discarded_by_reason).map(
                ([reason, count]) => (
                  <Text key={reason} style={styles.reasonLine}>
                    {count} × {reason.replace(/_/g, ' ')}
                  </Text>
                ),
              )}
            </View>
          )}
          {stats.last_error && (
            <Text style={styles.errorLine}>{t(stats.last_error)}</Text>
          )}
        </>
      )}

      <View style={styles.actions}>
        <TouchableOpacity
          style={[styles.action, (!trackable || busy) && styles.actionDisabled]}
          disabled={!trackable || busy}
          onPress={toggle}
          accessibilityRole="button"
          accessibilityLabel={
            running
              ? t('mobile.tracker.stopLabel')
              : t('mobile.tracker.startLabel')
          }
        >
          <Activity size={14} color="#FFFFFF" />
          <Text style={styles.actionText}>
            {running ? t('mobile.tracker.stop') : t('mobile.tracker.start')}
          </Text>
        </TouchableOpacity>

        {stats.queued > 0 && (
          <TouchableOpacity
            style={[styles.secondary, busy && styles.actionDisabled]}
            disabled={busy}
            onPress={send}
            accessibilityRole="button"
            accessibilityLabel={t('mobile.tracker.sendNowLabel')}
          >
            <CloudUpload size={14} color={Theme.colors.brand} />
            <Text style={styles.secondaryText}>
              {t('mobile.tracker.sendNow')}
            </Text>
          </TouchableOpacity>
        )}
      </View>
    </MinimalCard>
  );
}

const styles = StyleSheet.create({
  head: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  label: {
    fontSize: Theme.typography.caption,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  stateRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  state: {
    fontSize: Theme.typography.caption,
    fontWeight: '700',
    color: Theme.colors.textDim,
  },
  stateOn: { color: Theme.colors.passable },
  body: {
    marginTop: Theme.spacing.sm,
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
    lineHeight: 20,
  },
  figures: {
    flexDirection: 'row',
    gap: Theme.spacing.md,
    marginTop: Theme.spacing.md,
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
  meta: {
    marginTop: Theme.spacing.sm,
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
  },
  reasons: { marginTop: Theme.spacing.sm, gap: 2 },
  reasonsLabel: {
    fontSize: Theme.typography.micro,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  reasonLine: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.caution,
  },
  errorLine: {
    marginTop: Theme.spacing.sm,
    fontSize: Theme.typography.caption,
    color: Theme.colors.blocked,
  },
  warnBox: {
    flexDirection: 'row',
    gap: Theme.spacing.sm,
    alignItems: 'flex-start',
    marginTop: Theme.spacing.sm,
    backgroundColor: Theme.colors.blockedBg,
    borderWidth: 1,
    borderColor: Theme.colors.blockedBorder,
    borderRadius: Theme.radius.sm,
    padding: Theme.spacing.sm,
  },
  warnText: {
    flexShrink: 1,
    fontSize: Theme.typography.caption,
    color: Theme.colors.text,
    lineHeight: 18,
  },
  noteBox: {
    marginTop: Theme.spacing.sm,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.sm,
    padding: Theme.spacing.sm,
  },
  noteText: {
    flexShrink: 1,
    fontSize: Theme.typography.caption,
    color: Theme.colors.text,
    lineHeight: 18,
  },
  actions: {
    flexDirection: 'row',
    gap: Theme.spacing.sm,
    marginTop: Theme.spacing.md,
  },
  action: {
    flex: 1,
    flexDirection: 'row',
    gap: 6,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: Theme.colors.brand,
    borderRadius: Theme.radius.md,
    paddingVertical: 12,
  },
  actionDisabled: { opacity: 0.5 },
  actionText: {
    color: '#FFFFFF',
    fontSize: Theme.typography.body,
    fontWeight: '700',
  },
  secondary: {
    flexDirection: 'row',
    gap: 6,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    paddingVertical: 12,
    paddingHorizontal: Theme.spacing.md,
  },
  secondaryText: {
    color: Theme.colors.brand,
    fontSize: Theme.typography.body,
    fontWeight: '700',
  },
});
