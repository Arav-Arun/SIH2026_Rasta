import Dexie, { type Table } from 'dexie';

import type {
  DraftRecord,
  EntityRecord,
  MediaRecord,
  MutationEnvelope,
} from './types';

/** Local stores for offline drafts, the outbox and cached reference data. */
export class RastaOfflineDatabase extends Dexie {
  drafts!: Table<DraftRecord, string>;
  outbox!: Table<MutationEnvelope, string>;
  media!: Table<MediaRecord, string>;
  entities!: Table<EntityRecord, string>;

  constructor(name = 'rasta-offline') {
    super(name);
    this.version(1).stores({
      drafts: 'draftId, kind, profileId, updatedAt',
      outbox: 'mutationId, state, profileId, createdAt, type',
      media: 'mediaId, mutationId, profileId',
      entities: 'key, collection, profileId, fetchedAt',
    });
  }
}

let database: RastaOfflineDatabase | null = null;

/**
 * The shared database, or null where IndexedDB does not exist, server
 * rendering, a hardened browser, a private window that blocks storage.
 */
export function getOfflineDatabase(): RastaOfflineDatabase | null {
  if (typeof indexedDB === 'undefined') return null;
  if (!database) {
    database = new RastaOfflineDatabase();
  }
  return database;
}

/** Test seam: lets a suite work against its own database instance. */
export function setOfflineDatabase(next: RastaOfflineDatabase | null): void {
  database = next;
}

/**
 * What `purgeProfileData` would destroy before the server has it: queued or
 * refused changes (anything the server has not accepted) and unfinished drafts.
 */
export async function countUnsentWork(
  profileId: string | null,
): Promise<number> {
  const db = getOfflineDatabase();
  if (!db) return 0;
  const belongs = (row: { profileId?: unknown }) =>
    row.profileId === profileId || row.profileId == null;
  const [outbox, drafts] = await Promise.all([
    db.outbox.toArray(),
    db.drafts.toArray(),
  ]);
  return (
    outbox.filter((row) => belongs(row) && row.state !== 'accepted').length +
    drafts.filter(belongs).length
  );
}

/**
 * Removes everything belonging to one person. Called on sign-out, so the next
 * user of a shared district phone cannot read the previous one's drafts.
 */
export async function purgeProfileData(
  profileId: string | null,
): Promise<void> {
  const db = getOfflineDatabase();
  if (!db) return;

  await db.transaction(
    'rw',
    db.drafts,
    db.outbox,
    db.media,
    db.entities,
    async () => {
      for (const table of [db.drafts, db.outbox, db.media, db.entities]) {
        await table
          .where('profileId')
          .equals(profileId as never)
          .delete();
        await table
          .filter((row) => (row as { profileId?: unknown }).profileId == null)
          .delete();
      }
    },
  );
}
