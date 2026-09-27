import { Modal, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { CheckCircle2, Clock3 } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { CaptureDeliveryReceipt } from '../../types';

interface Props {
  visible: boolean;
  receipt: CaptureDeliveryReceipt | null;
  categoryLabel: string;
  corridorCode: string;
  onDismiss: () => void;
  onGoHome?: () => void;
}

/** What happened to the report just filed: sent, or held on the phone. */
export function ReportSuccessModal({
  visible,
  receipt,
  categoryLabel,
  corridorCode,
  onDismiss,
  onGoHome,
}: Props) {
  if (!visible) return null;

  const sent = Boolean(receipt?.controlRoomDb);
  const Icon = sent ? CheckCircle2 : Clock3;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onDismiss}
    >
      <View style={styles.backdrop}>
        <View style={styles.card}>
          <Icon
            size={28}
            color={sent ? Theme.colors.passable : Theme.colors.caution}
          />
          <Text style={styles.title}>
            {sent ? 'Report sent' : 'Report saved on this phone'}
          </Text>
          <Text style={styles.body}>
            {sent
              ? 'The control room has it. A dispatcher will review it.'
              : 'It is sent when there is a connection.'}
          </Text>

          <View style={styles.summary}>
            <View style={styles.row}>
              <Text style={styles.label}>Hazard</Text>
              <Text style={styles.value}>{categoryLabel}</Text>
            </View>
            <View style={[styles.row, styles.lastRow]}>
              <Text style={styles.label}>Corridor</Text>
              <Text style={styles.value}>{corridorCode}</Text>
            </View>
          </View>

          <View style={styles.actions}>
            {onGoHome && (
              <TouchableOpacity
                style={styles.secondary}
                onPress={() => {
                  onDismiss();
                  onGoHome();
                }}
                accessibilityRole="button"
              >
                <Text style={styles.secondaryText}>Home</Text>
              </TouchableOpacity>
            )}
            <TouchableOpacity
              style={styles.primary}
              onPress={onDismiss}
              accessibilityRole="button"
            >
              <Text style={styles.primaryText}>Done</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(23, 32, 51, 0.45)',
    justifyContent: 'center',
    padding: Theme.spacing.xl,
  },
  card: {
    width: '100%',
    maxWidth: 420,
    alignSelf: 'center',
    backgroundColor: Theme.colors.surface,
    borderRadius: Theme.radius.lg,
    padding: Theme.spacing.xl,
  },
  title: {
    fontSize: 18,
    fontWeight: '700',
    color: Theme.colors.text,
    marginTop: Theme.spacing.md,
  },
  body: {
    fontSize: 14,
    lineHeight: 20,
    color: Theme.colors.textMuted,
    marginTop: 4,
  },
  summary: {
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    marginTop: Theme.spacing.lg,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: Theme.spacing.md,
    paddingHorizontal: Theme.spacing.md,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: Theme.colors.border,
  },
  lastRow: { borderBottomWidth: 0 },
  label: { fontSize: 13, color: Theme.colors.textMuted },
  value: {
    flexShrink: 1,
    fontSize: 13,
    fontWeight: '600',
    color: Theme.colors.text,
    textAlign: 'right',
  },
  actions: {
    flexDirection: 'row',
    gap: Theme.spacing.sm,
    marginTop: Theme.spacing.lg,
  },
  secondary: {
    flex: 1,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
    paddingVertical: 12,
  },
  secondaryText: { fontSize: 14, fontWeight: '600', color: Theme.colors.text },
  primary: {
    flex: 1,
    alignItems: 'center',
    backgroundColor: Theme.colors.brand,
    borderRadius: Theme.radius.md,
    paddingVertical: 12,
  },
  primaryText: { fontSize: 14, fontWeight: '700', color: '#FFFFFF' },
});
