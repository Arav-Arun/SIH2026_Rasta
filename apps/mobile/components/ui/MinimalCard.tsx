import React from 'react';
import {
  View,
  StyleSheet,
  ViewProps,
  StyleProp,
  ViewStyle,
} from 'react-native';
import { Theme } from '../../constants/theme';

interface MinimalCardProps extends ViewProps {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  elevated?: boolean;
}

export function MinimalCard({
  children,
  style,
  elevated = false,
  ...rest
}: MinimalCardProps) {
  return (
    <View style={[styles.card, elevated && styles.elevated, style]} {...rest}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Theme.colors.surface,
    borderColor: Theme.colors.border,
    borderWidth: 1,
    borderRadius: Theme.radius.lg,
    padding: Theme.spacing.lg,
    // Borders establish hierarchy more reliably than shadows on low-end Android devices.
  },
  elevated: {
    backgroundColor: Theme.colors.surfaceElevated,
    borderColor: Theme.colors.telemetryBorder,
  },
});
