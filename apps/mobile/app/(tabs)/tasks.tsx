import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { useRouter } from 'expo-router';
import {
  CheckCircle2,
  ClipboardList,
  Clock,
  MapPin,
} from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { useCrew } from '../../contexts/SessionContext';
import { MinimalCard } from '../../components/ui/MinimalCard';
import {
  type InspectionSummary,
  advanceInspection,
  listInspections,
} from '../../services/rastaApi';
import { newUuid } from '../../services/ids';

/** The inspections a dispatcher has assigned to this officer. */
export default function TasksScreen() {
  const crew = useCrew();
  const router = useRouter();

  const [tasks, setTasks] = useState<InspectionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const result = await listInspections();
    if (result.ok) {
      setTasks(result.data.inspections);
      setError(null);
    } else {
      setTasks(null);
      setError(result.reason);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function advance(
    task: InspectionSummary,
    action: 'accept' | 'start' | 'complete',
  ) {
    setBusyId(task.id);
    try {
      const key = newUuid();
      const result = await advanceInspection(task.id, action, key);
      if (!result.ok) {
        setError(result.reason);
        return;
      }
      setError(null);
      await load();
    } finally {
      setBusyId(null);
    }
  }

  if (crew.mode === 'local_only') {
    return (
      <View style={styles.centered}>
        <ClipboardList size={26} color={Theme.colors.textDim} />
        <Text style={styles.emptyTitle}>No assigned tasks</Text>
        <Text style={styles.emptyBody}>
          Local-only session. Sign in with your district account to receive
          inspections from a dispatcher.
        </Text>
      </View>
    );
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
            await load();
            setRefreshing(false);
          }}
          tintColor={Theme.colors.brand}
        />
      }
    >
      {error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
          <TouchableOpacity onPress={load} accessibilityRole="button">
            <Text style={styles.errorAction}>Try again</Text>
          </TouchableOpacity>
        </View>
      )}

      {tasks === null && !error && (
        <View style={styles.centered}>
          <ActivityIndicator color={Theme.colors.brand} />
        </View>
      )}

      {tasks?.length === 0 && (
        <MinimalCard style={styles.empty}>
          <CheckCircle2 size={22} color={Theme.colors.passable} />
          <Text style={styles.emptyTitle}>Nothing assigned</Text>
          <Text style={styles.emptyBody}>
            When a dispatcher assigns you an inspection it appears here.
          </Text>
        </MinimalCard>
      )}

      {tasks?.map((task) => {
        const busy = busyId === task.id;
        const next = nextAction(task.status);

        return (
          <MinimalCard key={task.id} style={styles.card}>
            <View style={styles.cardHead}>
              <Text style={styles.status}>{STATUS_LABEL[task.status]}</Text>
              {task.due_at && (
                <View style={styles.dueRow}>
                  <Clock size={12} color={Theme.colors.textMuted} />
                  <Text style={styles.due}>
                    Due {new Date(task.due_at).toLocaleString()}
                  </Text>
                </View>
              )}
            </View>

            <Text style={styles.target}>
              {task.target_label ??
                `${task.target_type} ${task.target_id.slice(0, 8)}`}
            </Text>
            <View style={styles.targetTypeRow}>
              <MapPin size={12} color={Theme.colors.textMuted} />
              <Text style={styles.targetType}>
                Inspect this {task.target_type}
              </Text>
            </View>

            {task.instructions && (
              <Text style={styles.instructions}>{task.instructions}</Text>
            )}

            {next && (
              <TouchableOpacity
                style={styles.action}
                onPress={() => advance(task, next.action)}
                disabled={busy}
                accessibilityRole="button"
              >
                {busy ? (
                  <ActivityIndicator size="small" color="#FFFFFF" />
                ) : (
                  <Text style={styles.actionText}>{next.label}</Text>
                )}
              </TouchableOpacity>
            )}

            {task.status === 'in_progress' && (
              <TouchableOpacity
                style={styles.secondaryAction}
                onPress={() => router.push('/capture')}
                accessibilityRole="button"
              >
                <Text style={styles.secondaryActionText}>
                  Photograph what you found
                </Text>
              </TouchableOpacity>
            )}
          </MinimalCard>
        );
      })}
    </ScrollView>
  );
}

