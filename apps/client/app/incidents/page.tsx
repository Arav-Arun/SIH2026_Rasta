import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { ReviewQueueScreen } from '@/components/incidents/review-queue-screen';

export default function IncidentsPage() {
  return (
    <RouteAccessGate path="/incidents">
      <ReviewQueueScreen />
    </RouteAccessGate>
  );
}
