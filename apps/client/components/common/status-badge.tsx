'use client';

import type { ReactNode } from 'react';
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleHelp,
  CloudOff,
  CloudUpload,
  FileQuestion,
  Loader2,
  PackageCheck,
  PackageX,
  Send,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Truck,
  XCircle,
} from 'lucide-react';

import { useT } from '@/components/i18n/locale-provider';
import { cn } from '@/lib/utils';

type Passability = 'open' | 'restricted' | 'closed' | 'unknown';
type RiskLevel = 'low' | 'moderate' | 'high' | 'unavailable';
/** The API's own lifecycles, not a parallel vocabulary. */
type ConsignmentStatus =
  | 'draft'
  | 'planned'
  | 'assigned'
  | 'in_transit'
  | 'delivered'
  | 'partially_delivered'
  | 'failed'
  | 'cancelled';
type TripStatus =
  | 'planned'
  | 'awaiting_driver'
  | 'active'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';
type RequestStatus =
  | 'open'
  | 'planned'
  | 'partially_fulfilled'
  | 'fulfilled'
  | 'cancelled';
type SyncStatus =
  | 'saved_locally'
  | 'waiting'
  | 'uploading'
  | 'sent'
  | 'awaiting_verification'
  | 'conflict'
  | 'failed';
type ReviewStatus =
  | 'reported'
  | 'confirmed_closed'
  | 'confirmed_restricted'
  | 'rejected'
  | 'clarification'
  // Incident lifecycle as the API reports it.
  | 'draft'
  | 'queued'
  | 'submitted'
  | 'under_review'
  | 'confirmed'
  | 'superseded'
  // Evidence upload lifecycle as the API reports it.
  | 'pending'
  | 'uploaded'
  | 'verified'
  | 'expired';

type StatusBadgeProps =
  | { kind: 'passability'; value: Passability }
  | { kind: 'risk'; value: RiskLevel }
  | { kind: 'consignment'; value: ConsignmentStatus }
  | { kind: 'trip'; value: TripStatus }
  | { kind: 'request'; value: RequestStatus }
  | { kind: 'sync'; value: SyncStatus }
  | { kind: 'review'; value: ReviewStatus };

/**
 * Tone maps to the documented design tokens. Success/caution/urgent/neutral
 * are the only four status colours in the product.
 */
type Tone = 'success' | 'caution' | 'urgent' | 'neutral' | 'info';

const TONE_CLASS: Record<Tone, string> = {
  success: 'bg-[#DCFCE7] text-[#166534] border-[#BBF7D0]',
  caution: 'bg-[#FEF3C7] text-[#92400E] border-[#FDE68A]',
  urgent: 'bg-[#FEE2E2] text-[#991B1B] border-[#FECACA]',
  neutral: 'bg-[#E2E8F0] text-[#475569] border-[#CBD5E1]',
  info: 'bg-[#DBEAFE] text-[#1E3A8A] border-[#BFDBFE]',
};

/**
 * Road status also uses a line pattern so colour is never the only carrier:
 * open solid, restricted dashed, closed crossed, unknown dotted.
 */
function PassabilityGlyph({ value }: { value: Passability }) {
  const common = {
    x1: 1,
    y1: 6,
    x2: 15,
    y2: 6,
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
  };
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 16 12"
      className="size-3.5 shrink-0"
      focusable="false"
    >
      {value === 'open' ? <line {...common} /> : null}
      {value === 'restricted' ? (
        <line {...common} strokeDasharray="3 2.5" />
      ) : null}
      {value === 'unknown' ? (
        <line {...common} strokeDasharray="0.5 3" />
      ) : null}
      {value === 'closed' ? (
        <>
          <line {...common} />
          <line
            x1={5}
            y1={2}
            x2={11}
            y2={10}
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
          />
          <line
            x1={11}
            y1={2}
            x2={5}
            y2={10}
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
          />
        </>
      ) : null}
    </svg>
  );
}

