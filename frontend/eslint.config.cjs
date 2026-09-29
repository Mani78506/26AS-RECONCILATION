const js = require("@eslint/js");
const globals = require("globals");
const react = require("eslint-plugin-react");
const reactHooks = require("eslint-plugin-react-hooks");

module.exports = [
  { ignores: ["build/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["src/pages/phase5/**/*.{ts,tsx}", "src/pages/TdsCompliancePage.tsx"],
    languageOptions: {
      parser: require(require.resolve("@typescript-eslint/parser", { paths: [require.resolve("react-scripts/package.json")] })),
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.jest },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react, "react-hooks": reactHooks },
    settings: { react: { version: "detect" } },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "off",
      // TypeScript resolves type names and interface parameters.
      "no-undef": "off",
      "no-unused-vars": "off",
    },
  },
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react, "react-hooks": reactHooks },
    settings: { react: { version: "detect" } },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "off",
    },
  },
];
