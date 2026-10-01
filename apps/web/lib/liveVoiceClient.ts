/**
 * One live voice call with the assistant over our own WebSocket.
 *
 * Browser -> server: 16 kHz PCM16 microphone audio (binary) and small JSON
 * messages (typed text, the confirm button, a keep-alive ping, hang up). Server ->
 * browser: the assistant's 24 kHz PCM16 voice (binary) and JSON events. Provider keys
 * never reach the browser: the server talks to the voice model.
 *
 * The call holds on through rough moments: if the connection to our server drops it
 * reconnects by itself (the microphone and the voice player keep going, and what the
 * guest said meanwhile is sent once it is back); if the voice model's own session drops,
 * the server opens a new one and says "reconnecting"/"resumed". A stalled microphone is
 * woken up again, and a phone that paused its audio asks for a tap to resume.
 */
import { type MicHandle, PcmPlayer, createAudioContext, startMicrophone } from "./voiceAudio";

export type LiveCallState = "connecting" | "listening" | "speaking" | "thinking" | "reconnecting" | "ended" | "failed";

export interface LiveOrderAction {
  type: "AWAITING_ORDER_CONFIRMATION" | "ORDER_SUBMITTED" | string;
  order_number?: string;
  draft_version?: number;
  summary?: {
    items?: Array<{ name_ar: string; name_en?: string; quantity: number; line_total_display_ar: string; line_total_display_en?: string }>;
    item_count?: number;
    total_display_ar?: string;
    total_display_en?: string;
  };
}

export interface LiveCallEvents {
  onState?: (state: LiveCallState) => void;
  onTranscript?: (role: "user" | "assistant", text: string) => void;
  onTurnComplete?: () => void;
  onTool?: (name: string) => void;
  onAction?: (action: LiveOrderAction) => void;
  onDraft?: (draft: { item_count?: number; total_display_ar?: string } | null) => void;
  onConfirmFailed?: (errorCode: string | null) => void;
  onLevel?: (level: number) => void;
  /** Something happened on the call (speech heard or said): it is not idle. */
  onActivity?: () => void;
  /** The phone paused its audio and only a tap can resume it (see resumeAudio). */
  onAudioPaused?: (paused: boolean) => void;
  /** The call could not run or stopped with an error; `code` explains why. */
  onError?: (code: string) => void;
  /** The server ended the call on purpose (e.g. the time limit); `reason` says why. */
  onEnded?: (reason: string) => void;
}

/** Close codes sent by the server (see interfaces/websocket/routes.py). */
export const CLOSE_VOICE_UNAVAILABLE = 4409;
export const CLOSE_VOICE_RATE_LIMITED = 4429;
const CLOSE_POLICY = 1008; // no visit behind the call (it ended)

/** Waits before each new connection after the line dropped (then the call gives up). */
const RECONNECT_DELAYS_MS = [400, 1200, 2500, 5000];
const PING_EVERY_MS = 5000;
const SILENT_LINE_MS = 13000;       // no answer to pings for this long: the line is dead
const MIC_STALL_MS = 2500;          // no audio from the microphone for this long: wake it up
const MIC_KEEP_WHILE_AWAY_BYTES = 16000 * 2 * 2;   // the last 2 s of speech while reconnecting
const SPEECH_LEVEL = 0.06;

export class LiveVoiceCall {
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private mic: MicHandle | null = null;
  private player: PcmPlayer;
  private state: LiveCallState = "connecting";
  private finished = false;
  private everReady = false;
  private ready = false;
  private muted = false;
  private attempts = 0;
  private lastPong = 0;
  private lastError: string | null = null;
  private endReason: string | null = null;
  private away: ArrayBuffer[] = [];
  private awayBytes = 0;
  private audioPaused = false;
  // The waiter's greeting at the start of the call: the phone's echo canceller is still settling,
  // so the microphone would hear the speaker. Silence goes up instead, just then. (Not after a
  // reconnect: the guest is usually mid-conversation then, and their words must get through.)
  private echoGuard = false;
  private timers: number[] = [];
  private reconnectTimer = 0;

  constructor(private readonly url: string, private readonly events: LiveCallEvents = {}) {
    this.player = this.makePlayer(null);
  }

  private makePlayer(context: AudioContext | null): PcmPlayer {
    return new PcmPlayer(24000, () => {
      this.echoGuard = false; // the first sentence is over: full two-way talk from here
      if (this.state === "speaking") this.setState("listening");
    }, context);
  }

  private setState(state: LiveCallState) {
    if (this.state === state) return;
    this.state = state;
    this.events.onState?.(state);
  }

