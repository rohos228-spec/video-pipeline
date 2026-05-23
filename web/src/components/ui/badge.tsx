import { cn } from "@/lib/utils";

type Variant = "default" | "success" | "warning" | "destructive" | "info" | "muted";

const variantClasses: Record<Variant, string> = {
  default: "bg-primary/15 text-primary border-primary/30",
  success: "bg-success/15 text-success border-success/30",
  warning: "bg-warning/15 text-warning border-warning/30",
  destructive: "bg-destructive/15 text-destructive border-destructive/30",
  info: "bg-info/15 text-info border-info/30",
  muted: "bg-muted text-muted-foreground border-border",
};

export function Badge({
  className,
  variant = "default",
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium uppercase tracking-wider",
        variantClasses[variant],
        className
      )}
      {...props}
    >
      {children}
    </span>
  );
}
