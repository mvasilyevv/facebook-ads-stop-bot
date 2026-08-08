import * as a11yAddonAnnotations from "@storybook/addon-a11y/preview";
import { setProjectAnnotations } from "@storybook/react-vite";

import * as projectAnnotations from "./preview";

// Storybook 10.5 can provision preview annotations automatically, but its
// generated setup currently fails against aria-query's CommonJS exports in
// this self-hosted Vitest browser environment. Keep the documented manual
// setup so project rendering and release-gating a11y annotations are composed
// deterministically.
setProjectAnnotations([a11yAddonAnnotations, projectAnnotations]);
