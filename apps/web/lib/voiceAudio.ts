/**
 * Browser audio helpers for the assistant's voice features.
 *
 * - createAudioContext: one audio context per call, shared by the microphone and the player.
 * - startMicrophone: live 16 kHz PCM16 stream (for live voice calls).
 * - PcmPlayer: plays the assistant's 24 kHz PCM16 audio smoothly despite the network.
 * - recordUtterance: records one spoken turn and stops by itself after a pause.
 * - startRecording: a plain start/stop recording (dictation).
 *
 * Nothing here talks to the server; callers decide where audio goes.
 */

export const MIC_WORKLET_URL = "/audio/mic-capture-worklet.js";

type AudioContextClass = typeof AudioContext;

function audioContextClass(): AudioContextClass | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { AudioContext?: AudioContextClass; webkitAudioContext?: AudioContextClass };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

/**
 * One audio context for a whole call (microphone and voice together): phones handle a single
 * context far better than several, and the echo canceller hears exactly what is played.
 * Create it from the guest's tap, so the browser lets it make sound.
 */
export function createAudioContext(): AudioContext | null {
  const Ctx = audioContextClass();
  return Ctx ? new Ctx({ latencyHint: "interactive" }) : null;
}

/** True when this browser can record from the microphone at all (needs HTTPS or localhost). */
export function canUseMicrophone(): boolean {
  return typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia) && audioContextClass() !== null;
}

/** True when live streaming capture (AudioWorklet) is available. */
export function canStreamMicrophone(): boolean {
  const Ctx = audioContextClass();
  return canUseMicrophone() && Boolean(Ctx && "audioWorklet" in Ctx.prototype);
}

/** True when recorded clips can be produced (MediaRecorder). */
export function canRecordClips(): boolean {
  return canUseMicrophone() && typeof MediaRecorder !== "undefined";
}

async function openMicrophone(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
}

export interface MicHandle {
  stop(): void;
  setMuted(muted: boolean): void;
  /** When the last piece of audio arrived (performance.now()); a stalled microphone stops sending. */
  readonly lastChunkAt: number;
  /** False once the system took the microphone away (another app, a phone call). */
  readonly live: boolean;
}

/**
 * Stream the microphone as 16 kHz 16-bit mono PCM chunks of ~50 ms. With `context` (the call's
 * shared one) it is left open on stop; otherwise the microphone's own context is closed.
 */
export async function startMicrophone(options: {
  onChunk: (pcm16: ArrayBuffer) => void;
  onLevel?: (level: number) => void;
  /** The system took the microphone away (the call can try to start it again). */
  onEnded?: () => void;
  context?: AudioContext;
}): Promise<MicHandle> {
  const context = options.context ?? createAudioContext();
  if (!context) throw new Error("AUDIO_UNSUPPORTED");
  const ownsContext = !options.context;
  const stream = await openMicrophone();
  let lastChunkAt = performance.now();
  let live = true;
  const [track] = stream.getAudioTracks();
  const onTrackEnded = () => {
    live = false;
    options.onEnded?.();
  };
  track?.addEventListener("ended", onTrackEnded);
  try {
    await context.audioWorklet.addModule(MIC_WORKLET_URL);
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, "mic-capture", { numberOfInputs: 1, numberOfOutputs: 0 });
    node.port.onmessage = (event: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
      lastChunkAt = performance.now();
      options.onChunk(event.data.pcm);
      options.onLevel?.(event.data.level);
    };
    source.connect(node);
    if (context.state === "suspended") await context.resume();
    return {
      stop() {
        node.port.onmessage = null;
        track?.removeEventListener("ended", onTrackEnded);
        source.disconnect();
        node.disconnect();
        stream.getTracks().forEach((t) => t.stop());
        if (ownsContext) void context.close();
      },
      setMuted(muted: boolean) {
        node.port.postMessage({ muted });
      },
      get lastChunkAt() {
        return lastChunkAt;
      },
      get live() {
        return live && track?.readyState !== "ended";
      },
    };
  } catch (error) {
    track?.removeEventListener("ended", onTrackEnded);
    stream.getTracks().forEach((t) => t.stop());
    if (ownsContext) void context.close();
    throw error;
  }
}

