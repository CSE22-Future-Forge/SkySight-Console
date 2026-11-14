/**
 * Config plugin: copy assets/glyphs into the APK's assets so the offline style can use
 * "glyphs": "asset://glyphs/{fontstack}/{range}.pbf". Labels then never depend on the
 * laptop or the network (ARCHITECTURE.md section 8: missing glyphs = no labels).
 *
 * Only the Latin / punctuation ranges are bundled (~2 MB); MapLibre cannot shape
 * Sinhala/Tamil script, so the style prefers English names anyway.
 */
const fs = require('fs');
const path = require('path');
const { withDangerousMod } = require('expo/config-plugins');

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const from = path.join(src, entry.name);
    const to = path.join(dest, entry.name);
    if (entry.isDirectory()) copyDir(from, to);
    else fs.copyFileSync(from, to);
  }
}

module.exports = function withMapGlyphs(config) {
  return withDangerousMod(config, [
    'android',
    async (cfg) => {
      const src = path.join(cfg.modRequest.projectRoot, 'assets', 'glyphs');
      const dest = path.join(cfg.modRequest.platformProjectRoot, 'app', 'src', 'main', 'assets', 'glyphs');
      copyDir(src, dest);
      return cfg;
    },
  ]);
};
