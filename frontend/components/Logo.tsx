// AutoApply logo mark (paper plane + AI spark). Created by Abhiram. MIT license.
import { useId } from "react";

export function LogoMark({ size = 32, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, "");
  return (
    <svg width={size} height={size} viewBox="0 0 512 512" className={className} role="img" aria-label="AutoApply">
      <defs>
        <linearGradient id={`bg${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6366F1" />
          <stop offset="1" stopColor="#7C3AED" />
        </linearGradient>
        <radialGradient id={`gl${id}`} cx="0.28" cy="0.18" r="0.75">
          <stop offset="0" stopColor="#fff" stopOpacity="0.3" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={`wg${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#E0E7FF" />
          <stop offset="1" stopColor="#C7D2FE" />
        </linearGradient>
      </defs>
      <rect x="16" y="16" width="480" height="480" rx="112" fill={`url(#bg${id})`} />
      <rect x="16" y="16" width="480" height="480" rx="112" fill={`url(#gl${id})`} />
      <path d="M104 372 C 150 356, 176 330, 196 300" fill="none" stroke="#fff" strokeOpacity="0.55" strokeWidth="14" strokeLinecap="round" strokeDasharray="2 30" />
      <path d="M120 250 L396 128 L324 392 L262 300 Z" fill="#fff" />
      <path d="M396 128 L262 300 L246 372 L286 322 Z" fill={`url(#wg${id})`} />
      <path d="M396 128 L262 300" stroke="#A5B4FC" strokeWidth="6" strokeLinecap="round" />
      <path d="M398 334 l10 26 26 10 -26 10 -10 26 -10 -26 -26 -10 26 -10 z" fill="#fff" />
    </svg>
  );
}
