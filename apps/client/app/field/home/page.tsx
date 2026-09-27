import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { FieldHomeScreen } from '@/components/field/field-home-screen';

export default function FieldHomePage() {
  return (
    <RouteAccessGate path="/field/home">
      <FieldHomeScreen />
    </RouteAccessGate>
  );
}
