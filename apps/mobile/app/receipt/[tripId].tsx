import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { PackageCheck } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { MinimalCard } from '../../components/ui/MinimalCard';
import {
  type Consignment,
  type Trip,
  listConsignments,
  listTrips,
  recordReceipt,
} from '../../services/rastaApi';
import {
  deriveStatus,
  firstProblem,
  formatQuantity,
  parseQuantity,
  receiptLines,
} from '../../services/logistics';
import { newUuid } from '../../services/ids';
import { stopTracking } from '../../services/tracker';

const STATUS_SENTENCE: Record<string, string> = {
  delivered: 'Everything loaded arrived.',
  partially_delivered: 'Some of the load is short.',
  failed: 'Nothing arrived.',
};

/** Records what actually changed hands at the facility. */
export default function ReceiptScreen() {
  const { tripId } = useLocalSearchParams<{ tripId: string }>();
  const router = useRouter();

  const [trip, setTrip] = useState<Trip | null>(null);
  const [consignment, setConsignment] = useState<Consignment | null>(null);
  const [entered, setEntered] = useState<Record<string, string>>({});
  const [receivedBy, setReceivedBy] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [submitKey] = useState(newUuid);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [tripResult, consignmentResult] = await Promise.all([
        listTrips(),
        listConsignments(),
      ]);
      if (!tripResult.ok) {
        setError(tripResult.reason);
        return;
      }
      const found =
        tripResult.data.trips.find((row) => row.id === tripId) ?? null;
      setTrip(found);
      if (!found) {
        setError('That trip is not assigned to you.');
        return;
      }
      if (consignmentResult.ok) {
        setConsignment(
          consignmentResult.data.consignments.find(
            (row) => row.id === found.consignment_id,
          ) ?? null,
        );
      }
      setError(null);
    } finally {
      setLoading(false);
    }
  }, [tripId]);

  useEffect(() => {
    void load();
  }, [load]);

  const items = consignment?.items ?? [];
  const problem = firstProblem(items, entered);
  const status = deriveStatus(items, entered);
  const everyLineAnswered = items.every(
    (item) => parseQuantity(entered[item.id] ?? '', item).kind === 'ok',
  );
  const canSubmit =
    !saving && items.length > 0 && everyLineAnswered && problem === null;

  async function submit() {
    if (!canSubmit || !trip) return;
    setSaving(true);
    try {
      const result = await recordReceipt(
        trip.id,
        {
          status,
          received_by_ref: receivedBy.trim() || null,
          notes: notes.trim() || null,
          items: receiptLines(items, entered),
        },
        submitKey,
      );
      if (!result.ok) {
        setError(result.reason);
        return;
      }
      // Recording the receipt ends the trip, so position reporting ends too.
      await stopTracking();
      router.back();
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Stack.Screen options={{ title: 'Record what arrived' }} />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          style={styles.container}
          contentContainerStyle={styles.content}
        >
          {loading && (
            <View style={styles.centered}>
              <ActivityIndicator color={Theme.colors.brand} />
            </View>
          )}

          {error && (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          )}

          {!loading && consignment && (
            <>
              <MinimalCard style={styles.card}>
                <Text style={styles.reference}>{consignment.reference}</Text>
                <Text style={styles.destination}>
                  {consignment.destination_facility_name ?? 'Destination'}
                </Text>
                <Text style={styles.hint}>
                  Enter what the storekeeper counted. A line left at zero says
                  nothing arrived for that item.
                </Text>
              </MinimalCard>

              {items.map((item) => {
                const raw = entered[item.id] ?? '';
                const parsed = parseQuantity(raw, item);
                const short =
                  parsed.kind === 'ok' &&
                  formatQuantity(parsed.value) !==
                    formatQuantity(item.quantity);

                return (
                  <MinimalCard key={item.id} style={styles.card}>
                    <View style={styles.lineHead}>
                      <Text style={styles.commodity} numberOfLines={1}>
                        {item.commodity}
                      </Text>
                      <Text style={styles.ordered}>
                        Loaded {formatQuantity(item.quantity)} {item.unit}
                      </Text>
                    </View>

                    <View style={styles.inputRow}>
                      <TextInput
                        value={raw}
                        onChangeText={(text) =>
                          setEntered((current) => ({
                            ...current,
                            [item.id]: text,
                          }))
                        }
                        keyboardType="decimal-pad"
                        placeholder="0"
                        placeholderTextColor={Theme.colors.textDim}
                        accessibilityLabel={`Quantity of ${item.commodity} received`}
                        style={[
                          styles.input,
                          parsed.kind === 'invalid' && styles.inputInvalid,
                        ]}
                      />
                      <Text style={styles.unit}>{item.unit}</Text>
                      <TouchableOpacity
                        onPress={() =>
                          setEntered((current) => ({
                            ...current,
                            [item.id]: formatQuantity(item.quantity),
                          }))
                        }
                        accessibilityRole="button"
                        style={styles.allButton}
                      >
                        <Text style={styles.allButtonText}>All</Text>
                      </TouchableOpacity>
                    </View>

                    {parsed.kind === 'invalid' && (
                      <Text style={styles.lineError}>{parsed.reason}</Text>
                    )}
                    {short && (
                      <Text style={styles.lineShort}>
                        Short of what was loaded
                      </Text>
                    )}
                  </MinimalCard>
                );
              })}

              <MinimalCard style={styles.card}>
                <Text style={styles.label}>Who received it</Text>
                <TextInput
                  value={receivedBy}
                  onChangeText={setReceivedBy}
                  placeholder="Name or post, e.g. PHC storekeeper"
                  placeholderTextColor={Theme.colors.textDim}
                  style={styles.textField}
                />
                <Text style={styles.label}>Notes</Text>
                <TextInput
                  value={notes}
                  onChangeText={setNotes}
                  placeholder="Optional"
                  placeholderTextColor={Theme.colors.textDim}
                  multiline
                  style={[styles.textField, styles.notesField]}
                />
              </MinimalCard>

              <MinimalCard style={styles.card}>
                <View style={styles.summaryRow}>
                  <PackageCheck size={16} color={Theme.colors.telemetry} />
                  <Text style={styles.summaryText}>
                    {everyLineAnswered
                      ? STATUS_SENTENCE[status]
                      : 'Enter a quantity for every line.'}
                  </Text>
                </View>
              </MinimalCard>

              <TouchableOpacity
                style={[styles.submit, !canSubmit && styles.submitDisabled]}
                onPress={submit}
                disabled={!canSubmit}
                accessibilityRole="button"
              >
                {saving ? (
                  <ActivityIndicator size="small" color="#FFFFFF" />
                ) : (
                  <Text style={styles.submitText}>Record receipt</Text>
                )}
              </TouchableOpacity>
            </>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  container: { flex: 1, backgroundColor: Theme.colors.bg },
  content: {
    padding: Theme.spacing.lg,
    paddingBottom: 48,
    gap: Theme.spacing.md,
  },
  centered: { paddingVertical: Theme.spacing.xxl, alignItems: 'center' },
  card: { gap: Theme.spacing.sm },
  reference: {
    fontSize: Theme.typography.subtitle,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  destination: {
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
  },
  hint: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
    lineHeight: 17,
  },
  lineHead: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: Theme.spacing.sm,
  },
  commodity: {
    flexShrink: 1,
    fontSize: Theme.typography.body,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  ordered: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.textMuted,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Theme.spacing.sm,
  },
  input: {
    flex: 1,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.sm,
    paddingHorizontal: Theme.spacing.md,
    paddingVertical: 10,
    fontSize: Theme.typography.subtitle,
    color: Theme.colors.text,
    backgroundColor: Theme.colors.bg,
  },
  inputInvalid: { borderColor: Theme.colors.blocked },
  unit: {
    fontSize: Theme.typography.body,
    color: Theme.colors.textMuted,
    minWidth: 44,
  },
  allButton: {
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.sm,
    paddingHorizontal: Theme.spacing.md,
    paddingVertical: 10,
  },
  allButtonText: {
    fontSize: Theme.typography.body,
    fontWeight: '600',
    color: Theme.colors.text,
  },
  lineError: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.blocked,
  },
  lineShort: {
    fontSize: Theme.typography.caption,
    color: Theme.colors.caution,
  },
  label: {
    fontSize: Theme.typography.caption,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
  },
  textField: {
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.sm,
    paddingHorizontal: Theme.spacing.md,
    paddingVertical: 10,
    fontSize: Theme.typography.body,
    color: Theme.colors.text,
    backgroundColor: Theme.colors.bg,
  },
  notesField: { minHeight: 68, textAlignVertical: 'top' },
  summaryRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Theme.spacing.sm,
  },
  summaryText: {
    flexShrink: 1,
    fontSize: Theme.typography.body,
    color: Theme.colors.text,
  },
  submit: {
    backgroundColor: Theme.colors.brand,
    borderRadius: Theme.radius.md,
    paddingVertical: 14,
    alignItems: 'center',
  },
  submitDisabled: { opacity: 0.45 },
  submitText: {
    color: '#FFFFFF',
    fontSize: Theme.typography.body,
    fontWeight: '700',
  },
  errorBox: {
    borderWidth: 1,
    borderColor: Theme.colors.blockedBorder,
    backgroundColor: Theme.colors.blockedBg,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
  },
  errorText: { fontSize: Theme.typography.body, color: Theme.colors.blocked },
});