  private fail(code: string) {
    if (this.finished) return;
    this.finished = true;
    this.events.onError?.(code);
    this.setState("failed");
    this.release();
  }

  private end(reason: string) {
    if (this.finished) return;
    this.finished = true;
    this.events.onEnded?.(reason);
    this.setState("ended");
    this.release();
  }

  /** Must be called from a user gesture (tap), so the browser allows the microphone and audio. */
  async start(): Promise<void> {
    this.setState("connecting");
    this.context = createAudioContext();
    if (this.context) {
      // One context for the whole call: the voice and the microphone share it.
      this.player = this.makePlayer(this.context);
      this.context.onstatechange = () => this.checkAudio();
    }
    await this.player.unlock();
    try {
      await this.openMicrophone();
    } catch {
      this.fail("MICROPHONE_BLOCKED");
      return;
    }
    if (this.finished) return;
    this.connect();
    // Health checks: the line (pings), the microphone and the phone's audio.
    this.timers.push(window.setInterval(() => this.ping(), PING_EVERY_MS));
    this.timers.push(window.setInterval(() => this.checkMicrophone(), 1000));
  }

  private async openMicrophone(): Promise<void> {
    this.mic?.stop();
    this.mic = await startMicrophone({
      context: this.context ?? undefined,
      onChunk: (pcm) => this.sendAudio(pcm),
      onLevel: (level) => {
        this.events.onLevel?.(level);
        if (level > SPEECH_LEVEL && !this.muted) this.events.onActivity?.();
      },
      onEnded: () => void this.checkMicrophone(),
    });
    this.mic.setMuted(this.muted);
  }

  private sendAudio(pcm: ArrayBuffer) {
    if (this.ready && this.socket?.readyState === WebSocket.OPEN) {
      // Same length, so the voice model's clock keeps running while its own voice is not heard back.
      this.socket.send(this.echoGuard && this.player.playing ? new ArrayBuffer(pcm.byteLength) : pcm);
      return;
    }
    // Between connections: keep the last moments, sent as soon as the line is back.
    if (!this.everReady) return;
    this.away.push(pcm);
    this.awayBytes += pcm.byteLength;
    while (this.awayBytes > MIC_KEEP_WHILE_AWAY_BYTES && this.away.length > 1) {
      this.awayBytes -= this.away.shift()!.byteLength;
    }
  }

  private connect() {
    if (this.finished) return;
    this.ready = false;
    this.lastError = null;
    this.endReason = null;
    // Coming back to a call whose line dropped: the waiter says it is back instead of greeting.
    const socket = new WebSocket(this.everReady ? `${this.url}${this.url.includes("?") ? "&" : "?"}resume=1` : this.url);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.lastPong = performance.now();

    socket.onmessage = (event: MessageEvent) => {
      if (this.socket !== socket) return;
      this.lastPong = performance.now(); // anything from the server shows the line is alive
      if (event.data instanceof ArrayBuffer) {
        this.player.play(event.data);
        this.setState("speaking");
        this.events.onActivity?.();
        return;
      }
      let message: Record<string, unknown>;
      try {
        message = JSON.parse(String(event.data));
      } catch {
        return;
      }
      this.handle(message);
    };
    socket.onclose = (event: CloseEvent) => {
      if (this.socket !== socket || this.finished) return;
      this.socket = null;
      this.ready = false;
      if (this.endReason) return this.end(this.endReason);
      if (event.code === CLOSE_VOICE_RATE_LIMITED) return this.fail("RATE_LIMITED");
      if (event.code === CLOSE_POLICY) return this.fail("SESSION_ENDED");
      if (!this.everReady) {
        // It never started: live voice is not available now (the call can go on step by step).
        return this.fail(event.code === CLOSE_VOICE_UNAVAILABLE ? (this.lastError ?? "LIVE_VOICE_UNAVAILABLE") : "CONNECTION_LOST");
      }
      this.scheduleReconnect();
    };
    socket.onerror = () => {
      /* onclose follows with the reason */
    };
  }

  private scheduleReconnect() {
    if (this.finished) return;
    if (this.attempts >= RECONNECT_DELAYS_MS.length) {
      this.fail("CONNECTION_LOST");
      return;
    }
    this.setState("reconnecting");
    this.player.stop();
    const delay = RECONNECT_DELAYS_MS[this.attempts];
    this.attempts += 1;
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
  }

