import { NativeModule, requireNativeModule } from 'expo';

import { NotificationOpen, SarNativeModuleEvents } from './SarNative.types';

declare class SarNativeModule extends NativeModule<SarNativeModuleEvents> {
  /** Bind the process to Wi-Fi even though it has no internet. Resolves false on timeout. */
  bindToWifi(timeoutMs: number): Promise<boolean>;
  unbindNetwork(): void;
  isBoundToWifi(): boolean;

  sha256File(path: string): Promise<string>;
  /** Resumable (HTTP Range) download; progress via onDownloadProgress. Returns final size. */
  downloadFile(url: string, path: string, headers: Record<string, string>): Promise<number>;

  startService(title: string, text: string): void;
  updateService(title: string, text: string): void;
  stopService(): void;
  isServiceRunning(): boolean;

  showAlarm(victimId: string, title: string, body: string, imagePath: string | null, fullScreen: boolean): void;
  showInfo(id: string, title: string, body: string, victimId: string | null): void;
  cancelNotification(id: string): void;

  canUseFullScreenIntent(): boolean;
  openFullScreenIntentSettings(): void;
  isIgnoringBatteryOptimizations(): boolean;
  requestIgnoreBatteryOptimizations(): void;
  setShowWhenLocked(on: boolean): void;
  takeLaunchAction(): NotificationOpen | null;
}

export default requireNativeModule<SarNativeModule>('SarNative');
