import { useEffect, useState } from 'react';
import {
  Alert,
  Linking,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  CheckCircle2,
  Phone,
  RotateCcw,
  Siren,
  XCircle,
} from 'lucide-react-native';
import * as Haptics from 'expo-haptics';
import { Theme } from '../../constants/theme';
import { MinimalCard } from '../../components/ui/MinimalCard';
import { captureGpsFix } from '../../services/capturePipeline';
import {
  ConnectivityFacility,
  getConnectivitySummary,
  getMe,
} from '../../services/rastaApi';
import { flushSos, queueSos, type SosDelivery } from '../../services/sosQueue';

type SosState = 'standby' | 'counting_down' | 'dispatched';

type ControlRoomState = SosDelivery | 'sending';

/** How often a saved SOS is retried while this screen is open. */
const RETRY_MS = 20_000;

function controlRoomText(state: ControlRoomState): string {
  if (state === 'sending') return 'Alerting the control room…';
  if (state.state === 'sent') {
    if (state.recipients === 0) {
      return 'The control room has the alert, but nobody is assigned to this district to receive it.';
    }
    return state.recipients === 1
      ? 'Control room alerted: 1 person notified.'
      : `Control room alerted: ${state.recipients} people notified.`;
  }
  if (state.state === 'waiting') {
    return `Saved on this phone. The control room is alerted as soon as it can be reached. ${state.reason}`;
  }
  return `The control room could not take the alert: ${state.reason} The message to 112 is unaffected.`;
}

/** Time to cancel an accidental tap before the message opens. */
const COUNTDOWN_SECONDS = 5;

/** Only numbers published nationally are listed. */
const HELPLINES = [
  { number: '112', name: 'Emergency response (police, fire, ambulance)' },
  { number: '108', name: 'Ambulance' },
  { number: '1070', name: 'State disaster helpline' },
] as const;

const FACILITY_STATUS: Record<ConnectivityFacility['status'], string> = {
  reachable: 'Reachable',
  isolated: 'No route',
  unknown_coverage: 'Unknown',
};

function haptic(kind: 'light' | 'medium' | 'heavy' | 'warning' | 'success') {
  if (Platform.OS === 'web') return;
  try {
    if (kind === 'warning') {
      void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
    } else if (kind === 'success') {
      void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } else {
      void Haptics.impactAsync(
        kind === 'heavy'
          ? Haptics.ImpactFeedbackStyle.Heavy
          : kind === 'medium'
            ? Haptics.ImpactFeedbackStyle.Medium
            : Haptics.ImpactFeedbackStyle.Light,
      );
    }
  } catch {
    // A phone without haptics still opens the message.
  }
}

