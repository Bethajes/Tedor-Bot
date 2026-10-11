/**
 * The step counter above every form.
 *
 * Shows "Step 4 of 26" and a bar, so a tutor filling a 26-step application at
 * their phone can see it shrinking (Requirements 3.2).
 */

export interface ProgressBarProps {
  current: number;
  total: number;
  label?: string;
}

export default function ProgressBar({ current, total, label }: ProgressBarProps) {
  const safeTotal = total > 0 ? total : 1;
  const percentage = Math.min(100, Math.max(0, (current / safeTotal) * 100));
  const rounded = Math.round(percentage);

  return (
    <div className="mb-4" data-testid="progress-bar">
      <div className="mb-1 flex items-center justify-between text-xs font-medium text-slate-600">
        <span>{label ?? `Step ${current} of ${total}`}</span>
        <span data-testid="progress-percentage">{rounded}%</span>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-slate-200"
        role="progressbar"
        aria-valuenow={current}
        aria-valuemin={1}
        aria-valuemax={safeTotal}
        aria-label={label ?? `Step ${current} of ${total}`}
      >
        <div
          className="h-full rounded-full bg-tedor-500 transition-all"
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  );
}