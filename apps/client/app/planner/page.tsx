import { Suspense } from 'react';

import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { RoutePlannerScreen } from '@/components/planner/route-planner-screen';

export default function PlannerPage() {
  return (
    <RouteAccessGate path="/planner">
      <Suspense fallback={null}>
        <RoutePlannerScreen />
      </Suspense>
    </RouteAccessGate>
  );
}
