/** Where the landing page's buttons go. The APK is served by the public repository's latest release. */
export const APK_URL =
  process.env.NEXT_PUBLIC_APK_URL ||
  'https://github.com/Arav-Arun/SIH2026_Rasta/releases/latest/download/rasta.apk';

export const CONTROL_ROOM_PATH = '/sign-in';

export const SOURCE_URL = 'https://github.com/Arav-Arun/SIH2026_Rasta';

/** The 3-minute walkthrough on YouTube. Change the id here and everything follows. */
export const VIDEO_ID = 'd7QWfgCOpME';

export const VIDEO_URL = `https://youtu.be/${VIDEO_ID}`;
