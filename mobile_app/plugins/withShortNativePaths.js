/**
 * Config plugin: keep CMake's object-file paths short on Windows.
 *
 * React Native's New Architecture build compiles each library's codegen C++ with CMake,
 * and CMake mirrors the *absolute* source path inside the build folder. With the project
 * under C:\Users\...\Documents\GitHub\... an object path reaches ~375 characters, past
 * Windows' 260-character MAX_PATH, and ninja fails ("Filename longer than 260 characters").
 *
 * This moves the CMake staging folder to <project>/.c (shorter than android/app/.cxx). Long paths
 * must also be enabled in Windows and ninja must be >= 1.12 (tools/install_toolchain.sh).
 */
const { withAppBuildGradle } = require('expo/config-plugins');

const MARKER = '// sar: short CMake staging dir';

module.exports = function withShortNativePaths(config) {
  return withAppBuildGradle(config, (cfg) => {
    if (!cfg.modResults.contents.includes(MARKER)) {
      cfg.modResults.contents = cfg.modResults.contents.replace(
        /^android\s*\{/m,
        `android {\n    ${MARKER} (plugins/withShortNativePaths.js)\n` +
          '    externalNativeBuild { cmake { buildStagingDirectory = rootProject.file("../.c") } }\n',
      );
    }
    return cfg;
  });
};
