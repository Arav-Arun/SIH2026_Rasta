import { RouteAccessGate } from '@/components/auth/route-access-gate';
import { DeliveryQueueScreen } from '@/components/deliveries/delivery-queue-screen';

export default function DeliveriesPage() {
  return (
    <RouteAccessGate path="/deliveries">
      <DeliveryQueueScreen />
    </RouteAccessGate>
  );
}
