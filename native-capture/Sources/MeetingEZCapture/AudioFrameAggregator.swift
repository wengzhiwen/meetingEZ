import AVFoundation
import CoreMedia
import Foundation

/// CMSampleBuffer → 定长 Float32 LE 单声道帧。
///
/// SCK 正常会按 SCStreamConfiguration 交付 16k/mono/Float32；万一实际格式不同
/// （以首帧 ASBD 为准），多通道先 downmix，非 16k 用 AVAudioConverter 流式兜底转换，
/// 线上合同（16k mono Float32）不变。全部状态 confined 到调用方指定的串行队列。
final class AudioFrameAggregator: NSObject, @unchecked Sendable {
    private let targetFormat: AVAudioFormat
    private let frameSamples: Int

    private var acc: [Float] = []
    private var converter: AVAudioConverter?
    private var converterInputFormat: AVAudioFormat?
    private var lastAppendAt = Date()

    init(targetRate: Int, frameSamples: Int) {
        self.targetFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                          sampleRate: Double(targetRate),
                                          channels: 1,
                                          interleaved: false)!
        self.frameSamples = frameSamples
        super.init()
    }

    var pendingSamples: Int { acc.count }

    /// 追加一块音频样本，返回凑满的定长帧。
    func append(_ sampleBuffer: CMSampleBuffer) -> [Data] {
        lastAppendAt = Date()
        guard let mono = normalizeToMono(sampleBuffer) else { return [] }
        acc.reserveCapacity(acc.count + mono.count)
        acc.append(contentsOf: mono)

        var frames: [Data] = []
        let bytesPerSample = MemoryLayout<Float>.size
        while acc.count >= frameSamples {
            var data = Data(capacity: frameSamples * bytesPerSample)
            acc.withUnsafeBufferPointer { ptr in
                ptr.baseAddress!.withMemoryRebound(to: UInt8.self,
                                                   capacity: frameSamples * bytesPerSample) { bytes in
                    data.append(bytes, count: frameSamples * bytesPerSample)
                }
            }
            frames.append(data)
            acc.removeFirst(frameSamples)
        }
        return frames
    }

    /// 尾帧冲刷：距上次追加超过 ~120ms 仍未凑满一整帧时，把残余样本作为短帧发出。
    func flushStaleTail() -> Data? {
        guard !acc.isEmpty, Date().timeIntervalSince(lastAppendAt) > 0.12 else { return nil }
        let bytesPerSample = MemoryLayout<Float>.size
        var data = Data(capacity: acc.count * bytesPerSample)
        acc.withUnsafeBufferPointer { ptr in
            ptr.baseAddress!.withMemoryRebound(to: UInt8.self,
                                               capacity: acc.count * bytesPerSample) { bytes in
                data.append(bytes, count: acc.count * bytesPerSample)
            }
        }
        acc.removeAll(keepingCapacity: true)
        return data
    }

    // ---- CMSampleBuffer → [Float]（16k mono） ----

    private func normalizeToMono(_ sampleBuffer: CMSampleBuffer) -> [Float]? {
        guard let asbd = sampleBuffer.formatDescription?.audioStreamBasicDescription else {
            FileHandle.standardError.write(Data("audio buffer missing ASBD\n".utf8))
            return nil
        }
        let channels = max(Int(asbd.mChannelsPerFrame), 1)
        let bytesPerFrame = max(Int(asbd.mBytesPerFrame), 1)

        // 提取 AudioBufferList（按需分配可变长度，栈上单个 AudioBufferList 装不下多通道）。
        let listSize = MemoryLayout<AudioBufferList>.size
            + (channels - 1) * MemoryLayout<AudioBuffer>.size
        var listData = Data(count: listSize)
        var blockBuffer: CMBlockBuffer?
        let status = listData.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) -> OSStatus in
            CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
                sampleBuffer,
                bufferListSizeNeededOut: nil,
                bufferListOut: raw.baseAddress?.assumingMemoryBound(to: AudioBufferList.self),
                bufferListSize: listSize,
                blockBufferAllocator: kCFAllocatorDefault,
                blockBufferMemoryAllocator: kCFAllocatorDefault,
                flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
                blockBufferOut: &blockBuffer)
        }
        guard status == kCMBlockBufferNoErr else {
            FileHandle.standardError.write(Data("audio buffer list error: \(status)\n".utf8))
            return nil
        }

        let isFloat32 = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0 && asbd.mBitsPerChannel == 32
        let interleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) == 0

        let result: [Float] = listData.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) -> [Float] in
            let list = raw.baseAddress!.assumingMemoryBound(to: AudioBufferList.self)
            let buffers = UnsafeMutableAudioBufferListPointer(list)
            guard let first = buffers.first, let firstData = first.mData else { return [] }
            let frames = Int(first.mDataByteSize) / bytesPerFrame
            guard frames > 0 else { return [] }

            if !isFloat32 {
                FileHandle.standardError.write(Data("unexpected pcm format (bits=\(asbd.mBitsPerChannel))\n".utf8))
                return []
            }

            if channels == 1 {
                let src = firstData.assumingMemoryBound(to: Float32.self)
                return Array(UnsafeBufferPointer(start: src, count: frames))
            }

            // 多通道 downmix 到单声道。
            var mono = [Float](repeating: 0, count: frames)
            if interleaved {
                let src = firstData.assumingMemoryBound(to: Float32.self)
                for frame in 0..<frames {
                    var sum: Float = 0
                    for ch in 0..<channels {
                        sum += src[frame * channels + ch]
                    }
                    mono[frame] = sum / Float(channels)
                }
            } else {
                for buffer in buffers {
                    guard let data = buffer.mData else { continue }
                    let src = data.assumingMemoryBound(to: Float32.self)
                    let channelFrames = min(Int(buffer.mDataByteSize) / MemoryLayout<Float32>.size, frames)
                    for frame in 0..<channelFrames {
                        mono[frame] += src[frame]
                    }
                }
                for frame in 0..<frames {
                    mono[frame] /= Float(channels)
                }
            }
            return mono
        }

        guard !result.isEmpty else { return nil }

        // 采样率即目标：直接返回。
        if asbd.mSampleRate == targetFormat.sampleRate {
            return result
        }

        // 兜底：非目标采样率，mono→mono 走 AVAudioConverter（保持重采样状态）。
        return convertToTargetRate(mono: result, sourceRate: asbd.mSampleRate) ?? result
    }

    private func convertToTargetRate(mono: [Float], sourceRate: Float64) -> [Float]? {
        guard let inputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                              sampleRate: sourceRate,
                                              channels: 1,
                                              interleaved: false) else { return nil }
        if converter == nil || converterInputFormat != inputFormat {
            converterInputFormat = inputFormat
            converter = AVAudioConverter(from: inputFormat, to: targetFormat)
        }
        guard let converter else { return nil }

        let frameCount = AVAudioFrameCount(mono.count)
        guard frameCount > 0,
              let input = AVAudioPCMBuffer(pcmFormat: inputFormat, frameCapacity: frameCount) else { return nil }
        input.frameLength = frameCount
        if let dst = input.floatChannelData {
            dst[0].update(from: mono, count: Int(frameCount))
        }

        let ratio = targetFormat.sampleRate / max(sourceRate, 1)
        let capacity = AVAudioFrameCount(Double(frameCount) * ratio) + 32
        guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return nil }

        var fed = false
        var conversionError: NSError?
        let status = converter.convert(to: output, error: &conversionError) { _, outStatus in
            if !fed {
                fed = true
                outStatus.pointee = .haveData
                return input
            }
            outStatus.pointee = .noDataNow
            return nil
        }
        if let conversionError {
            FileHandle.standardError.write(Data("audio convert error: \(conversionError)\n".utf8))
            return nil
        }
        guard status != .error, let outChannel = output.floatChannelData?[0] else { return nil }
        return Array(UnsafeBufferPointer(start: outChannel, count: Int(output.frameLength)))
    }
}