const STATUS_LABEL: Record<InspectionSummary['status'], string> = {
  assigned: 'Assigned to you',
  accepted: 'Accepted',
  in_progress: 'In progress',
  submitted: 'Submitted for review',
  reviewed: 'Reviewed',
  cancelled: 'Cancelled',
  overdue: 'Overdue',
};

/** The one transition this officer may make next, or none. */
function nextAction(
  status: InspectionSummary['status'],
): { action: 'accept' | 'start' | 'complete'; label: string } | null {
  switch (status) {
    case 'assigned':
    case 'overdue':
      return { action: 'accept', label: 'Accept this task' };
    case 'accepted':
      return { action: 'start', label: 'Start' };
    case 'in_progress':
      return { action: 'complete', label: 'Submit for review' };
    default:
      return null;
  }
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: Theme.colors.bg },
  content: { padding: Theme.spacing.lg, paddingBottom: Theme.spacing.xxl },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: Theme.spacing.xxl,
    backgroundColor: Theme.colors.bg,
    gap: Theme.spacing.sm,
  },
  errorBox: {
    backgroundColor: Theme.colors.blockedBg,
    borderWidth: 1,
    borderColor: Theme.colors.blockedBorder,
    borderRadius: Theme.radius.md,
    padding: Theme.spacing.md,
    marginBottom: Theme.spacing.lg,
    gap: 6,
  },
  errorText: { fontSize: 12.5, lineHeight: 18, color: Theme.colors.blocked },
  errorAction: {
    fontSize: 12.5,
    fontWeight: '800',
    color: Theme.colors.blocked,
    textDecorationLine: 'underline',
  },
  empty: { alignItems: 'center', paddingVertical: Theme.spacing.xxl },
  emptyTitle: {
    fontSize: 15,
    fontWeight: '800',
    color: Theme.colors.text,
    marginTop: Theme.spacing.sm,
  },
  emptyBody: {
    fontSize: 12.5,
    lineHeight: 18,
    color: Theme.colors.textMuted,
    textAlign: 'center',
    marginTop: 4,
  },
  card: { marginBottom: Theme.spacing.md, padding: Theme.spacing.lg },
  cardHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: Theme.spacing.sm,
  },
  status: {
    fontSize: 10.5,
    fontWeight: '800',
    color: Theme.colors.telemetry,
    textTransform: 'uppercase',
    letterSpacing: 0.7,
  },
  dueRow: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  due: { fontSize: 11.5, color: Theme.colors.textMuted },
  target: { fontSize: 15, fontWeight: '800', color: Theme.colors.text },
  targetTypeRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    marginTop: 3,
  },
  targetType: { fontSize: 12, color: Theme.colors.textMuted },
  instructions: {
    fontSize: 13,
    lineHeight: 19,
    color: Theme.colors.text,
    marginTop: Theme.spacing.md,
  },
  action: {
    alignItems: 'center',
    backgroundColor: Theme.colors.brand,
    paddingVertical: 13,
    borderRadius: Theme.radius.sm,
    marginTop: Theme.spacing.lg,
    minHeight: 44,
    justifyContent: 'center',
  },
  actionText: { fontSize: 13.5, fontWeight: '800', color: '#FFFFFF' },
  secondaryAction: {
    alignItems: 'center',
    paddingVertical: 12,
    borderRadius: Theme.radius.sm,
    borderWidth: 1,
    borderColor: Theme.colors.brand,
    marginTop: Theme.spacing.sm,
  },
  secondaryActionText: {
    fontSize: 13,
    fontWeight: '700',
    color: Theme.colors.brand,
  },
});
