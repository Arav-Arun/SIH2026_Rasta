import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { FieldReportScreen } from '@/components/field/field-report-screen';

export default function FieldReportPage() {
  return (
    <RouteAccessGate path="/field/report">
      <FieldReportScreen />
    </RouteAccessGate>
  );
}
