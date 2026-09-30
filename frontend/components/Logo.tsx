// AutoApply logo mark ("A" + AI spark). Created by Abhiram. MIT license.
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
      </defs>
      <rect x="16" y="16" width="480" height="480" rx="112" fill={`url(#bg${id})`} />
      <rect x="16" y="16" width="480" height="480" rx="112" fill={`url(#gl${id})`} />
      <path fill="#fff" fillRule="evenodd" d="M130 404 L222 118 L290 118 L382 404 L316 404 L298 344 L214 344 L196 404 Z M230 290 L282 290 L256 202 Z" />
      <path d="M392 96 l12 30 30 12 -30 12 -12 30 -12 -30 -30 -12 30 -12 z" fill="#fff" />
    </svg>
  );
}
