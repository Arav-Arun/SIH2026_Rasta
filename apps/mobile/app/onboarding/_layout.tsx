import { Stack } from 'expo-router';
import { Theme } from '../../constants/theme';

export default function OnboardingLayout() {
  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: Theme.colors.bg },
        animation: 'slide_from_right',
      }}
    />
  );
}
