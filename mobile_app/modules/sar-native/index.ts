// Re-export the native module. On web, it will be resolved to SarNativeModule.web.ts
// and on native platforms to SarNativeModule.ts
export { default } from './src/SarNativeModule';
export * from './src/SarNative.types';
