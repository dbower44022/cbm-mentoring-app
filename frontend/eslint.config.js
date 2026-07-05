import js from "@eslint/js";
import tseslint from "typescript-eslint";

// Zero-warnings gate (npm run check passes --max-warnings 0), mirroring the
// ruff gate on the Python side. schema.d.ts is generated (npm run gen:api) —
// machine output is regenerated, never linted or hand-edited.
export default tseslint.config(
  { ignores: ["dist/", "node_modules/", "src/api/schema.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    // Config files run under Node before the TS project exists; keep them
    // syntax-linted only.
    files: ["eslint.config.js"],
    extends: [tseslint.configs.disableTypeChecked],
  },
);
