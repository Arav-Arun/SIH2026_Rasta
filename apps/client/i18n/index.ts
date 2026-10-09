// The catalogues offered in this build, each loaded only when chosen, so a
// visitor downloads one language rather than all of them. English is not
// listed: it is the fallback and ships with the app. A language is listed
// only when its catalogue has every English string.
export const CATALOGUE_LOADERS: Record<string, () => Promise<{ default: unknown }>> = {
  as: () => import('./as.json'),
  bn: () => import('./bn.json'),
  brx: () => import('./brx.json'),
  hi: () => import('./hi.json'),
};
