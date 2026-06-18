/**
 * Barrel-экспорт UI-kit примитивов.
 * Импортируй: import { Button, Badge, Drawer } from "@/components/ui";
 */

export { cn } from "@/lib/utils/cn";

export { Button } from "./Button";
export type { ButtonProps } from "./Button";

export { Badge } from "./Badge";
export type { BadgeVariant, BadgeProps } from "./Badge";

export { FilterPill, Chip, Pill } from "./Pill";

export { Kbd } from "./Kbd";

export { Input, SearchInput } from "./Input";
export type { InputProps } from "./Input";

export { TagListInput } from "./TagListInput";
export type { TagListInputProps } from "./TagListInput";

export { Select } from "./Select";
export type { SelectOption } from "./Select";

export { Switch } from "./Switch";

export { Checkbox } from "./Checkbox";
export type { CheckboxState } from "./Checkbox";

export { Tabs, TabsList, TabsContent } from "./Tabs";
export type { TabItem } from "./Tabs";

export { Tooltip, TooltipProvider } from "./Tooltip";

export { Skeleton, SkeletonRows } from "./Skeleton";

export { EmptyState } from "./EmptyState";

export { ErrorState } from "./ErrorState";

export { Card } from "./Card";

export { Spinner, ProgressBar } from "./Spinner";

export { Modal, ModalFooter } from "./Modal";

export { Drawer } from "./Drawer";

export { ConfirmDialog } from "./ConfirmDialog";

export { ToastViewport, toast } from "./Toast";
export type { ToastVariant } from "./Toast";
