/**
 * Web Worker - WAV 编码器
 * 运行在独立的 Worker 线程，负责同步 WAV 编码
 * 职责：接收 Float32 音频数据，编码为 WAV 格式，返回 Blob
 */

// 监听主线程消息
self.onmessage = function(e) {
    const { id, float32Array, sampleRate } = e.data;

    try {
        // 执行 WAV 编码（同步操作，但在独立线程不影响主线程）
        const wavBlob = encodeWav(float32Array, sampleRate);

        // 返回结果到主线程
        self.postMessage({
            id: id,
            success: true,
            blob: wavBlob
        });
    } catch (error) {
        // 返回错误
        self.postMessage({
            id: id,
            success: false,
            error: error.message
        });
    }
};

/**
 * 将 Float32 PCM 编码为 WAV 格式
 * @param {Float32Array} float32Array - 音频数据
 * @param {number} sampleRate - 采样率
 * @returns {Blob} WAV 格式的 Blob
 */
function encodeWav(float32Array, sampleRate) {
    // 转换为 Int16 PCM
    const bufferLength = float32Array.length;
    const pcm16 = new Int16Array(bufferLength);
    for (let i = 0; i < bufferLength; i++) {
        const s = Math.max(-1, Math.min(1, float32Array[i]));
        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }

    const wavBuffer = new ArrayBuffer(44 + pcm16.length * 2);
    const view = new DataView(wavBuffer);

    // RIFF header
    writeString(view, 0, 'RIFF');
    view.setUint32(4, 36 + pcm16.length * 2, true);
    writeString(view, 8, 'WAVE');

    // fmt chunk
    writeString(view, 12, 'fmt ');
    view.setUint32(16, 16, true); // PCM chunk size
    view.setUint16(20, 1, true); // audio format = PCM
    view.setUint16(22, 1, true); // channels = 1
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true); // byte rate
    view.setUint16(32, 2, true); // block align
    view.setUint16(34, 16, true); // bits per sample

    // data chunk
    writeString(view, 36, 'data');
    view.setUint32(40, pcm16.length * 2, true);

    // PCM samples
    let offset = 44;
    for (let i = 0; i < pcm16.length; i++, offset += 2) {
        view.setInt16(offset, pcm16[i], true);
    }

    return new Blob([view], { type: 'audio/wav' });
}

function writeString(dataview, offset, string) {
    for (let i = 0; i < string.length; i++) {
        dataview.setUint8(offset + i, string.charCodeAt(i));
    }
}

