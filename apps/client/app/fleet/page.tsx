import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { FleetScreen } from '@/components/fleet/fleet-screen';

export default function FleetPage() {
  return (
    <RouteAccessGate path="/fleet">
      <FleetScreen />
    </RouteAccessGate>
  );
}
