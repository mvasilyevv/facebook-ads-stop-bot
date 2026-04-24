type ToggleLikeHandle = {
  getAttribute(name: string): Promise<string | null>;
  $(selector: string): Promise<ToggleLikeHandle | null>;
};

export async function resolveToggleHandleFromCell<T extends ToggleLikeHandle>(
  cell: T | null,
): Promise<T | null> {
  if (!cell) {
    return null;
  }

  if ((await cell.getAttribute('role')) === 'switch') {
    return cell;
  }

  return cell.$('[role="switch"]') as Promise<T | null>;
}
