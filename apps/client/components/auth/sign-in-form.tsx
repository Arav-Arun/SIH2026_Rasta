'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { CircleAlert, LoaderCircle, RefreshCw } from 'lucide-react';

import { useAuth } from '@/components/auth/auth-provider';
import { countUnsentWork } from '@/lib/offline/db';
import { useT } from '@/components/i18n/locale-provider';
import { homePathFor } from '@/lib/auth/policy';
import { LanguageSwitcher } from '@/components/i18n/language-switcher';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export function SignInForm() {
  const router = useRouter();
  const {
    bootstrapStatus,
    refreshServerIdentity,
    session,
    signInWithPassword,
    signOut,
    status,
    workspace,
  } = useAuth();
  const t = useT();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  // Set while the person decides what to do about work not yet sent.
  const [unsentOnSignOut, setUnsentOnSignOut] = useState<number | null>(null);
  // Set by a sign-in on this page, so the workspace opens once the server has
  // verified it. A session that was already here still waits for "Continue".
  const [justSignedIn, setJustSignedIn] = useState(false);

  /**
   * Signing out deletes this person's drafts and queued work from the device
   * (so the next person on a shared phone cannot read them).
   */
  async function requestSignOut() {
    let unsent = 0;
    try {
      unsent = await countUnsentWork(workspace?.identity.profileId ?? null);
    } catch {
      // Storage that cannot be read cannot be purged either; nothing is lost.
    }
    if (unsent === 0) {
      await signOut();
      return;
    }
    setUnsentOnSignOut(unsent);
  }

  async function submit(form: FormData) {
    setError(undefined);
    setSubmitting(true);
    const emailValue = form.get('email');
    const passwordValue = form.get('password');
    const email = typeof emailValue === 'string' ? emailValue.trim() : '';
    const password = typeof passwordValue === 'string' ? passwordValue : '';
    const result = await signInWithPassword(email, password);
    setSubmitting(false);
    if (!result.ok) {
      setError(t('signIn.rejected'));
      return;
    }
    setJustSignedIn(true);
  }

  // One title and one line per identity state, in the reader's language.
  const bootstrapCopy = {
    title: t(`signIn.bootstrap.${bootstrapStatus}.title`),
    text: t(`signIn.bootstrap.${bootstrapStatus}.text`),
  };

  const isWorkspaceReady =
    (bootstrapStatus === 'ready' || bootstrapStatus === 'offline') &&
    workspace !== null;

  useEffect(() => {
    if (justSignedIn && isWorkspaceReady && workspace) {
      router.replace(homePathFor(workspace.identity));
    }
  }, [justSignedIn, isWorkspaceReady, workspace, router]);

  return (
    <main className="flex min-h-svh items-center justify-center bg-background px-4 py-10 text-foreground">
      <div className="w-full max-w-sm">
        <div className="mb-3 flex justify-end">
          <LanguageSwitcher />
        </div>
        <Card className="shadow-none">
          <CardHeader>
            <CardTitle>{t('signIn.title')}</CardTitle>
          </CardHeader>
          <CardContent>
            {status === 'unconfigured' ? (
              <Alert>
                <CircleAlert />
                <AlertTitle>{t('signIn.unconfiguredTitle')}</AlertTitle>
                <AlertDescription>
                  {t('signIn.unconfiguredBody')}
                </AlertDescription>
              </Alert>
            ) : session ? (
              <div className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  {t('signIn.sessionExists', {
                    name:
                      workspace?.displayName ??
                      session.user.email ??
                      t('signIn.currentUser'),
                  })}
                </p>
                {isWorkspaceReady ? (
                  <Button
                    type="button"
                    // Where this role's work starts: a driver or field officer
                    // sent to the dispatcher overview would meet a refusal.
                    onClick={() =>
                      router.replace(homePathFor(workspace.identity))
                    }
                  >
                    {t('signIn.continue')}
                  </Button>
                ) : (
                  <Alert>
                    <CircleAlert />
                    <AlertTitle>{bootstrapCopy.title}</AlertTitle>
                    <AlertDescription>{bootstrapCopy.text}</AlertDescription>
                  </Alert>
                )}
                <div className="flex flex-wrap gap-2">
                  {!isWorkspaceReady ? (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={bootstrapStatus === 'loading'}
                      onClick={() => void refreshServerIdentity()}
                    >
                      <RefreshCw
                        className={
                          bootstrapStatus === 'loading' ? 'animate-spin' : ''
                        }
                      />
                      {t('signIn.checkAgain')}
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void requestSignOut()}
                  >
                    {t('signIn.signOutLocally')}
                  </Button>
                </div>
                {unsentOnSignOut !== null ? (
                  <Alert variant="destructive">
                    <CircleAlert />
                    <AlertTitle>{t('signIn.unsentTitle')}</AlertTitle>
                    <AlertDescription>
                      <p>
                        {t('signIn.unsentText', { count: unsentOnSignOut })}
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button
                          type="button"
                          onClick={() => setUnsentOnSignOut(null)}
                        >
                          {t('signIn.stay')}
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => {
                            setUnsentOnSignOut(null);
                            void signOut();
                          }}
                        >
                          {t('signIn.discardAndSignOut')}
                        </Button>
                      </div>
                    </AlertDescription>
                  </Alert>
                ) : null}
              </div>
            ) : (
              <form
                className="space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void submit(new FormData(event.currentTarget));
                }}
              >
                <div className="space-y-2">
                  <Label htmlFor="email">{t('signIn.email')}</Label>
                  <Input
                    id="email"
                    name="email"
                    type="email"
                    autoComplete="username"
                    required
                    disabled={status === 'loading' || submitting}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="password">{t('signIn.password')}</Label>
                  <Input
                    id="password"
                    name="password"
                    type="password"
                    autoComplete="current-password"
                    minLength={8}
                    required
                    disabled={status === 'loading' || submitting}
                  />
                </div>
                {error ? (
                  <p className="text-sm text-destructive" role="alert">
                    {error}
                  </p>
                ) : null}
                <Button
                  className="w-full"
                  type="submit"
                  disabled={status === 'loading' || submitting}
                >
                  {submitting ? (
                    <LoaderCircle className="animate-spin" />
                  ) : null}
                  {submitting ? t('signIn.signingIn') : t('signIn.submit')}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
