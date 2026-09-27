import * as ImagePicker from 'expo-image-picker';
import * as Location from 'expo-location';
import {
  CaptureDeliveryReceipt,
  CrewSession,
  HazardReport,
  ObservationCapture,
} from '../types';
import {
  enqueueHazardReport,
  getNetworkMode,
  sendStoredReport,
} from './offlineStorage';

// ---------------------------------------------------------------------------
// Device capture, real camera, real GPS. Nothing here invents a coordinate or
// a photo: when a permission is refused or no fix arrives, the caller is told.
// ---------------------------------------------------------------------------

export interface GpsFix {
  latitude: number;
  longitude: number;
  altitudeMeters?: number;
  accuracyMeters?: number;
  takenAt: string;
}

type GpsResult =
  | { ok: true; fix: GpsFix }
  | { ok: false; reason: string; permissionDenied: boolean };

type PhotoResult =
  | { ok: true; uri: string; width: number; height: number }
  | { ok: false; reason: string; cancelled: boolean };

/** Requests a single high-accuracy fix from the device GPS. */
export async function captureGpsFix(): Promise<GpsResult> {
  try {
    const permission = await Location.requestForegroundPermissionsAsync();
    if (permission.status !== 'granted') {
      return {
        ok: false,
        permissionDenied: true,
        reason:
          'Location permission was refused. A disruption report without a position cannot be placed on the map.',
      };
    }

    const position = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.High,
    });

    return {
      ok: true,
      fix: {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        altitudeMeters:
          position.coords.altitude === null
            ? undefined
            : Math.round(position.coords.altitude),
        accuracyMeters:
          position.coords.accuracy === null
            ? undefined
            : Math.round(position.coords.accuracy),
        takenAt: new Date(position.timestamp).toISOString(),
      },
    };
  } catch (err) {
    return {
      ok: false,
      permissionDenied: false,
      reason:
        err instanceof Error
          ? err.message
          : 'The device could not obtain a position fix.',
    };
  }
}

/** Opens the camera for the onboard observer. Gallery picking is deliberately
 *  not offered: a disruption report must be photographed at the disruption. */
export async function capturePhoto(): Promise<PhotoResult> {
  try {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (permission.status !== 'granted') {
      return {
        ok: false,
        cancelled: false,
        reason:
          'Camera permission was refused. Photo evidence is what lets the control room verify this report.',
      };
    }

    const result = await ImagePicker.launchCameraAsync({
      quality: 0.6,
      exif: true,
      allowsEditing: false,
    });

    if (result.canceled || !result.assets?.length) {
      return { ok: false, cancelled: true, reason: 'No photo was taken.' };
    }

    const asset = result.assets[0];
    return {
      ok: true,
      uri: asset.uri,
      width: asset.width ?? 0,
      height: asset.height ?? 0,
    };
  } catch (err) {
    return {
      ok: false,
      cancelled: false,
      reason:
        err instanceof Error ? err.message : 'The camera could not be opened.',
    };
  }
}

// ---------------------------------------------------------------------------
// Sending, saved on the phone first, then filed through the outbox.
// ---------------------------------------------------------------------------

/**
 * Saves an observation on this phone and, unless the person is holding reports
 * or has no account session, tries to file it with the control room straight
 * away.
 */
export async function publishObservation(
  capture: ObservationCapture,
  crew: CrewSession,
): Promise<{ report: HazardReport; receipt: CaptureDeliveryReceipt }> {
  const notes: string[] = [];

  if (capture.latitude === null || capture.longitude === null) {
    // The capture screen does not offer Send without a position; this is the
    // guard behind it. Nothing is saved that could never be filed.
    throw new Error(
      'A report needs a position (a GPS fix or a pin) before it can be saved.',
    );
  }

  const held = (await getNetworkMode()) === 'dead_zone';
  const saved = await enqueueHazardReport({
    category: capture.category,
    categoryLabel: capture.categoryLabel,
    corridorCode: capture.corridorCode,
    locationName: capture.locationName,
    latitude: capture.latitude,
    longitude: capture.longitude,
    altitudeMeters: capture.altitudeMeters,
    severity: capture.severity,
    notes: capture.notes,
    photoUri: capture.photoUri,
    reporterRole: crew.role,
    reporterName: crew.displayName,
    crewCode: crew.crewCode,
    accuracyMeters: capture.accuracyMeters,
    capturedAtIso: capture.fixTakenAt ?? new Date().toISOString(),
    positionSource: capture.positionSource ?? 'device_gps',
  });

  const receipt: CaptureDeliveryReceipt = {
    localOutbox: true,
    controlRoomDb: false,
    evidence: capture.photoUri ? 'not_sent' : null,
    notes,
  };

  if (held) {
    notes.push(
      'Reports are being held on this phone. It will be sent when you release the hold on the Outbox screen.',
    );
    return { report: saved, receipt };
  }
  if (crew.mode === 'local_only') {
    notes.push(
      'Local-only session: this report stays on this device. Sign in to send it to the control room.',
    );
    return { report: saved, receipt };
  }

  const report = (await sendStoredReport(saved.id)) ?? saved;
  receipt.controlRoomDb = report.syncStatus === 'synced';
  receipt.evidence = capture.photoUri
    ? (report.evidenceStatus ?? 'not_sent')
    : null;

  if (report.syncStatus === 'queued' || report.syncStatus === 'syncing') {
    notes.push(
      `Not sent yet${report.filing?.lastError ? `: ${report.filing.lastError}` : '.'} ` +
        'It stays in the outbox and is tried again automatically.',
    );
  } else if (report.syncStatus === 'failed') {
    notes.push(
      `The control room refused this report${
        report.filing?.lastError ? `: ${report.filing.lastError}` : '.'
      }`,
    );
  }
  if (report.filing?.evidenceError) notes.push(report.filing.evidenceError);

  // Risk is not scored here.
  notes.push(
    'Risk is scored by the control room, not on this phone, so this report ' +
      'changes no score until a reviewer has seen it.',
  );

  return { report, receipt };
}
