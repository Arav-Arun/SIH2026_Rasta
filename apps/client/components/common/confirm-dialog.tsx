'use client';

import { useId, useState, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';

type ConfirmDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Use the urgent tone for irreversible or network-changing decisions. */
  destructive?: boolean;
  /** Require a written reason (recorded in the audit trail). */
  requireReason?: boolean;
  /** Runs on confirm; the dialog stays open and busy until it resolves. */
  onConfirm: (reason: string) => Promise<void> | void;
};

/** Focus-trapping confirmation for operational decisions. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel,
  destructive = false,
  requireReason = false,
  onConfirm,
}: ConfirmDialogProps) {
  const t = useT();
  const reasonId = useId();
  const errorId = useId();
  const [reason, setReason] = useState('');
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  const submit = async () => {
    const trimmed = reason.trim();
    if (requireReason && !trimmed) {
      setReasonError(t('confirm.reasonRequired'));
      return;
    }
    setReasonError(null);
    setWorking(true);
    try {
      await onConfirm(trimmed);
      setReason('');
      onOpenChange(false);
    } catch {
      // Keep the dialog open with the reason preserved; the caller surfaces
      // the error through its own ErrorPanel.
    } finally {
      setWorking(false);
    }
  };

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (working) return;
        onOpenChange(next);
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title ?? t('confirm.title')}</AlertDialogTitle>
          {description ? (
            <AlertDialogDescription>{description}</AlertDialogDescription>
          ) : null}
        </AlertDialogHeader>
        {requireReason ? (
          <div className="grid gap-1.5">
            <Label htmlFor={reasonId}>{t('confirm.reasonLabel')}</Label>
            <Textarea
              id={reasonId}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={t('confirm.reasonPlaceholder')}
              aria-invalid={reasonError ? true : undefined}
              aria-describedby={reasonError ? errorId : undefined}
              disabled={working}
              rows={3}
            />
            {reasonError ? (
              <p id={errorId} className="text-xs text-[#991B1B]">
                {reasonError}
              </p>
            ) : null}
          </div>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={working}>
            {cancelLabel ?? t('confirm.cancel')}
          </AlertDialogCancel>
          <Button
            variant={destructive ? 'destructive' : 'default'}
            onClick={submit}
            disabled={working}
            aria-busy={working}
          >
            {working ? (
              <Loader2 aria-hidden="true" className="animate-spin" />
            ) : null}
            {working
              ? t('confirm.working')
              : (confirmLabel ?? t('confirm.confirm'))}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
