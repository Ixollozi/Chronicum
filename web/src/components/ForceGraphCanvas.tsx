"use client";

import { forwardRef, useEffect, useState } from "react";

// Client-only load so canvas + ref work with Next.
const ForceGraphCanvas = forwardRef(function ForceGraphCanvas(props: Record<string, unknown>, ref) {
  const [Comp, setComp] = useState<React.ComponentType<Record<string, unknown>> | null>(null);

  useEffect(() => {
    let alive = true;
    import("react-force-graph-2d").then((mod) => {
      if (alive) setComp(() => mod.default as unknown as React.ComponentType<Record<string, unknown>>);
    });
    return () => {
      alive = false;
    };
  }, []);

  if (!Comp) {
    return (
      <div className="flex h-full w-full items-center justify-center text-sm text-[var(--muted)]">
        Собираю сеть…
      </div>
    );
  }

  const C = Comp as React.ComponentType<Record<string, unknown> & { ref?: unknown }>;
  return <C ref={ref} {...props} />;
});

export default ForceGraphCanvas;
