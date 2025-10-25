module.exports = [
  // Frontend configuration - for browser-based JavaScript
  {
    files: ["webhook_server/web/static/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        // Browser environment globals
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        WebSocket: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        alert: "readonly",
        confirm: "readonly",
        prompt: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        URLSearchParams: "readonly",
        AbortController: "readonly",
      },
    },
    rules: {
      "no-unused-vars": "warn",
      "no-undef": "error",
      "no-console": "off",
    },
  },
];
