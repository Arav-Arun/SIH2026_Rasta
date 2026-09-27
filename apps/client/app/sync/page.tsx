import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { SyncQueueScreen } from '@/components/offline/sync-queue-screen';

export default function SyncPage() {
  return (
    <RouteAccessGate path="/sync">
      <SyncQueueScreen />
    </RouteAccessGate>
  );
}
