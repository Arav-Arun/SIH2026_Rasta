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
import { useT } from '../../contexts/LocaleContext';
import { useSession } from '../../contexts/SessionContext';
import { LanguagePicker } from '../ui/LanguagePicker';
import { unsentOnThisPhone } from '../../services/session';

/**
 * Always-visible statement of who is signed in, in which seat, and whether the
 * session is live or local-only. Tapping it opens the seat switch and sign-out.
 */
export function CrewHeaderBadge() {
  const { session, signOut } = useSession();
  const t = useT();
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
        accessibilityLabel={t('mobile.crew.badgeLabel', {
          name: session.displayName,
          seat: isObserver
            ? t('mobile.seat.observer')
            : t('mobile.seat.driver'),
        })}
      >
        <RoleIcon
          size={13}
          color={isLocal ? Theme.colors.caution : Theme.colors.brand}
        />
        <Text style={[styles.badgeText, isLocal && styles.badgeTextLocal]}>
          {isObserver
            ? t('mobile.crew.observerShort')
            : t('mobile.seat.driver')}
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
              <Text style={styles.sheetTitle}>
                {t('mobile.crew.sessionTitle')}
              </Text>
              <TouchableOpacity
                onPress={() => setOpen(false)}
                hitSlop={12}
                accessibilityRole="button"
                accessibilityLabel={t('mobile.common.close')}
              >
                <X size={19} color={Theme.colors.textMuted} />
              </TouchableOpacity>
            </View>

            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>{t('mobile.crew.name')}</Text>
              <Text style={styles.detailValue}>{session.displayName}</Text>
            </View>
            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>{t('mobile.crew.seat')}</Text>
              <Text style={styles.detailValue}>
                {isObserver
                  ? t('mobile.seat.observer')
                  : t('mobile.seat.driver')}
              </Text>
            </View>
            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>{t('mobile.crew.crewCode')}</Text>
              <Text style={[styles.detailValue, styles.mono]}>
                {session.crewCode}
              </Text>
            </View>
            <View style={styles.detailRow}>
              <Text style={styles.detailKey}>{t('mobile.crew.session')}</Text>
              <Text
                style={[
                  styles.detailValue,
                  isLocal ? styles.valueCaution : styles.valueOk,
                ]}
              >
                {isLocal
                  ? t('mobile.crew.localOnly')
                  : t('mobile.crew.signedIn')}
              </Text>
            </View>

            {isLocal && (
              <Text style={styles.localNote}>{t('mobile.crew.localNote')}</Text>
            )}

            <View style={styles.language}>
              <LanguagePicker />
            </View>

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
                const parts = [
                  unsent.reports
                    ? t('mobile.crew.reports', { count: unsent.reports })
                    : null,
                  unsent.positions
                    ? t('mobile.crew.positions', { count: unsent.positions })
                    : null,
                ].filter((part): part is string => part !== null);
                const items =
                  parts.length === 2
                    ? t('mobile.crew.listAnd', {
                        first: parts[0],
                        second: parts[1],
                      })
                    : parts[0];
                Alert.alert(
                  t('mobile.crew.unsentTitle'),
                  t('mobile.crew.unsentBody', {
                    items,
                    count: unsent.reports + unsent.positions,
                  }),
                  [
                    { text: t('mobile.crew.staySignedIn'), style: 'cancel' },
                    {
                      text: t('mobile.crew.deleteAndSignOut'),
                      style: 'destructive',
                      onPress: () => void signOut(),
                    },
                  ],
                );
              }}
              accessibilityRole="button"
            >
              <LogOut size={15} color={Theme.colors.blocked} />
              <Text style={styles.signOutText}>{t('mobile.crew.signOut')}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  language: { marginTop: Theme.spacing.lg },
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
