import Link from "next/link";
import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";
import { cx } from "@/lib/cx";
import styles from "./Button.module.css";

type Variant = "primary" | "secondary" | "ghost";

type Shared = {
  variant?: Variant;
  size?: "medium" | "small";
  block?: boolean;
  children: ReactNode;
};

function classesFor(
  variant: Variant = "primary",
  size: Shared["size"] = "medium",
  block?: boolean,
): string {
  return cx(
    styles.button,
    styles[variant],
    size === "small" && styles.small,
    block && styles.block,
  );
}

export type ButtonProps = Shared & ButtonHTMLAttributes<HTMLButtonElement>;

export function Button({
  variant,
  size,
  block,
  className,
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(classesFor(variant, size, block), className)}
      {...rest}
    >
      {children}
    </button>
  );
}

export type ButtonLinkProps<RouteType> = Shared &
  ComponentProps<typeof Link<RouteType>>;

/**
 * A link that looks like a button. Kept separate so navigation stays an
 * anchor: it must be middle-clickable and announced as a link.
 *
 * Generic over the route type so Next's typed routes still check the href —
 * a non-generic wrapper collapses it to `unknown` and rejects every dynamic
 * route.
 */
export function ButtonLink<RouteType>({
  variant,
  size,
  block,
  className,
  children,
  ...rest
}: ButtonLinkProps<RouteType>) {
  return (
    <Link
      className={cx(classesFor(variant, size, block), className)}
      {...rest}
    >
      {children}
    </Link>
  );
}
