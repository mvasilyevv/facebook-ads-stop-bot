// Минимальный спиннер загрузки — используется как fallback для React.lazy()
export default function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center min-h-[300px]">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-accent" />
    </div>
  );
}
