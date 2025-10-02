/**
 * AudioWorklet Processor - 音频采集处理器
 * 运行在独立的音频线程，不受主线程阻塞影响
 * 职责：实时采集音频并传递到主线程，确保零丢失
 */
class AudioCaptureProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.bufferSize = 2048; // 与之前的 ScriptProcessor 保持一致
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferIndex = 0;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        
        // 如果没有输入，返回 true 继续处理
        if (!input || !input[0]) {
            return true;
        }

        const inputChannel = input[0]; // 单声道

        // 累积音频数据到缓冲区
        for (let i = 0; i < inputChannel.length; i++) {
            this.buffer[this.bufferIndex++] = inputChannel[i];

            // 当缓冲区满时，发送到主线程
            if (this.bufferIndex >= this.bufferSize) {
                // 复制数据（避免引用问题）
                const data = new Float32Array(this.buffer);
                
                // 计算 RMS（用于音量指示）
                let sum = 0;
                for (let j = 0; j < data.length; j++) {
                    sum += data[j] * data[j];
                }
                const rms = Math.sqrt(sum / data.length);

                // 发送到主线程
                this.port.postMessage({
                    type: 'audio',
                    data: data,
                    rms: rms
                });

                // 重置缓冲区
                this.bufferIndex = 0;
            }
        }

        // 返回 true 继续处理
        return true;
    }
}

registerProcessor('audio-capture-processor', AudioCaptureProcessor);

