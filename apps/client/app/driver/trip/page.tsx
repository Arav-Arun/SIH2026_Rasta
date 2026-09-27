import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { DriverTripsScreen } from '@/components/driver/driver-trips-screen';

export default function DriverTripPage() {
  return (
    <RouteAccessGate path="/driver/trip">
      <DriverTripsScreen />
    </RouteAccessGate>
  );
}
