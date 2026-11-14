export type DownloadProgress = { url: string; written: number; total: number };
export type NotificationOpen = { victimId: string | null; action: 'open' | 'alert' | 'accept' | 'map' };
export type NetworkChange = { bound: boolean };

export type SarNativeModuleEvents = {
  onDownloadProgress: (e: DownloadProgress) => void;
  onNotificationOpen: (e: NotificationOpen) => void;
  onNetworkChange: (e: NetworkChange) => void;
};