/** How the player keeps the voice smooth: a small cushion of audio ahead of what is heard. */
const CUSHION_START_S = 0.25;   // before the first word (and after a pause)
const CUSHION_MIN_S = 0.16;
const CUSHION_MAX_S = 0.6;
const CUSHION_GROW_S = 0.08;    // after a moment the network ran behind
const CUSHION_SHRINK_S = 0.02;  // after a calm stretch
const CALM_STRETCH_S = 12;
const SAME_UTTERANCE_S = 0.8;   // audio this soon after the last is the same sentence (a gap in it is a stall)
const FADE_S = 0.006;           // soft edges where the voice starts again, so there is no click

/**
 * Plays 16-bit mono PCM chunks back to back. The assistant's voice arrives in real time over
 * the network, a little early or late each time; a small cushion (a quarter of a second, more
 * if the network is uneven, less when it is steady) turns that into continuous speech instead
 * of choppy pieces. `stop()` cuts off speech immediately.
 */
export class PcmPlayer {
  private context: AudioContext | null;
  private readonly ownsContext: boolean;
  private output: GainNode | null = null;
  private nextTime = 0;
  private lastSource: AudioBufferSourceNode | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private cushion = CUSHION_START_S;
  private calmSince = 0;
  private onIdle?: () => void;
  /** How often the network ran behind the voice (for the call's notes). */
  underruns = 0;

  constructor(private readonly sampleRate = 24000, onIdle?: () => void, context?: AudioContext | null) {
    this.onIdle = onIdle;
    this.context = context ?? null;
    this.ownsContext = !context;
  }

  /** Call from a user gesture (e.g. the "start call" tap) so playback is allowed. */
  async unlock(): Promise<void> {
    if (!this.context) this.context = createAudioContext();
    if (this.context && this.context.state === "suspended") await this.context.resume().catch(() => undefined);
  }

  /** Audio is still scheduled to be heard. */
  get playing(): boolean {
    return Boolean(this.context && this.nextTime > this.context.currentTime + 0.01);
  }

  private destination(context: AudioContext): AudioNode {
    if (!this.output) {
      this.output = context.createGain();
      this.output.connect(context.destination);
    }
    return this.output;
  }

  play(pcm16: ArrayBuffer): void {
    const context = this.context;
    if (!context || pcm16.byteLength < 2) return;
    const samples = new Int16Array(pcm16, 0, Math.floor(pcm16.byteLength / 2));
    const buffer = context.createBuffer(1, samples.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 0x8000;

    const now = context.currentTime;
    if (this.nextTime < now + 0.02) {
      // Nothing left to play: the voice starts (again). A gap inside the same sentence means the
      // network ran behind, so the cushion grows; after a calm stretch it shrinks back.
      const sameUtterance = this.nextTime > 0 && now - this.nextTime < SAME_UTTERANCE_S;
      if (sameUtterance) {
        this.underruns += 1;
        this.cushion = Math.min(CUSHION_MAX_S, this.cushion + CUSHION_GROW_S);
        this.calmSince = now;
      } else if (this.calmSince && now - this.calmSince > CALM_STRETCH_S) {
        this.cushion = Math.max(CUSHION_MIN_S, this.cushion - CUSHION_SHRINK_S);
        this.calmSince = now;
      }
      if (!this.calmSince) this.calmSince = now;
      this.nextTime = now + this.cushion;
      const fade = Math.min(channel.length, Math.round(FADE_S * this.sampleRate));
      for (let i = 0; i < fade; i += 1) channel[i] *= i / fade;
    }

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.destination(context));
    source.start(this.nextTime);
    this.nextTime += buffer.duration;
    this.sources.add(source);
    this.lastSource = source;
    source.onended = () => {
      this.sources.delete(source);
      if (source === this.lastSource) {
        this.lastSource = null;
        this.onIdle?.();
      }
    };
  }

