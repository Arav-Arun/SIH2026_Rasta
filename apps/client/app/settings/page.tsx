import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { SettingsScreen } from '@/components/settings/settings-screen';

export default function SettingsPage() {
  return (
    <RouteAccessGate path="/settings">
      <SettingsScreen />
    </RouteAccessGate>
  );
}
