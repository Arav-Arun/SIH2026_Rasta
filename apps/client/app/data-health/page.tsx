import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { DataHealthScreen } from '@/components/operations/data-health-screen';

export default function DataHealthPage() {
  return (
    <RouteAccessGate path="/data-health">
      <DataHealthScreen />
    </RouteAccessGate>
  );
}