function resolve(props: StatusBadgeProps): {
  tone: Tone;
  icon: ReactNode;
  labelKey: string;
  categoryKey: string;
  pattern: string;
} {
  const cls = 'size-3.5 shrink-0';
  switch (props.kind) {
    case 'passability': {
      const tone: Record<Passability, Tone> = {
        open: 'success',
        restricted: 'caution',
        closed: 'urgent',
        unknown: 'neutral',
      };
      return {
        tone: tone[props.value],
        icon: <PassabilityGlyph value={props.value} />,
        labelKey: `status.passability.${props.value}`,
        categoryKey: 'status.passability.label',
        pattern: props.value,
      };
    }
    case 'risk': {
      const tone: Record<RiskLevel, Tone> = {
        low: 'success',
        moderate: 'caution',
        high: 'urgent',
        unavailable: 'neutral',
      };
      const icon: Record<RiskLevel, ReactNode> = {
        low: <ShieldCheck className={cls} aria-hidden="true" />,
        moderate: <ShieldAlert className={cls} aria-hidden="true" />,
        high: <AlertTriangle className={cls} aria-hidden="true" />,
        unavailable: <ShieldQuestion className={cls} aria-hidden="true" />,
      };
      return {
        tone: tone[props.value],
        icon: icon[props.value],
        labelKey: `status.risk.${props.value}`,
        categoryKey: 'status.risk.label',
        pattern: props.value,
      };
    }
    case 'consignment': {
      const tone: Record<ConsignmentStatus, Tone> = {
        draft: 'neutral',
        planned: 'neutral',
        assigned: 'info',
        in_transit: 'info',
        delivered: 'success',
        partially_delivered: 'caution',
        failed: 'urgent',
        cancelled: 'neutral',
      };
      const icon: Record<ConsignmentStatus, ReactNode> = {
        draft: <CircleDashed className={cls} aria-hidden="true" />,
        planned: <CircleDashed className={cls} aria-hidden="true" />,
        assigned: <CheckCircle2 className={cls} aria-hidden="true" />,
        in_transit: <Truck className={cls} aria-hidden="true" />,
        delivered: <PackageCheck className={cls} aria-hidden="true" />,
        partially_delivered: <PackageX className={cls} aria-hidden="true" />,
        failed: <PackageX className={cls} aria-hidden="true" />,
        cancelled: <Ban className={cls} aria-hidden="true" />,
      };
      return {
        tone: tone[props.value],
        icon: icon[props.value],
        labelKey: `status.consignment.${props.value}`,
        categoryKey: 'status.consignment.label',
        pattern: props.value,
      };
    }
    case 'trip': {
      const tone: Record<TripStatus, Tone> = {
        planned: 'neutral',
        awaiting_driver: 'caution',
        active: 'info',
        paused: 'caution',
        completed: 'success',
        failed: 'urgent',
        cancelled: 'neutral',
      };
      const icon: Record<TripStatus, ReactNode> = {
        planned: <CircleDashed className={cls} aria-hidden="true" />,
        awaiting_driver: <CircleHelp className={cls} aria-hidden="true" />,
        active: <Truck className={cls} aria-hidden="true" />,
        paused: <CircleDashed className={cls} aria-hidden="true" />,
        completed: <CheckCircle2 className={cls} aria-hidden="true" />,
        failed: <XCircle className={cls} aria-hidden="true" />,
        cancelled: <Ban className={cls} aria-hidden="true" />,
      };
      return {
        tone: tone[props.value],
        icon: icon[props.value],
        labelKey: `status.trip.${props.value}`,
        categoryKey: 'status.trip.label',
        pattern: props.value,
      };
    }
    case 'request': {
      // "Partly fulfilled" is caution, never success: the facility still does
      // not have everything it asked for.
      const tone: Record<RequestStatus, Tone> = {
        open: 'caution',
        planned: 'info',
        partially_fulfilled: 'caution',
        fulfilled: 'success',
        cancelled: 'neutral',
      };
      const icon: Record<RequestStatus, ReactNode> = {
        open: <FileQuestion className={cls} aria-hidden="true" />,
        planned: <CircleDashed className={cls} aria-hidden="true" />,
        partially_fulfilled: <PackageX className={cls} aria-hidden="true" />,
        fulfilled: <PackageCheck className={cls} aria-hidden="true" />,
        cancelled: <Ban className={cls} aria-hidden="true" />,
      };
      return {
        tone: tone[props.value],
        icon: icon[props.value],
        labelKey: `status.request.${props.value}`,
        categoryKey: 'status.request.label',
        pattern: props.value,
      };
    }
    case 'sync': {
      const tone: Record<SyncStatus, Tone> = {
        saved_locally: 'neutral',
        waiting: 'caution',
        uploading: 'info',
        sent: 'success',
        awaiting_verification: 'info',
        conflict: 'caution',
        failed: 'urgent',
      };
      const icon: Record<SyncStatus, ReactNode> = {
        saved_locally: <CheckCircle2 className={cls} aria-hidden="true" />,
        waiting: <CloudOff className={cls} aria-hidden="true" />,
        uploading: (
          <Loader2 className={cn(cls, 'animate-spin')} aria-hidden="true" />
        ),
        sent: <Send className={cls} aria-hidden="true" />,
        awaiting_verification: (
          <CloudUpload className={cls} aria-hidden="true" />
        ),
        conflict: <AlertTriangle className={cls} aria-hidden="true" />,
        failed: <XCircle className={cls} aria-hidden="true" />,
      };
      return {
        tone: tone[props.value],
        icon: icon[props.value],
        labelKey: `status.sync.${props.value}`,
        categoryKey: 'status.sync.label',
        pattern: props.value,
      };
    }
    case 'review': {
      const tone: Record<ReviewStatus, Tone> = {
        reported: 'caution',
        confirmed_closed: 'urgent',
        confirmed_restricted: 'caution',
        rejected: 'neutral',
        clarification: 'info',
        draft: 'neutral',
        queued: 'info',
        submitted: 'caution',
        under_review: 'info',
        confirmed: 'urgent',
        superseded: 'neutral',
        pending: 'caution',
        uploaded: 'info',
        verified: 'info',
        expired: 'neutral',
      };
      const icon: Record<ReviewStatus, ReactNode> = {
        reported: <FileQuestion className={cls} aria-hidden="true" />,
        confirmed_closed: <Ban className={cls} aria-hidden="true" />,
        confirmed_restricted: (
          <AlertTriangle className={cls} aria-hidden="true" />
        ),
        rejected: <XCircle className={cls} aria-hidden="true" />,
        clarification: <CircleHelp className={cls} aria-hidden="true" />,
        draft: <FileQuestion className={cls} aria-hidden="true" />,
        queued: <CircleHelp className={cls} aria-hidden="true" />,
        submitted: <FileQuestion className={cls} aria-hidden="true" />,
        under_review: <CircleHelp className={cls} aria-hidden="true" />,
        confirmed: <Ban className={cls} aria-hidden="true" />,
        superseded: <XCircle className={cls} aria-hidden="true" />,
        pending: <FileQuestion className={cls} aria-hidden="true" />,
        uploaded: <CircleHelp className={cls} aria-hidden="true" />,
        verified: <ShieldCheck className={cls} aria-hidden="true" />,
        expired: <XCircle className={cls} aria-hidden="true" />,
      };
      return {
        tone: tone[props.value],
        icon: icon[props.value],
        labelKey: `status.review.${props.value}`,
        categoryKey: 'status.review.label',
        pattern: props.value,
      };
    }
  }
}

/**
 * One badge for every operational status in the product. The label is always
 * text; the icon or line pattern is a second carrier for colour-blind users.
 */
export function StatusBadge({
  className,
  ...props
}: StatusBadgeProps & { className?: string }) {
  const t = useT();
  const { tone, icon, labelKey, categoryKey, pattern } = resolve(props);
  return (
    <span
      data-status-kind={props.kind}
      data-status-value={pattern}
      className={cn(
        'inline-flex h-6 w-fit shrink-0 items-center gap-1.5 rounded-md border px-2 text-xs font-medium whitespace-nowrap',
        TONE_CLASS[tone],
        className,
      )}
    >
      {icon}
      <span className="sr-only">{t(categoryKey)}: </span>
      {t(labelKey)}
    </span>
  );
}
