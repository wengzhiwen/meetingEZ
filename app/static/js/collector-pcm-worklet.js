/**
 * collector-pcm-worklet — 本地采集器 PCM 回放 AudioWorklet。
 *
 * 环形缓冲 + 目标水位控制延迟：
 *   - 缓冲容量 16384 样本（16kHz 下约 1 秒）；
 *   - 目标水位 3200 样本（200ms）：正常流转的蓄水；
 *   - 超过 8192（500ms）时丢最旧回到目标水位，防止断流恢复后延迟累积；
 *   - process() 欠载时输出静音（自然防爆音），每约 500ms 上报水位与累计丢弃数。
 *
 * 输入消息：{type:'pcm', samples:Float32Array}（buffer 已转移所有权）/ {type:'clear'}。
 * 输出消息：{type:'level', buffered, dropped}。
 *
 * 注意：此文件运行在 AudioWorkletGlobalScope，不能有任何 DOM/import 引用。
 */
class CollectorPcmPlayer extends AudioWorkletProcessor {
    constructor() {
        super();
        this.capacity = 16384;
        this.buffer = new Float32Array(this.capacity);
        this.readIndex = 0;
        this.writeIndex = 0;
        this.buffered = 0;
        this.targetWatermark = 3200;
        this.overflowLimit = 8192;
        this.dropped = 0;
        this.lastReportAt = 0;

        this.port.onmessage = (event) => {
            const data = event.data;
            if (!data) return;
            if (data.type === 'clear') {
                this.readIndex = 0;
                this.writeIndex = 0;
                this.buffered = 0;
                return;
            }
            if (data.type === 'pcm' && data.samples) {
                this.enqueue(data.samples);
            }
        };
    }

    enqueue(samples) {
        for (let i = 0; i < samples.length; i++) {
            if (this.buffered >= this.capacity) {
                // 环满：丢最旧。
                this.readIndex = (this.readIndex + 1) % this.capacity;
                this.buffered -= 1;
                this.dropped += 1;
            }
            this.buffer[this.writeIndex] = samples[i];
            this.writeIndex = (this.writeIndex + 1) % this.capacity;
            this.buffered += 1;
        }
        if (this.buffered > this.overflowLimit) {
            const drop = this.buffered - this.targetWatermark;
            this.readIndex = (this.readIndex + drop) % this.capacity;
            this.buffered -= drop;
            this.dropped += drop;
        }
    }

    process(inputs, outputs) {
        const output = outputs[0] && outputs[0][0];
        if (output) {
            if (this.buffered < output.length) {
                output.fill(0);
            } else {
                for (let i = 0; i < output.length; i++) {
                    output[i] = this.buffer[this.readIndex];
                    this.readIndex = (this.readIndex + 1) % this.capacity;
                }
                this.buffered -= output.length;
            }
        }
        if (currentTime - this.lastReportAt >= 0.5) {
            this.lastReportAt = currentTime;
            this.port.postMessage({ type: 'level', buffered: this.buffered, dropped: this.dropped });
        }
        return true;
    }
}

registerProcessor('collector-pcm-player', CollectorPcmPlayer);
