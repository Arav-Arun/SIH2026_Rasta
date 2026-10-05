import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';
import { useRouter } from 'expo-router';
import { FileText, Route, Shield, ChevronRight } from 'lucide-react-native';
import { Theme } from '../../constants/theme';
import { useCrew } from '../../contexts/SessionContext';
import { MinimalCard } from './MinimalCard';

export function QuickActionGrid() {
  const router = useRouter();
  const crew = useCrew();

  const actions = [
    {
      id: 'action-report',
      title: 'Report a hazard',
      subtitle: 'Add a photo and location, even offline',
      icon: FileText,
      route: '/(tabs)/report',
    },
    {
      id: 'action-routes',
      title: 'Your route',
      subtitle: 'The route the control room approved',
      icon: Route,
      route: '/(tabs)/routes',
    },
    {
      id: 'action-safehavens',
      title: 'Get emergency help',
      subtitle: 'Find support or send an SOS',
      icon: Shield,
      route: '/(tabs)/sos',
    },
  ] as const;

  return (
    <View style={styles.container}>
      <Text style={styles.sectionHeader}>For the road</Text>
      <View style={styles.grid}>
        {/* Only a driver follows a route; an observer's phone has none. */}
        {actions
          .filter((act) => act.id !== 'action-routes' || crew.role === 'driver')
          .map((act) => {
            const Icon = act.icon;

            return (
              <TouchableOpacity
                key={act.id}
                activeOpacity={0.7}
                onPress={() => router.push(act.route)}
              >
                <MinimalCard style={styles.card}>
                  <Icon size={20} color={Theme.colors.brand} />

                  <View style={styles.content}>
                    <Text style={styles.title}>{act.title}</Text>
                    <Text style={styles.subtitle}>{act.subtitle}</Text>
                  </View>

                  <ChevronRight size={16} color={Theme.colors.textDim} />
                </MinimalCard>
              </TouchableOpacity>
            );
          })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    marginBottom: Theme.spacing.md,
  },
  sectionHeader: {
    fontSize: 11,
    fontWeight: '700',
    color: Theme.colors.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
    marginBottom: 8,
  },
  grid: {
    gap: 10,
  },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 14,
    paddingHorizontal: Theme.spacing.md,
    backgroundColor: Theme.colors.surface,
  },
  content: {
    flex: 1,
    marginLeft: 12,
  },
  title: {
    fontSize: 14,
    fontWeight: '700',
    color: Theme.colors.text,
  },
  subtitle: {
    fontSize: 12,
    color: Theme.colors.textMuted,
    marginTop: 2,
    lineHeight: 17,
  },
});
