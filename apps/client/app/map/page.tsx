import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { AccessibilityMapScreen } from '@/components/map/accessibility-map-screen';

export default function MapPage() {
  return (
    <RouteAccessGate path="/map">
      <AccessibilityMapScreen />
    </RouteAccessGate>
  );
}
