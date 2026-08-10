import type { Preview } from "@storybook/react";
import "../src/styles/app.css";

const preview: Preview = {
  parameters: {
    a11y: {
      // Storybook/Vitest is a release gate: any axe violation fails the run.
      test: "error",
    },
    backgrounds: {
      default: "dark",
      values: [{ name: "dark", value: "#0A0A0B" }],
    },
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
  },
};

export default preview;