  private handle(message: Record<string, unknown>) {
    switch (message.type) {
      case "ready":
      case "resumed": {
        const first = !this.everReady;
        this.ready = true;
        this.everReady = true;
        this.attempts = 0;
        if (first) {
          this.echoGuard = true;
          window.setTimeout(() => {
            this.echoGuard = false;
          }, 8000);
        }
        if (message.type === "ready" && first) this.events.onDraft?.((message.draft as never) ?? null);
        // What the guest said while the line was down goes first.
        for (const chunk of this.away) this.socket?.send(chunk);
        this.away = [];
        this.awayBytes = 0;
        this.setState("listening");
        break;
      }
      case "reconnecting":
        // The server is opening a new session with the voice model; our line is fine.
        this.player.stop();
        this.setState("reconnecting");
        break;
      case "pong":
        break;
      case "transcript":
        this.events.onTranscript?.(message.role === "user" ? "user" : "assistant", String(message.text ?? ""));
        this.events.onActivity?.();
        if (message.role === "user" && this.state === "listening") this.setState("thinking");
        break;
      case "tool":
        this.events.onTool?.(String(message.name ?? ""));
        this.setState("thinking");
        break;
      case "action":
        this.events.onAction?.(message.action as LiveOrderAction);
        break;
      case "draft":
        this.events.onDraft?.((message.draft as never) ?? null);
        break;
      case "confirm_failed":
        this.events.onConfirmFailed?.((message.error_code as string) ?? null);
        break;
      case "interrupted":
        this.player.stop(); // the guest talked over the assistant
        this.setState("listening");
        break;
      case "turn_complete":
        this.events.onTurnComplete?.();
        if (!this.player.playing) this.setState("listening");
        break;
      case "ending":
        // The call ends on purpose (the server says why); the socket closes right after.
        this.endReason = String(message.reason ?? "ENDED");
        break;
      case "error":
        this.lastError = String(message.code ?? "LIVE_VOICE_FAILED");
        break;
    }
  }

  /** Keep-alive: a line that stays silent too long is dead even if the browser has not noticed. */
  private ping() {
    const socket = this.socket;
    if (this.finished || !socket || socket.readyState !== WebSocket.OPEN) return;
    if (performance.now() - this.lastPong > SILENT_LINE_MS) {
      this.socket = null;
      this.ready = false;
      try {
        socket.close(4000);
      } catch {
        // already closing
      }
      this.scheduleReconnect();
      return;
    }
    socket.send(JSON.stringify({ type: "ping" }));
  }

  /** The microphone stopped sending (the system paused it) or was taken away: start it again. */
  private async checkMicrophone() {
    if (this.finished || !this.mic) return;
    if (!this.mic.live) {
      try {
        await this.openMicrophone();
      } catch {
        this.fail("MICROPHONE_BLOCKED");
      }
      return;
    }
    if (performance.now() - this.mic.lastChunkAt > MIC_STALL_MS) this.checkAudio();
  }

  /** The phone's audio can be paused by the system (a notification, a call): resume it, or ask for a tap. */
  private checkAudio() {
    const context = this.context;
    if (this.finished || !context) return;
    const state = context.state as string;
    if (state === "running") {
      this.setAudioPaused(false);
      return;
    }
    if (state === "closed") return;
    context.resume().then(
      () => this.setAudioPaused((context.state as string) !== "running"),
      () => this.setAudioPaused(true),
    );
  }

  private setAudioPaused(paused: boolean) {
    if (this.audioPaused === paused) return;
    this.audioPaused = paused;
    this.events.onAudioPaused?.(paused);
  }

  /** From a tap: resume the phone's audio (and the microphone, if it went away). */
  async resumeAudio(): Promise<void> {
    if (this.finished) return;
    await this.context?.resume().catch(() => undefined);
    if (this.mic && !this.mic.live) await this.openMicrophone().catch(() => undefined);
    this.checkAudio();
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.mic?.setMuted(muted);
  }

  sendText(text: string): void {
    if (text.trim() && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "text", text: text.trim() }));
      this.setState("thinking");
    }
  }

  /** The on-screen "confirm order" button: the server submits the reviewed summary. */
  confirmOrder(): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN || !this.ready) return false;
    this.socket.send(JSON.stringify({ type: "confirm_order" }));
    return true;
  }

  /** The guest (or the page) ends the call; `reason` is kept in the server's notes. */
  hangUp(reason = "hang_up"): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "end", reason }));
    }
    if (!this.finished) {
      this.finished = true;
      this.setState("ended");
    }
    this.release();
  }

  private release(): void {
    for (const timer of this.timers) window.clearInterval(timer);
    this.timers = [];
    window.clearTimeout(this.reconnectTimer);
    this.mic?.stop();
    this.mic = null;
    this.player.close();
    if (this.context) {
      this.context.onstatechange = null;
      void this.context.close().catch(() => undefined);
      this.context = null;
    }
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState <= WebSocket.OPEN) {
      try {
        socket.close(1000);
      } catch {
        // already closed
      }
    }
  }
}
