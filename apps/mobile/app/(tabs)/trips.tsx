import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { ArrowRight, PackageCheck, Truck } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { useT } from '../../contexts/LocaleContext';
import { useCrew } from '../../contexts/SessionContext';
import { MinimalCard } from '../../components/ui/MinimalCard';
import { TrackerPanel } from '../../components/driver/TrackerPanel';
import {
  type Consignment,
  type DriverTripAction,
  type Trip,
  listConsignments,
  listTrips,
  transitionTrip,
} from '../../services/rastaApi';
import { formatQuantity } from '../../services/logistics';
import { newUuid } from '../../services/ids';
import { stopTracking } from '../../services/tracker';

/** The load this driver is carrying. */
export default function TripsScreen() {
  const crew = useCrew();
  const t = useT();
  const router = useRouter();

  const [trips, setTrips] = useState<Trip[] | null>(null);
  const [loads, setLoads] = useState<Map<string, Consignment>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const isLocal = crew.mode === 'local_only';

  const load = useCallback(async () => {
    // A session without an account is never assigned a load.
    if (isLocal) {
      setTrips([]);
      setLoads(new Map());
      setError(null);
      return;
    }

    const [tripResult, consignmentResult] = await Promise.all([
      listTrips(),
      listConsignments(),
    ]);
    if (!tripResult.ok) {
      setTrips(null);
      setError(tripResult.reason);
      return;
    }
    setTrips(tripResult.data.trips);
    setError(null);
    if (consignmentResult.ok) {
      setLoads(
        new Map(
          consignmentResult.data.consignments.map((row) => [row.id, row]),
        ),
      );
    }
  }, [isLocal]);

  // Reload whenever the tab comes back into view, such as after a receipt.
  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );

  async function move(trip: Trip, action: DriverTripAction) {
    setBusyId(trip.id);
    try {
      const result = await transitionTrip(trip.id, action, newUuid());
      if (!result.ok) {
        setError(result.reason);
        return;
      }
      setError(null);
      if (action === 'failed') await stopTracking();
      await load();
    } finally {
      setBusyId(null);
    }
  }

  const open = (trips ?? []).filter(
    (trip) => trip.status !== 'completed' && trip.status !== 'cancelled',
  );
  const done = (trips ?? []).filter(
    (trip) => trip.status === 'completed' || trip.status === 'cancelled',
  );

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
      {error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{t(error)}</Text>
          <TouchableOpacity onPress={load} accessibilityRole="button">
            <Text style={styles.errorAction}>{t('mobile.home.retry')}</Text>
          </TouchableOpacity>
        </View>
      )}

      {trips === null && !error && (
        <View style={styles.centered}>
          <ActivityIndicator color={Theme.colors.brand} />
        </View>
      )}

      {/* Whether this trip is reporting its position belongs next to the trip,
          not on a settings screen a driver would never open. */}
      {isLocal ? null : <TrackerPanel compact />}

      {trips?.length === 0 && (
        <MinimalCard style={styles.empty}>
          <Truck size={22} color={Theme.colors.textDim} />
          <Text style={styles.emptyTitle}>{t('mobile.trips.noLoad')}</Text>
          <Text style={styles.emptyBody}>
            {isLocal
              ? t('mobile.trips.signInForLoads')
              : t('mobile.trips.noLoadBody')}
          </Text>
        </MinimalCard>
      )}

      {open.map((trip) => (
        <TripCard
          key={trip.id}
          trip={trip}
          consignment={loads.get(trip.consignment_id) ?? null}
          busy={busyId === trip.id}
          onMove={(action) => move(trip, action)}
          onReceipt={() => router.push(`/receipt/${trip.id}`)}
        />
      ))}

      {done.length > 0 && (
        <>
          <Text style={styles.sectionLabel}>{t('mobile.trips.finished')}</Text>
          {done.map((trip) => (
            <TripCard
              key={trip.id}
              trip={trip}
              consignment={loads.get(trip.consignment_id) ?? null}
              busy={false}
              onMove={() => undefined}
              onReceipt={() => router.push(`/receipt/${trip.id}`)}
            />
          ))}
        </>
      )}
    </ScrollView>
  );
}

