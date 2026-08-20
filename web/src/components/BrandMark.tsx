import Image from "next/image";

type BrandMarkProps = {
  size?: number;
  className?: string;
  priority?: boolean;
};

export function BrandMark({ size = 40, className = "", priority = false }: BrandMarkProps) {
  return (
    <Image
      src="/logo.png"
      alt="Chronicum"
      width={size}
      height={size}
      priority={priority}
      className={`brand-mark ${className}`.trim()}
      style={{ width: size, height: size }}
    />
  );
}
