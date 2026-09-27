import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { InspectionQueueScreen } from '@/components/inspections/inspection-queue-screen';

export default function InspectionsPage() {
  return (
    <RouteAccessGate path="/inspections">
      <InspectionQueueScreen />
    </RouteAccessGate>
  );
}