  /** The guest started talking over the assistant: stop everything queued. */
  stop(): void {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // already finished
      }
    }
    this.sources.clear();
    this.lastSource = null;
    this.nextTime = 0;
  }

  close(): void {
    this.stop();
    this.output?.disconnect();
    this.output = null;
    if (this.ownsContext) void this.context?.close();
    this.context = null;
  }
}

function pickRecordingType(): string | undefined {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return candidates.find((type) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.(type));
}

export interface Recording {
  /** Stop and get the recorded audio (null if nothing was captured). */
  stop(): Promise<Blob | null>;
  /** Stop and throw the audio away. */
  cancel(): void;
}

/**
 * Start a manual recording (the dictation button): stop() returns the clip.
 * Pass `stream` to record from a microphone stream the caller already owns.
 */
export async function startRecording(maxMs = 60000, stream?: MediaStream): Promise<Recording> {
  const ownsStream = !stream;
  const input = stream ?? (await openMicrophone());
  const mimeType = pickRecordingType();
  const recorder = new MediaRecorder(input, mimeType ? { mimeType } : undefined);
  const parts: Blob[] = [];
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) parts.push(event.data);
  };
  const finished = new Promise<Blob | null>((resolve) => {
    recorder.onstop = () => {
      if (ownsStream) input.getTracks().forEach((track) => track.stop());
      resolve(parts.length ? new Blob(parts, { type: recorder.mimeType || mimeType || "audio/webm" }) : null);
    };
  });
  recorder.start(250);
  const limit = setTimeout(() => {
    if (recorder.state !== "inactive") recorder.stop();
  }, maxMs);
  let cancelled = false;
  return {
    async stop() {
      clearTimeout(limit);
      if (recorder.state !== "inactive") recorder.stop();
      const clip = await finished;
      return cancelled ? null : clip;
    },
    cancel() {
      cancelled = true;
      clearTimeout(limit);
      if (recorder.state !== "inactive") recorder.stop();
    },
  };
}

/**
 * Record one spoken turn: waits for speech, then stops after `silenceMs` of quiet
 * (or `maxMs`). Resolves null if the guest never spoke or `signal` aborted.
 */
export async function recordUtterance(options: {
  silenceMs?: number;
  maxMs?: number;
  waitForSpeechMs?: number;
  onLevel?: (level: number) => void;
  signal?: AbortSignal;
} = {}): Promise<Blob | null> {
  const { silenceMs = 1200, maxMs = 20000, waitForSpeechMs = 12000, onLevel, signal } = options;
  const Ctx = audioContextClass();
  if (!Ctx) throw new Error("AUDIO_UNSUPPORTED");
  const stream = await openMicrophone();
  const recording = await startRecording(maxMs, stream);
  const context = new Ctx();
  const analyser = context.createAnalyser();
  analyser.fftSize = 1024;
  context.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  const started = performance.now();
  let spokeAt = 0;
  let lastLoudAt = 0;

  const cleanup = () => {
    stream.getTracks().forEach((track) => track.stop());
    void context.close();
  };

  return new Promise<Blob | null>((resolve) => {
    let done = false;
    const finish = async (keep: boolean) => {
      if (done) return;
      done = true;
      if (!keep) {
        recording.cancel();
        cleanup();
        resolve(null);
        return;
      }
      const clip = await recording.stop();
      cleanup();
      resolve(clip);
    };
    signal?.addEventListener("abort", () => void finish(false), { once: true });
    const tick = () => {
      if (done || signal?.aborted) return;
      analyser.getFloatTimeDomainData(samples);
      let peak = 0;
      for (const value of samples) peak = Math.max(peak, Math.abs(value));
      onLevel?.(peak);
      const now = performance.now();
      if (peak > 0.06) {
        lastLoudAt = now;
        if (!spokeAt) spokeAt = now;
      }
      if (!spokeAt && now - started > waitForSpeechMs) return void finish(false);
      if (spokeAt && now - lastLoudAt > silenceMs) return void finish(true);
      if (now - started > maxMs) return void finish(Boolean(spokeAt));
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}
