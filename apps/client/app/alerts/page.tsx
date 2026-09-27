import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { AlertInboxScreen } from '@/components/alerts/alert-inbox-screen';

export default function AlertsPage() {
  return (
    <RouteAccessGate path="/alerts">
      <AlertInboxScreen />
    </RouteAccessGate>
  );
}
