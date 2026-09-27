'use client';

import { useEffect, useState } from 'react';
import {
  Camera,
  CheckCircle2,
  LocateFixed,
  LoaderCircle,
  ShieldAlert,
} from 'lucide-react';

import { useLocale } from '@/components/i18n/locale-provider';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { formatAbsoluteTime } from '@/lib/i18n/format';
import {
  captureDevicePhoto,
  DeviceCapabilityError,
  getCurrentDevicePosition,
  type DevicePhoto,
} from '@/lib/device';

type CheckState = 'idle' | 'running' | 'passed' | 'failed';

/**
 * A message kept as a key, not as text, so switching language mid-check
 * re-renders it in the new language instead of leaving English behind.
 */
type Message = { key: string; params?: Record<string, string | number> };

function failureMessage(error: unknown): Message {
  return error instanceof DeviceCapabilityError
    ? { key: `permissions.error.${error.code}` }
    : { key: 'permissions.error.unknown' };
}

export function DeviceCheck() {
  const { locale, t } = useLocale();
  const [photo, setPhoto] = useState<DevicePhoto>();
  const [cameraState, setCameraState] = useState<CheckState>('idle');
  const [cameraMessage, setCameraMessage] = useState<Message>({
    key: 'permissions.camera.idle',
  });
  const [locationState, setLocationState] = useState<CheckState>('idle');
  const [locationMessage, setLocationMessage] = useState<Message>({
    key: 'permissions.location.idle',
  });

  useEffect(
    () => () => {
      if (photo) URL.revokeObjectURL(photo.previewUrl);
    },
    [photo],
  );

  async function checkCamera() {
    setCameraState('running');
    setCameraMessage({ key: 'permissions.camera.running' });
    try {
      const result = await captureDevicePhoto();
      if (photo) URL.revokeObjectURL(photo.previewUrl);
      setPhoto(result);
      setCameraState('passed');
      setCameraMessage({ key: 'permissions.camera.passed' });
    } catch (error) {
      setCameraState('failed');
      setCameraMessage(failureMessage(error));
    }
  }

  async function checkLocation() {
    setLocationState('running');
    setLocationMessage({ key: 'permissions.location.running' });
    try {
      const fix = await getCurrentDevicePosition();
      setLocationState('passed');
      setLocationMessage({
        key: Number.isFinite(fix.accuracyMetres)
          ? 'permissions.location.passedWithAccuracy'
          : 'permissions.location.passed',
        params: {
          time: formatAbsoluteTime(locale, new Date(fix.capturedAt)),
          accuracy: Math.round(fix.accuracyMetres),
        },
      });
    } catch (error) {
      setLocationState('failed');
      setLocationMessage(failureMessage(error));
    }
  }

  return (
    <section className="space-y-4" aria-labelledby="device-check-heading">
      <div>
        <h2 id="device-check-heading" className="text-base font-semibold">
          {t('permissions.checksHeading')}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {t('permissions.checksBody')}
        </p>
      </div>

      <Alert>
        <ShieldAlert />
        <AlertTitle>{t('permissions.localOnlyTitle')}</AlertTitle>
        <AlertDescription>{t('permissions.localOnlyBody')}</AlertDescription>
      </Alert>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-3 rounded-lg border bg-white p-4">
          <div className="flex items-center gap-2">
            <Camera className="size-4 text-blue-700" aria-hidden />
            <h3 className="text-sm font-semibold">
              {t('permissions.camera.title')}
            </h3>
          </div>
          <p
            className="min-h-10 text-xs leading-relaxed text-muted-foreground"
            aria-live="polite"
            data-camera-state={cameraState}
          >
            {t(cameraMessage.key, cameraMessage.params)}
          </p>
          {photo ? (
            // The image is a user-initiated local capture, never a fixture presented as evidence.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={photo.previewUrl}
              alt={t('permissions.camera.previewAlt')}
              className="aspect-video w-full rounded-md border bg-slate-50 object-cover"
            />
          ) : (
            <div className="flex aspect-video items-center justify-center rounded-md border border-dashed bg-slate-50 px-4 text-center text-xs text-muted-foreground">
              {t('permissions.camera.none')}
            </div>
          )}
          <Button
            type="button"
            variant="outline"
            onClick={checkCamera}
            disabled={cameraState === 'running'}
          >
            {cameraState === 'running' ? (
              <LoaderCircle className="animate-spin" aria-hidden />
            ) : (
              <Camera aria-hidden />
            )}
            {t('permissions.camera.test')}
          </Button>
        </div>

        <div className="space-y-3 rounded-lg border bg-white p-4">
          <div className="flex items-center gap-2">
            <LocateFixed className="size-4 text-blue-700" aria-hidden />
            <h3 className="text-sm font-semibold">
              {t('permissions.location.title')}
            </h3>
          </div>
          <p
            className="min-h-10 text-xs leading-relaxed text-muted-foreground"
            aria-live="polite"
            data-location-state={locationState}
          >
            {t(locationMessage.key, locationMessage.params)}
          </p>
          <div className="flex aspect-video items-center justify-center rounded-md border border-dashed bg-slate-50 px-4 text-center text-xs text-muted-foreground">
            {locationState === 'passed' ? (
              <span className="inline-flex items-center gap-2 text-emerald-700">
                <CheckCircle2 className="size-4" aria-hidden />{' '}
                {t('permissions.location.received')}
              </span>
            ) : (
              t('permissions.location.noCoordinates')
            )}
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={checkLocation}
            disabled={locationState === 'running'}
          >
            {locationState === 'running' ? (
              <LoaderCircle className="animate-spin" aria-hidden />
            ) : (
              <LocateFixed aria-hidden />
            )}
            {t('permissions.location.test')}
          </Button>
        </div>
      </div>
    </section>
  );
}
