/*
 * Microphone capture for live voice: turns the browser's audio (usually 44.1 or
 * 48 kHz float) into 16 kHz, 16-bit mono PCM and posts it in ~50 ms chunks.
 * Each output sample is the average of the input it covers (a simple low-pass),
 * so high sounds do not fold back into speech as noise (clearer "s" and "sh").
 * Served from this site so the page's Content-Security-Policy ('self') allows it.
 */
const TARGET_RATE = 16000;
const CHUNK_SAMPLES = 800; // 50 ms at 16 kHz: small steady pieces travel more evenly

class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / TARGET_RATE; // input samples per output sample (`sampleRate`: the context rate)
    this.sum = 0; // weighted input gathered for the current output sample
    this.weight = 0; // how much of `step` it covers so far
    this.out = new Int16Array(CHUNK_SAMPLES);
    this.filled = 0;
    this.level = 0;
    this.muted = false;
    this.port.onmessage = (event) => {
      if (event.data && typeof event.data.muted === "boolean") this.muted = event.data.muted;
    };
  }

  emit(value) {
    const sample = this.muted ? 0 : Math.max(-1, Math.min(1, value));
    this.level = Math.max(this.level, Math.abs(sample));
    this.out[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    if (this.filled === CHUNK_SAMPLES) {
      this.port.postMessage({ pcm: this.out.buffer, level: this.level }, [this.out.buffer]);
      this.out = new Int16Array(CHUNK_SAMPLES);
      this.filled = 0;
      this.level = 0;
    }
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input || input.length === 0) return true;
    for (let i = 0; i < input.length; i += 1) {
      let remaining = 1; // this input sample's share, split across output samples when it straddles two
      while (remaining > 0) {
        const room = this.step - this.weight;
        const take = Math.min(room, remaining);
        this.sum += input[i] * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= this.step - 1e-9) {
          this.emit(this.sum / this.step);
          this.sum = 0;
          this.weight = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("mic-capture", MicCaptureProcessor);
