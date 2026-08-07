"use client";

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

export type FancySelectOption = {
  value: string;
  label: string;
};

type FancySelectProps = {
  value: string;
  onChange: (value: string) => void;
  options: FancySelectOption[];
  "aria-label": string;
  className?: string;
  variant?: "archive" | "graph";
  /** Jump to a global search (e.g. graph top bar) with the current filter text */
  onSeekGlobal?: (query: string) => void;
  seekGlobalLabel?: string;
};

export function FancySelect({
  value,
  onChange,
  options,
  "aria-label": ariaLabel,
  className = "",
  variant = "archive",
  onSeekGlobal,
  seekGlobalLabel = "Искать в сети",
}: FancySelectProps) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [filter, setFilter] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [menuPos, setMenuPos] = useState<{
    top: number;
    left: number;
    width: number;
    maxHeight: number;
    openUp: boolean;
  } | null>(null);

  const selected = options.find((o) => o.value === value) ?? options[0];

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, filter]);

  useEffect(() => {
    setMounted(true);
  }, []);

  const placeMenu = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const gap = 6;
    const pad = 8;
    const preferred = Math.min(320, window.innerHeight * 0.5);
    const spaceBelow = window.innerHeight - rect.bottom - gap - pad;
    const spaceAbove = rect.top - gap - pad;
    const openUp = spaceBelow < 160 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(160, Math.min(preferred, openUp ? spaceAbove : spaceBelow));
    const width = Math.min(
      Math.max(rect.width, variant === "archive" ? 180 : rect.width),
      window.innerWidth - pad * 2
    );
    let left = rect.left;
    if (left + width > window.innerWidth - pad) {
      left = Math.max(pad, rect.right - width);
    }
    left = Math.max(pad, left);

    setMenuPos({
      top: openUp ? rect.top - gap : rect.bottom + gap,
      left,
      width,
      maxHeight,
      openUp,
    });
  }, [variant]);

  useLayoutEffect(() => {
    if (!open) {
      setMenuPos(null);
      return;
    }
    placeMenu();
    setFilter("");
    setActiveIndex(0);
    const t = window.setTimeout(() => filterRef.current?.focus(), 20);
    const onScroll = () => placeMenu();
    window.addEventListener("resize", placeMenu);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("resize", placeMenu);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open, placeMenu]);

  useEffect(() => {
    if (!open) return;
    function onPointer(e: MouseEvent | TouchEvent) {
      const t = e.target as Node;
      if (rootRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("touchstart", onPointer, { passive: true });
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("touchstart", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex, filtered]);

  function choose(next: string) {
    onChange(next);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function seekGlobal() {
    if (!onSeekGlobal) return;
    onSeekGlobal(filter.trim());
    setOpen(false);
  }

  function onTriggerKeyDown(e: KeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
    }
  }

  function onFilterKeyDown(e: KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filtered.length === 1) {
        choose(filtered[0].value);
      } else if (filtered[activeIndex]) {
        choose(filtered[activeIndex].value);
      } else if (onSeekGlobal && filter.trim()) {
        seekGlobal();
      }
    } else if (e.key === "Tab" && onSeekGlobal) {
      // keep default; close after blur via outside handler
    }
  }

  const menu =
    open && menuPos && mounted
      ? createPortal(
          <div
            ref={menuRef}
            className={`fancy-select-menu fancy-select-menu--${variant}`}
            style={{
              position: "fixed",
              top: menuPos.openUp ? undefined : menuPos.top,
              bottom: menuPos.openUp ? `calc(100dvh - ${menuPos.top}px)` : undefined,
              left: menuPos.left,
              width: menuPos.width,
              maxHeight: menuPos.maxHeight,
              zIndex: 120,
            }}
          >
            <div className="fancy-select-search">
              <input
                ref={filterRef}
                type="search"
                value={filter}
                onChange={(e) => {
                  setFilter(e.target.value);
                  setActiveIndex(0);
                }}
                onKeyDown={onFilterKeyDown}
                placeholder="Поиск…"
                className="fancy-select-search-input"
                aria-label={`Поиск: ${ariaLabel}`}
                autoComplete="off"
                autoCorrect="off"
                spellCheck={false}
              />
              {onSeekGlobal && (
                <button
                  type="button"
                  className="fancy-select-seek"
                  onClick={seekGlobal}
                  title={seekGlobalLabel}
                >
                  {seekGlobalLabel}
                  <span aria-hidden> →</span>
                </button>
              )}
            </div>
            <ul
              ref={listRef}
              id={listId}
              className="fancy-select-options"
              role="listbox"
              aria-label={ariaLabel}
            >
              {filtered.length === 0 && (
                <li className="fancy-select-empty">
                  Ничего не найдено
                  {onSeekGlobal && filter.trim() && (
                    <button type="button" className="fancy-select-seek-inline" onClick={seekGlobal}>
                      {seekGlobalLabel} →
                    </button>
                  )}
                </li>
              )}
              {filtered.map((opt, index) => {
                const isSelected = opt.value === value;
                const isActive = index === activeIndex;
                return (
                  <li key={`${opt.value || "__empty"}-${index}`} role="presentation">
                    <button
                      type="button"
                      role="option"
                      data-index={index}
                      aria-selected={isSelected}
                      className="fancy-select-option"
                      data-selected={isSelected || undefined}
                      data-active={isActive || undefined}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => choose(opt.value)}
                    >
                      <span className="fancy-select-option-label">{opt.label}</span>
                      {isSelected && (
                        <span className="fancy-select-check" aria-hidden="true">
                          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                            <path
                              d="M2.75 7.25 5.6 10.1 11.25 3.9"
                              stroke="currentColor"
                              strokeWidth="1.6"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>,
          document.body
        )
      : null;

  return (
    <div
      ref={rootRef}
      className={`fancy-select fancy-select--${variant} ${className}`.trim()}
      data-open={open || undefined}
    >
      <button
        ref={triggerRef}
        type="button"
        className="fancy-select-trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
      >
        <span className="fancy-select-value" data-placeholder={value === "" || undefined}>
          {selected?.label ?? "—"}
        </span>
        <span className="fancy-select-chevron" aria-hidden="true">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path
              d="M2.5 4.25 6 7.75l3.5-3.5"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>
      {menu}
    </div>
  );
}
