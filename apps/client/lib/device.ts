export type DevicePhoto = {
  blob: Blob;
  format: string;
  previewUrl: string;
  capturedAt: string;
};

type CurrentPositionFix = {
  latitude: number;
  longitude: number;
  accuracyMetres: number;
  capturedAt: string;
};

export class DeviceCapabilityError extends Error {
  readonly code:
    | 'camera_unavailable'
    | 'location_unavailable'
    | 'permission_denied'
    | 'request_cancelled'
    | 'location_failed';

  constructor(code: DeviceCapabilityError['code'], message: string) {
    super(message);
    this.name = 'DeviceCapabilityError';
    this.code = code;
  }
}

/** Opens the camera (or file picker) and resolves with the chosen photo. */
export function captureDevicePhoto(): Promise<DevicePhoto> {
  if (typeof document === 'undefined') {
    return Promise.reject(
      new DeviceCapabilityError(
        'camera_unavailable',
        'Camera access requires a browser window.',
      ),
    );
  }

  return new Promise((resolve, reject) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.capture = 'environment';
    input.hidden = true;

    const cancel = () => {
      input.remove();
      reject(
        new DeviceCapabilityError(
          'request_cancelled',
          'No photo was selected.',
        ),
      );
    };

    input.addEventListener('cancel', cancel, { once: true });
    input.addEventListener(
      'change',
      () => {
        const file = input.files?.[0];
        if (!file) {
          cancel();
          return;
        }
        input.remove();
        resolve({
          blob: file,
          format: file.type || 'image/*',
          previewUrl: URL.createObjectURL(file),
          capturedAt: new Date().toISOString(),
        });
      },
      { once: true },
    );

    document.body.appendChild(input);
    input.click();
  });
}

/** A single high-accuracy fix, never a cached one. */
export function getCurrentDevicePosition(): Promise<CurrentPositionFix> {
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    return Promise.reject(
      new DeviceCapabilityError(
        'location_unavailable',
        'This browser does not expose geolocation.',
      ),
    );
  }

  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      (position) =>
        resolve({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracyMetres: position.coords.accuracy,
          capturedAt: new Date(position.timestamp).toISOString(),
        }),
      (error) =>
        reject(
          error.code === error.PERMISSION_DENIED
            ? new DeviceCapabilityError(
                'permission_denied',
                'Location permission was not granted. No position was captured.',
              )
            : new DeviceCapabilityError(
                'location_failed',
                'A current location fix could not be obtained before the request ended.',
              ),
        ),
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15_000 },
    );
  });
}
