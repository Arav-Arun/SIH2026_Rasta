import AsyncStorage from '@react-native-async-storage/async-storage';
import { HazardCategory, RiskTier } from '../types';

/** Autosaved report draft. */

const STORAGE_KEY_DRAFT = '@rasta_report_draft_v1';

interface ReportDraft {
  category: HazardCategory;
  corridorCode: string;
  landmark: string;
  severity: RiskTier;
  notes: string;
  photoUri: string | null;
  latitude: number | null;
  longitude: number | null;
  accuracyMeters: number | null;
  altitudeMeters: number | null;
  fixTakenAt: string | null;
  /** How the position was obtained. Recorded so a reviewer can weigh it. */
  positionSource: PositionSource | null;
  savedAt: string;
}

/**
 * Where a coordinate came from. A pin dropped by hand is a real, usable
 * position but it is not a device fix, and the control room is told which.
 */
export type PositionSource = 'device_gps' | 'manual_pin';

type DraftPatch = Partial<Omit<ReportDraft, 'savedAt'>>;

type Listener = (draft: ReportDraft | null) => void;
const listeners = new Set<Listener>();
function notify(draft: ReportDraft | null) {
  listeners.forEach((listener) => {
    try {
      listener(draft);
    } catch (err) {
      console.error('Draft listener error', err);
    }
  });
}

export async function loadDraft(): Promise<ReportDraft | null> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY_DRAFT);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ReportDraft;
    // A draft written by an older build may be missing fields; treat anything
    // unreadable as absent rather than crashing the capture screen.
    if (!parsed || typeof parsed !== 'object' || !parsed.category) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** Merges a change into the stored draft. Safe to call on every keystroke. */
export async function saveDraft(
  patch: DraftPatch,
): Promise<ReportDraft | null> {
  try {
    const current = await loadDraft();
    const next: ReportDraft = {
      category: 'landslide',
      corridorCode: 'NH-6',
      landmark: '',
      severity: 'high',
      notes: '',
      photoUri: null,
      latitude: null,
      longitude: null,
      accuracyMeters: null,
      altitudeMeters: null,
      fixTakenAt: null,
      positionSource: null,
      ...current,
      ...patch,
      savedAt: new Date().toISOString(),
    };
    await AsyncStorage.setItem(STORAGE_KEY_DRAFT, JSON.stringify(next));
    notify(next);
    return next;
  } catch {
    // A failed write must not block the officer from continuing to type; the
    // capture screen keeps its own in-memory state either way.
    return null;
  }
}

export async function clearDraft(): Promise<void> {
  try {
    await AsyncStorage.removeItem(STORAGE_KEY_DRAFT);
  } finally {
    notify(null);
  }
}
