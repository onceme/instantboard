import { SSEConnectionState, SSEEventType } from "@/types";

export { SSEConnectionState };

type SSEEventHandler = (data: unknown) => void;

interface SSEConnectionOptions {
  category: string;
  token?: string;
  onStateChange?: (state: SSEConnectionState) => void;
  eventHandlers?: Partial<Record<SSEEventType, SSEEventHandler>>;
}

const SSE_BASE_URL = "/api/v1/stream";
const MAX_BACKOFF = 60_000;
const INITIAL_BACKOFF = 1_000;

export class SSEConnection {
  private eventSource: EventSource | null = null;
  private category: string;
  private token: string;
  private state: SSEConnectionState = SSEConnectionState.DISCONNECTED;
  private backoff = INITIAL_BACKOFF;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private eventHandlers: Map<string, SSEEventHandler> = new Map();
  private onStateChange: ((state: SSEConnectionState) => void) | null = null;
  private lastEventId: string = "";
  private intentionallyClosed = false;

  constructor(options: SSEConnectionOptions) {
    this.category = options.category;
    this.token = options.token || localStorage.getItem("access_token") || "";
    this.onStateChange = options.onStateChange || null;

    if (options.eventHandlers) {
      for (const [eventType, handler] of Object.entries(
        options.eventHandlers,
      )) {
        if (handler) {
          this.eventHandlers.set(eventType, handler);
        }
      }
    }
  }

  connect(): void {
    if (
      this.state === SSEConnectionState.CONNECTED ||
      this.state === SSEConnectionState.CONNECTING
    ) {
      return;
    }

    // Re-read the token on every (re)connect: boards stay open longer than the JWT
    // lifetime and the axios interceptor refreshes localStorage in the meantime.
    // Without this, reconnects keep presenting the expired token forever and the
    // connection flaps between "reconnecting" and 401 without ever recovering.
    const latestToken = localStorage.getItem("access_token");
    if (latestToken) {
      this.token = latestToken;
    }

    this.intentionallyClosed = false;
    this.setState(SSEConnectionState.CONNECTING);

    const url = `${SSE_BASE_URL}/${this.category}?token=${encodeURIComponent(this.token)}`;
    this.eventSource = new EventSource(url);

    this.eventSource.onopen = () => {
      this.setState(SSEConnectionState.CONNECTED);
      this.backoff = INITIAL_BACKOFF;
    };

    this.eventSource.onerror = () => {
      this.eventSource?.close();
      this.eventSource = null;

      if (this.intentionallyClosed) {
        this.setState(SSEConnectionState.DISCONNECTED);
        return;
      }

      this.setState(SSEConnectionState.RECONNECTING);
      this.scheduleReconnect();
    };

    const eventTypes: SSEEventType[] = [
      SSEEventType.HEARTBEAT,
      SSEEventType.QUOTE_UPDATE,
      SSEEventType.MARKET_INDEX_UPDATE,
      SSEEventType.COMMODITY_UPDATE,
      SSEEventType.NAV_ESTIMATE_UPDATE,
      SSEEventType.ALERT_UPDATE,
      SSEEventType.ITEM_UPDATE,
      SSEEventType.TOPIC_STATS_UPDATE,
      SSEEventType.SOURCE_HEALTH_UPDATE,
      SSEEventType.SYSTEM_METRIC_UPDATE,
      SSEEventType.DB_METRIC_UPDATE,
      SSEEventType.BUSINESS_METRIC_UPDATE,
    ];

    for (const eventType of eventTypes) {
      this.eventSource.addEventListener(eventType, (e: MessageEvent) => {
        if (e.lastEventId) {
          this.lastEventId = e.lastEventId;
        }
        const handler = this.eventHandlers.get(eventType);
        if (handler) {
          try {
            const data = JSON.parse(e.data);
            handler(data);
          } catch {
            handler(e.data);
          }
        }
      });
    }
  }

  disconnect(): void {
    this.intentionallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.setState(SSEConnectionState.DISCONNECTED);
  }

  addEventHandler(eventType: string, handler: SSEEventHandler): void {
    this.eventHandlers.set(eventType, handler);
  }

  removeEventHandler(eventType: string): void {
    this.eventHandlers.delete(eventType);
  }

  getState(): SSEConnectionState {
    return this.state;
  }

  updateToken(token: string): void {
    this.token = token;
    if (
      this.state === SSEConnectionState.CONNECTED ||
      this.state === SSEConnectionState.CONNECTING
    ) {
      this.disconnect();
      this.connect();
    }
  }

  private setState(state: SSEConnectionState): void {
    this.state = state;
    this.onStateChange?.(state);
  }

  private scheduleReconnect(): void {
    if (this.intentionallyClosed) return;

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, this.backoff);

    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF);
  }
}

export function createSSEConnection(
  options: SSEConnectionOptions,
): SSEConnection {
  return new SSEConnection(options);
}
