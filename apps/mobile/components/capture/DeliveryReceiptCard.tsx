import { StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { Check, Clock, Info, X } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { CaptureDeliveryReceipt } from '../../types';

interface Props {
  receipt: CaptureDeliveryReceipt;
  onDismiss: () => void;
}

interface Leg {
  key: string;
  label: string;
  done: boolean;
  detail: string;
}

const EVIDENCE_DETAIL: Record<string, string> = {
  verified: 'Checked by the control room and accepted',
  uploaded: 'Uploaded, but the control room has not checked it yet',
  rejected: 'The control room rejected it. Retake the photo',
  failed: 'Upload did not complete',
  not_sent: 'Not sent',
  none: 'No photo was taken',
};

/** States, per destination, whether the capture actually arrived. */
export function DeliveryReceiptCard({ receipt, onDismiss }: Props) {
  const legs: Leg[] = [
    {
      key: 'localOutbox',
      label: 'Saved on this device',
      done: receipt.localOutbox,
      detail: 'Held in the outbox so it survives a dead zone',
    },
    {
      key: 'controlRoomDb',
      label: 'Filed with the control room',
      done: receipt.controlRoomDb,
      detail: receipt.controlRoomDb
        ? 'Waiting in the dispatcher verification queue'
        : 'Not yet. It stays in the outbox until it is accepted',
    },
    ...(receipt.evidence !== null
      ? [
          {
            key: 'evidence',
            label: 'Photo accepted as evidence',
            // Only a verified photo is evidence. Bytes sitting in the bucket
            // that nobody has checked must not read as a completed step.
            done: receipt.evidence === 'verified',
            detail: EVIDENCE_DETAIL[receipt.evidence] ?? receipt.evidence,
          },
        ]
      : []),
    // Risk scoring is not a leg of a capture: the service scores roads against
    // a recorded model version once a reviewer has acted on a report.
  ];

  const allDone = legs.every((leg) => leg.done);

  return (
    <View
      style={[styles.card, allDone ? styles.cardComplete : styles.cardPartial]}
    >
      <View style={styles.head}>
        <View style={styles.headText}>
          <Text style={styles.title}>
            {allDone ? 'Report received' : 'Report saved on this phone'}
          </Text>
          <Text style={styles.subtitle}>
            {allDone
              ? 'The control room has it. Its review status is below.'
              : 'It is sent when there is a connection.'}
          </Text>
        </View>
        <TouchableOpacity
          onPress={onDismiss}
          hitSlop={12}
          accessibilityRole="button"
          accessibilityLabel="Dismiss the delivery receipt"
        >
          <X size={18} color={Theme.colors.textMuted} />
        </TouchableOpacity>
      </View>

      <View style={styles.legs}>
        {legs.map((leg) => (
          <View key={leg.key} style={styles.legRow}>
            <View
              style={[
                styles.legIcon,
                leg.done ? styles.legIconDone : styles.legIconPending,
              ]}
            >
              {leg.done ? (
                <Check size={12} color="#FFFFFF" />
              ) : (
                <Clock size={12} color={Theme.colors.caution} />
              )}
            </View>
            <View style={styles.legText}>
              <Text
                style={[styles.legLabel, !leg.done && styles.legLabelPending]}
              >
                {leg.label}
              </Text>
              <Text style={styles.legDetail}>{leg.detail}</Text>
            </View>
          </View>
        ))}
      </View>

      {receipt.notes.length > 0 && (
        <View style={styles.notes}>
          {receipt.notes.map((note) => (
            <View key={note} style={styles.noteRow}>
              <Info size={12} color={Theme.colors.textMuted} />
              <Text style={styles.noteText}>{note}</Text>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: 1,
    borderRadius: Theme.radius.lg,
    padding: Theme.spacing.lg,
    marginBottom: Theme.spacing.lg,
  },
  cardComplete: {
    backgroundColor: Theme.colors.passableBg,
    borderColor: Theme.colors.passableBorder,
  },
  cardPartial: {
    backgroundColor: Theme.colors.cautionBg,
    borderColor: Theme.colors.cautionBorder,
  },
  head: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: Theme.spacing.md,
    marginBottom: Theme.spacing.lg,
  },
  headText: { flex: 1 },
  title: { fontSize: 15, fontWeight: '800', color: Theme.colors.text },
  subtitle: {
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.textMuted,
    marginTop: 3,
  },
  legs: { gap: Theme.spacing.md },
  legRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  legIcon: {
    width: 20,
    height: 20,
    borderRadius: Theme.radius.full,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: 1,
  },
  legIconDone: { backgroundColor: Theme.colors.passable },
  legIconPending: {
    backgroundColor: 'transparent',
    borderWidth: 1.5,
    borderColor: Theme.colors.cautionBorder,
  },
  legText: { flex: 1 },
  legLabel: { fontSize: 13, fontWeight: '700', color: Theme.colors.text },
  legLabelPending: { color: Theme.colors.textMuted },
  legDetail: { fontSize: 11.5, color: Theme.colors.textMuted, marginTop: 2 },
  notes: {
    marginTop: Theme.spacing.lg,
    paddingTop: Theme.spacing.md,
    borderTopWidth: 1,
    borderTopColor: 'rgba(0,0,0,0.07)',
    gap: 7,
  },
  noteRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 7 },
  noteText: {
    flex: 1,
    fontSize: 11.5,
    lineHeight: 17,
    color: Theme.colors.textMuted,
  },
});
