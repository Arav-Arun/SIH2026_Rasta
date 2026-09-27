import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { PermissionsScreen } from '@/components/permissions/permissions-screen';

export default function PermissionsPage() {
  return (
    <RouteAccessGate path="/permissions">
      <PermissionsScreen />
    </RouteAccessGate>
  );
}
