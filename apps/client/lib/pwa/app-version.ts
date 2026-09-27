/** Which build is running. */
export const APP_VERSION =
  process.env.NEXT_PUBLIC_APP_VERSION?.trim() || 'local development build';
