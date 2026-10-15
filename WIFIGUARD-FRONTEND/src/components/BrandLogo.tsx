/** Shared original brand asset; do not replace it with a generated icon. */
export function BrandLogo({ className = "" }: { className?: string }) {
  return (
    <img
      src="/wifi-guard-logo.png"
      alt="WIFI-GUARD 로고"
      width={44}
      height={44}
      className={`brand-logo ${className}`.trim()}
      decoding="async"
    />
  );
}
