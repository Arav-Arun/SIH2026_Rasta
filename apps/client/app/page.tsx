import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { OverviewScreen } from '@/components/overview/overview-screen';

export default function Home() {
  return (
    <RouteAccessGate path="/overview">
      <OverviewScreen />
    </RouteAccessGate>
  );
}
