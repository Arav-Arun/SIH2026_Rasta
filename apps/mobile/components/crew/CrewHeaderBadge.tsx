import { useState } from 'react';
import {
  Alert,
  Modal,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { Camera, LogOut, Navigation, X } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { useSession } from '../../contexts/SessionContext';
import { unsentOnThisPhone } from '../../services/session';

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * Always-visible statement of who is signed in, in which seat, and whether the
 * session is live or local-only. Tapping it opens the seat switch and sign-out.
 */
export function CrewHeaderBadge() {
  const { session, signOut } = useSession();
  const [open, setOpen] = useState(false);

  if (!session) return null;

  const isObserver = session.role === 'observer';
  const RoleIcon = isObserver ? Camera : Navigation;
  const isLocal = session.mode === 'local_only';

  return (
    <>
      <TouchableOpacity
        style={[styles.badge, isLocal && styles.badgeLocal]}
        onPress={() => setOpen(true)}
        accessibilityRole="button"
        accessibilityLabel={`Signed in as ${session.displayName}, ${
          isObserver ? 'observer' : 'driver'
        }. Open crew options.`}
      >
        <RoleIcon
          size={13}
          color={isLocal ? Theme.colors.caution : Theme.colors.brand}
        />
        <Text style={[styles.badgeText, isLocal && styles.badgeTextLocal]}>
          {isObserver ? 'Observer' : 'Driver'}
        </Text>
      </TouchableOpacity>

      <Modal
        visible={open}
        transparent
        animationType="fade"
        onRequestClose={() => setOpen(false)}
      >
        <View style={styles.backdrop}>
          <View style={styles.sheet}>
            <View style={styles.sheetHead}>
              <Text style={styles.sheetTitle}>Crew session</Text>
              <TouchableOpacity
                onPress={() => setOpen(false)}
                hitSlop={12}
                accessibilityRole="button"
                accessibilityLabel="Close"
              >
                <X size={19} color={Theme.colors.textMuted} />
              </TouchableOpacity>
            </View>

            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>Name</Text>
              <Text style={styles.detailValue}>{session.displayName}</Text>
            </View>
            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>Seat</Text>
              <Text style={styles.detailValue}>
                {isObserver ? 'Onboard observer' : 'Driver'}
              </Text>
            </View>
            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>Crew code</Text>
              <Text style={[styles.detailValue, styles.mono]}>
                {session.crewCode}
              </Text>
            </View>
            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>Session</Text>
              <Text
                style={[
                  styles.detailValue,
                  isLocal ? styles.valueCaution : styles.valueOk,
                ]}
              >
                {isLocal ? 'Local-only' : 'Signed in'}
              </Text>
            </View>

            {isLocal && (
              <Text style={styles.localNote}>
                Captures stay on this phone. They do not reach the driver, the
                control room or the risk model.
              </Text>
            )}

            <TouchableOpacity
              style={styles.signOutButton}
              onPress={async () => {
                setOpen(false);
                // Signing out deletes this person's data from the phone, so
                // anything not yet sent needs an explicit choice first.
                const unsent = await unsentOnThisPhone();
                if (unsent.reports === 0 && unsent.positions === 0) {
                  await signOut();
                  return;
                }
                const lost = [
                  unsent.reports
                    ? plural(unsent.reports, 'report', 'reports')
                    : null,
                  unsent.positions
                    ? plural(
                        unsent.positions,
                        'trip position',
                        'trip positions',
                      )
                    : null,
                ]
                  .filter(Boolean)
                  .join(' and ');
                Alert.alert(
                  'Not everything has been sent',
                  `${lost} on this phone ${
                    unsent.reports + unsent.positions === 1 ? 'has' : 'have'
                  } not reached the control room. Signing out stops tracking and deletes ${
                    unsent.reports + unsent.positions === 1 ? 'it' : 'them'
                  } from this phone.`,
                  [
                    { text: 'Stay signed in', style: 'cancel' },
                    {
                      text: 'Delete and sign out',
                      style: 'destructive',
                      onPress: () => void signOut(),
                    },
                  ],
                );
              }}
              accessibilityRole="button"
            >
              <LogOut size={15} color={Theme.colors.blocked} />
              <Text style={styles.signOutText}>Sign out</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: Theme.radius.full,
    backgroundColor: Theme.colors.surfaceElevated,
    borderWidth: 1,
    borderColor: Theme.colors.border,
  },
  badgeLocal: {
    backgroundColor: Theme.colors.cautionBg,
    borderColor: Theme.colors.cautionBorder,
  },
  badgeText: { fontSize: 11, fontWeight: '800', color: Theme.colors.brand },
  badgeTextLocal: { color: Theme.colors.caution },

  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(25, 37, 34, 0.45)',
    justifyContent: 'flex-end',
  },
  sheet: {
    backgroundColor: Theme.colors.surface,
    borderTopLeftRadius: Theme.radius.xl,
    borderTopRightRadius: Theme.radius.xl,
    padding: Theme.spacing.xl,
    paddingBottom: Theme.spacing.xxl,
  },
  sheetHead: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: Theme.spacing.lg,
  },
  sheetTitle: { fontSize: 17, fontWeight: '800', color: Theme.colors.text },
  detailRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 11,
    borderBottomWidth: 1,
    borderBottomColor: Theme.colors.borderSubtle,
  },
  detailKey: { fontSize: 12.5, color: Theme.colors.textMuted },
  detailValue: { fontSize: 13, fontWeight: '700', color: Theme.colors.text },
  mono: { fontFamily: 'monospace' },
  valueOk: { color: Theme.colors.passable },
  valueCaution: { color: Theme.colors.caution },
  localNote: {
    fontSize: 12,
    lineHeight: 17,
    color: Theme.colors.caution,
    backgroundColor: Theme.colors.cautionBg,
    borderRadius: Theme.radius.sm,
    padding: Theme.spacing.md,
    marginTop: Theme.spacing.md,
  },
  signOutButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingVertical: 14,
    marginTop: Theme.spacing.xl,
    borderWidth: 1,
    borderColor: Theme.colors.border,
    borderRadius: Theme.radius.md,
  },
  signOutText: {
    fontSize: 13.5,
    fontWeight: '700',
    color: Theme.colors.blocked,
  },
});