export default function EmergencySosScreen() {
  const [facilities, setFacilities] = useState<ConnectivityFacility[] | null>(
    null,
  );
  const [facilityRefusal, setFacilityRefusal] = useState<string | null>(null);
  const [sosState, setSosState] = useState<SosState>('standby');
  const [countdown, setCountdown] = useState(COUNTDOWN_SECONDS);
  const [message, setMessage] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [controlRoom, setControlRoom] = useState<ControlRoomState | null>(null);

  // An SOS saved while offline is retried on opening this screen and every
  // RETRY_MS while it is open and still waiting.
  useEffect(() => {
    let cancelled = false;
    void flushSos().then((result) => {
      if (!cancelled && result) setControlRoom(result);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (
      !controlRoom ||
      controlRoom === 'sending' ||
      controlRoom.state !== 'waiting'
    ) {
      return;
    }
    const timer = setInterval(() => {
      void flushSos().then((result) => {
        if (result) setControlRoom(result);
      });
    }, RETRY_MS);
    return () => clearInterval(timer);
  }, [controlRoom]);

  // The district's real facilities.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const me = await getMe();
      if (cancelled) return;
      if (!me.ok) {
        setFacilityRefusal(me.reason);
        return;
      }
      const district = me.data.districts[0];
      if (!district) {
        setFacilityRefusal(
          'This account has no district scope, so no facility list applies.',
        );
        return;
      }
      const summary = await getConnectivitySummary(district.id);
      if (cancelled) return;
      if (!summary.ok) {
        setFacilityRefusal(summary.reason);
        return;
      }
      setFacilityRefusal(null);
      setFacilities(summary.data.facility_status);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // One tick per second; at zero the message opens.
  useEffect(() => {
    if (sosState !== 'counting_down') return;
    if (countdown === 0) {
      void openMessage();
      return;
    }
    const timer = setTimeout(() => {
      haptic('medium');
      setCountdown((value) => value - 1);
    }, 1000);
    return () => clearTimeout(timer);
  }, [sosState, countdown]);

  function arm() {
    haptic('heavy');
    setNote(null);
    setMessage(null);
    setCountdown(COUNTDOWN_SECONDS);
    setSosState('counting_down');
  }

  function cancel() {
    haptic('success');
    setSosState('standby');
    setCountdown(COUNTDOWN_SECONDS);
    setNote('Cancelled. Nothing was sent.');
  }

  function clear() {
    haptic('light');
    setSosState('standby');
    setMessage(null);
    setNote(null);
  }

  /** Compose the message a responder will read, from what is actually known. */
  async function openMessage(alertControlRoom = true) {
    haptic('warning');
    setSosState('dispatched');
    setMessage('Getting this phone’s position…');

    const pressedAt = new Date().toISOString();
    const gps = await captureGpsFix();
    const lines = ['RASTA SOS', `TIME: ${new Date().toISOString()}`];
    if (gps.ok) {
      lines.push(
        `POSITION: ${gps.fix.latitude.toFixed(5)}, ${gps.fix.longitude.toFixed(5)}`,
        gps.fix.accuracyMeters != null
          ? `ACCURACY: ${Math.round(gps.fix.accuracyMeters)} m`
          : 'ACCURACY: not reported',
        `FIX TAKEN: ${gps.fix.takenAt}`,
      );
    } else {
      lines.push(
        'POSITION: NOT AVAILABLE ON THIS DEVICE',
        `REASON: ${gps.reason}`,
        'Describe your location in this message before sending.',
      );
    }
    const body = lines.join('\n');
    setMessage(body);

    // Opened in the phone's messaging app, not sent silently: the person keeps
    // the last word on what goes to an emergency number, and can add what the
    // device cannot know.
    Linking.openURL(`sms:112?body=${encodeURIComponent(body)}`).catch(() => {
      Alert.alert('Message ready to send', body);
    });

    // The control room hears at the same time, from the same fix. Opening the
    // message again does not raise a second alert.
    if (!alertControlRoom) return;
    setControlRoom('sending');
    void queueSos({
      captured_at: pressedAt,
      latitude: gps.ok ? gps.fix.latitude : null,
      longitude: gps.ok ? gps.fix.longitude : null,
      accuracy_m: gps.ok ? (gps.fix.accuracyMeters ?? null) : null,
    }).then(setControlRoom);
  }

  const counting = sosState === 'counting_down';

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <MinimalCard>
        <Text style={styles.cardTitle}>Send an SOS message</Text>
        <Text style={styles.cardBody}>
          Opens a text message to 112 with this phone&apos;s position. You check
          it and send it. The control room is alerted at the same time.
        </Text>

        <View style={styles.buttonArea}>
          <TouchableOpacity
            activeOpacity={0.85}
            style={[styles.sosButton, counting && styles.sosButtonCounting]}
            onPress={counting ? cancel : arm}
            accessibilityRole="button"
            accessibilityLabel={
              counting
                ? `Cancel. The message opens in ${countdown} seconds.`
                : 'Start an SOS message'
            }
          >
            {counting ? (
              <>
                <Text style={styles.countdown}>{countdown}</Text>
                <Text style={styles.sosHint}>Tap to cancel</Text>
              </>
            ) : (
              <>
                <Siren size={32} color="#FFFFFF" />
                <Text style={styles.sosLabel}>SOS</Text>
              </>
            )}
          </TouchableOpacity>
        </View>

        {counting ? (
          <TouchableOpacity
            style={styles.cancelButton}
            onPress={cancel}
            accessibilityRole="button"
          >
            <XCircle size={16} color={Theme.colors.text} />
            <Text style={styles.cancelText}>Cancel</Text>
          </TouchableOpacity>
        ) : null}

        {note && sosState === 'standby' ? (
          <View style={styles.noteRow}>
            <CheckCircle2 size={14} color={Theme.colors.passable} />
            <Text style={styles.noteText}>{note}</Text>
          </View>
        ) : null}

        {sosState === 'dispatched' && message ? (
          <View style={styles.message}>
            <Text style={styles.messageHeading}>Message for 112</Text>
            <Text style={styles.messageBody}>{message}</Text>
            <Text style={styles.caveat}>
              This phone opened the message for you to send. It cannot confirm
              that anyone received it.
            </Text>
            {controlRoom ? (
              <Text style={styles.caveat} accessibilityLiveRegion="polite">
                {controlRoomText(controlRoom)}
              </Text>
            ) : null}
            <View style={styles.messageActions}>
              <TouchableOpacity
                style={styles.secondaryButton}
                onPress={() => void openMessage(false)}
                accessibilityRole="button"
              >
                <RotateCcw size={14} color={Theme.colors.brand} />
                <Text style={styles.secondaryText}>Open again</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.secondaryButton}
                onPress={clear}
                accessibilityRole="button"
              >
                <Text style={styles.secondaryText}>Done</Text>
              </TouchableOpacity>
            </View>
          </View>
        ) : null}
      </MinimalCard>

      <Text style={styles.sectionTitle}>Helplines</Text>
      {HELPLINES.map((line) => (
        <MinimalCard key={line.number} style={styles.row}>
          <Text style={styles.rowName}>{line.name}</Text>
          <TouchableOpacity
            style={styles.callButton}
            onPress={() =>
              Linking.openURL(`tel:${line.number}`).catch(() => {})
            }
            accessibilityRole="button"
            accessibilityLabel={`Call ${line.number}`}
          >
            <Phone size={14} color="#FFFFFF" />
            <Text style={styles.callText}>{line.number}</Text>
          </TouchableOpacity>
        </MinimalCard>
      ))}

      <Text style={styles.sectionTitle}>Facilities in this district</Text>
      {facilityRefusal ? (
        <MinimalCard>
          <Text style={styles.rowName}>No facility list</Text>
          <Text style={styles.rowDetail}>{facilityRefusal}</Text>
        </MinimalCard>
      ) : null}
      {facilities?.length === 0 ? (
        <MinimalCard>
          <Text style={styles.rowDetail}>
            No facilities on record for this district.
          </Text>
        </MinimalCard>
      ) : null}
      {(facilities ?? []).map((facility) => (
        <MinimalCard key={facility.facility_id} style={styles.facility}>
          <View style={styles.facilityTop}>
            <Text style={[styles.rowName, styles.facilityName]}>
              {facility.name}
            </Text>
            <Text
              style={[
                styles.status,
                facility.status === 'isolated' && styles.statusBlocked,
              ]}
            >
              {FACILITY_STATUS[facility.status]}
            </Text>
          </View>
          <Text style={styles.rowDetail}>
            {facility.type.replace(/_/g, ' ')}
            {facility.location
              ? `, ${facility.location.latitude.toFixed(4)}, ${facility.location.longitude.toFixed(4)}`
              : ', no location on record'}
          </Text>
        </MinimalCard>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Theme.colors.bg },
  content: {
    padding: Theme.spacing.lg,
    paddingBottom: Theme.spacing.xxl,
    gap: Theme.spacing.sm,
  },
  cardTitle: { fontSize: 17, fontWeight: '700', color: Theme.colors.text },
  cardBody: {
    fontSize: 14,
    lineHeight: 20,
    color: Theme.colors.textMuted,
    marginTop: 4,
  },
  buttonArea: { alignItems: 'center', paddingVertical: Theme.spacing.xl },
  sosButton: {
    width: 156,
    height: 156,
    borderRadius: 78,
    backgroundColor: Theme.colors.sosRed,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
  },
  sosButtonCounting: { backgroundColor: '#7F1D1D' },
  sosLabel: {
    fontSize: 28,
    fontWeight: '800',
    color: '#FFFFFF',
    letterSpacing: 1,
  },
  countdown: { fontSize: 48, fontWeight: '800', color: '#FFFFFF' },
  sosHint: { fontSize: 12, fontWeight: '600', color: '#FFFFFF' },
  cancelButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    paddingVertical: 12,
  },
  cancelText: { fontSize: 15, fontWeight: '700', color: Theme.colors.text },
  noteRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  noteText: { fontSize: 13, color: Theme.colors.passable },
  message: {
    borderTopWidth: 1,
    borderTopColor: Theme.colors.border,
    paddingTop: Theme.spacing.md,
    gap: Theme.spacing.sm,
  },
  messageHeading: { fontSize: 14, fontWeight: '700', color: Theme.colors.text },
  messageBody: {
    fontFamily: 'monospace',
    fontSize: 12,
    lineHeight: 18,
    color: Theme.colors.text,
    backgroundColor: Theme.colors.surfaceElevated,
    borderRadius: Theme.radius.sm,
    padding: Theme.spacing.md,
  },
  caveat: { fontSize: 12, lineHeight: 17, color: Theme.colors.textMuted },
  messageActions: { flexDirection: 'row', gap: Theme.spacing.sm },
  secondaryButton: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    paddingVertical: 10,
  },
  secondaryText: { fontSize: 13, fontWeight: '600', color: Theme.colors.brand },
  sectionTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    marginTop: Theme.spacing.lg,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Theme.spacing.md,
  },
  rowName: {
    flexShrink: 1,
    fontSize: 14,
    fontWeight: '600',
    color: Theme.colors.text,
  },
  rowDetail: {
    fontSize: 12,
    lineHeight: 17,
    color: Theme.colors.textMuted,
    marginTop: 2,
  },
  callButton: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: Theme.colors.sosRed,
    borderRadius: Theme.radius.md,
    paddingVertical: 8,
    paddingHorizontal: 14,
  },
  callText: { fontSize: 14, fontWeight: '700', color: '#FFFFFF' },
  facility: { gap: 2 },
  facilityTop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: Theme.spacing.sm,
  },
  facilityName: { flex: 1 },
  status: { fontSize: 12, fontWeight: '600', color: Theme.colors.passable },
  statusBlocked: { color: Theme.colors.blocked },
});
