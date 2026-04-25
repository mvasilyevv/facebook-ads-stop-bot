type ToggleLikeHandle = {
  getAttribute(name: string): Promise<string | null>;
  $(selector: string): Promise<ToggleLikeHandle | null>;
};

export const TOGGLE_SELECTOR = '[role="switch"]';

export async function resolveToggleHandleFromCell<T extends ToggleLikeHandle>(
  cell: T | null,
): Promise<T | null> {
  if (!cell) {
    return null;
  }

  if ((await cell.getAttribute('role')) === 'switch') {
    return cell;
  }

  return cell.$(TOGGLE_SELECTOR) as Promise<T | null>;
}
