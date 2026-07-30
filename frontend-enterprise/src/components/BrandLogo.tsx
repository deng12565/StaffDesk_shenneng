import { cn } from '@/lib/utils';
import logoMark from '../assets/shenergy-logo-mark.png';

export type BrandLogoProps = {
  /** Hide the product wordmark and only render the corporate logo mark. */
  markOnly?: boolean;
  /** Size of the square logo mark in pixels. */
  markSize?: number;
  className?: string;
  /** Extra classes applied to the wordmark wrapper (e.g. to hide it responsively). */
  wordmarkClassName?: string;
};

/** Brand logo lockup (SHENERGY mark + product wordmark). */
export default function BrandLogo({
  markOnly = false,
  markSize = 28,
  className,
  wordmarkClassName,
}: BrandLogoProps) {
  return (
    <span className={cn('flex items-center gap-[8px] overflow-hidden p-[4px]', className)}>
      <img
        src={logoMark}
        alt="申能 SHENERGY"
        className="shrink-0"
        style={{ width: markSize, height: markSize }}
      />
      {!markOnly && (
        <span className={cn('flex flex-col items-center gap-[2px] leading-none', wordmarkClassName)}>
          <strong className="text-[17px] font-semibold leading-none text-[#18181a]">
            小申数字员工
          </strong>
        </span>
      )}
    </span>
  );
}