function TripCard({
  trip,
  consignment,
  busy,
  onMove,
  onReceipt,
}: {
  trip: Trip;
  consignment: Consignment | null;
  busy: boolean;
  onMove: (action: DriverTripAction) => void;
  onReceipt: () => void;
}) {
  const t = useT();
  return (
    <MinimalCard style={styles.card}>
      <View style={styles.cardHead}>
        <Text style={styles.status}>
          {t(`mobile.trips.status.${trip.status}`)}
        </Text>
        <Text style={styles.vehicle}>{trip.vehicle_registration ?? '-'}</Text>
      </View>

      <Text style={styles.reference}>
        {trip.consignment_reference ??
          consignment?.reference ??
          t('mobile.trips.consignment')}
      </Text>

      {consignment && (
        <View style={styles.routeRow}>
          <Text style={styles.routeText} numberOfLines={1}>
            {consignment.origin_facility_name ?? t('mobile.trips.origin')}
          </Text>
          <ArrowRight size={13} color={Theme.colors.textMuted} />
          <Text style={styles.routeText} numberOfLines={1}>
            {consignment.destination_facility_name ??
              t('mobile.delivery.destination')}
          </Text>
        </View>
      )}

      {consignment && consignment.items.length > 0 && (
        <View style={styles.manifest}>
          {consignment.items.map((item) => (
            <View key={item.id} style={styles.manifestRow}>
              <Text style={styles.manifestCommodity} numberOfLines={1}>
                {item.commodity}
              </Text>
              <Text style={styles.manifestQuantity}>
                {t('mobile.trips.quantity', {
                  quantity: formatQuantity(item.quantity),
                  unit: item.unit,
                })}
              </Text>
            </View>
          ))}
        </View>
      )}

      {/* Only the moves this seat owns. Start and pause are the driver's; the
          dispatcher offers and cancels. */}
      {trip.status === 'awaiting_driver' && (
        <PrimaryButton
          busy={busy}
          label={t('mobile.trips.start')}
          onPress={() => onMove('active')}
        />
      )}
      {trip.status === 'active' && (
        <>
          <PrimaryButton
            busy={false}
            label={t('mobile.delivery.title')}
            onPress={onReceipt}
          />
          <TouchableOpacity
            style={styles.secondaryAction}
            onPress={() => onMove('paused')}
            disabled={busy}
            accessibilityRole="button"
          >
            <Text style={styles.secondaryActionText}>
              {t('mobile.trips.pause')}
            </Text>
          </TouchableOpacity>
        </>
      )}
      {trip.status === 'paused' && (
        <PrimaryButton
          busy={busy}
          label={t('mobile.trips.resume')}
          onPress={() => onMove('active')}
        />
      )}
      {trip.status === 'planned' && (
        <Text style={styles.waiting}>{t('mobile.trips.waitingHandover')}</Text>
      )}
      {trip.status === 'completed' && (
        <View style={styles.completedRow}>
          <PackageCheck size={14} color={Theme.colors.passable} />
          <Text style={styles.completedText}>
            {t('mobile.trips.receiptRecorded')}
          </Text>
        </View>
      )}
    </MinimalCard>
  );
}

function PrimaryButton({
  busy,
  label,
  onPress,
}: {
  busy: boolean;
  label: string;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity
      style={styles.action}
      onPress={onPress}
      disabled={busy}
      accessibilityRole="button"
    >
      {busy ? (
        <ActivityIndicator size="small" color="#FFFFFF" />
      ) : (
        <Text style={styles.actionText}>{label}</Text>
      )}
    </TouchableOpacity>
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
  card: { gap: Theme.spacing.sm },
  cardHead: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  status: {
    fontSize: Theme.typography.caption,
    fontWeight: '700',
    color: Theme.colors.telemetry,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  vehicle: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
  },
  reference: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  routeRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  routeText: {
    flexShrink: 1,
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
  },
  manifest: {
    borderTopWidth: 1,
    borderTopColor: Theme.colors.borderSubtle,
    paddingTop: Theme.spacing.sm,
    gap: 4,
  },
  manifestRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: Theme.spacing.sm,
  },
  manifestCommodity: {
    flexShrink: 1,
    fontSize: Theme.typography.body,
    color: Theme.colors.text,
  },
  manifestQuantity: {
    fontSize: Theme.typography.body,
    fontWeight: '600',
    color: Theme.colors.text,
    fontVariant: ['tabular-nums'],
  },
  action: {
    marginTop: Theme.spacing.xs,
    backgroundColor: Theme.colors.brand,
    borderRadius: Theme.radius.md,
    paddingVertical: 13,
    alignItems: 'center',
  },
  actionText: {
    color: '#FFFFFF',
    fontSize: Theme.typography.body,
    fontWeight: '700',
  },
  secondaryAction: {
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    paddingVertical: 11,
    alignItems: 'center',
  },
  secondaryActionText: {
    color: Theme.colors.text,
    fontSize: Theme.typography.body,
    fontWeight: '600',
  },
  waiting: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
  },
  completedRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  completedText: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.passable,
  },
  sectionLabel: {
    marginTop: Theme.spacing.sm,
    fontSize: Theme.typography.caption,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  errorBox: {
    borderWidth: 1,
    borderColor: Theme.colors.blockedBorder,
    backgroundColor: Theme.colors.blockedBg,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    gap: 6,
  },
  errorText: { fontSize: Theme.typography.body, color: Theme.colors.blocked },
  errorAction: {
    fontSize: Theme.typography.body,
    fontWeight: '700',
    color: Theme.colors.blocked,
  },
});
