// The project uses CRA's bundled Jest runtime.  Keep these declarations local
// until @types/jest is explicitly added to the approved dependency set.
declare const describe: (name: string, body: () => void) => void;
declare const test: (name: string, body: () => void | Promise<void>) => void;
declare const beforeEach: (body: () => void | Promise<void>) => void;
declare const afterEach: (body: () => void | Promise<void>) => void;
declare const expect: (value: unknown) => any;
declare const jest: {
  fn: () => any;
  mock: (moduleName: string, factory: () => unknown, options?: { virtual?: boolean }) => void;
};
