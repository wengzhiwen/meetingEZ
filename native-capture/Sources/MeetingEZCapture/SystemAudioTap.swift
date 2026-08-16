import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
@preconcurrency import ScreenCaptureKit

/// ScreenCaptureKit 封装：权限、应用枚举、纯音频 SCStream 生命周期与错误恢复。
///
/// 线程纪律：
///  - SCStream/过滤器的创建与销毁都发生在 sckQueue（Task 中的 await 结果跳回 sckQueue 再落状态）；
///  - 音频样本回调在 captureQueue 上处理（聚合、格式兜底转换）；
///  - 对外回调（onFrame/onStopped/完成闭包）可能来自上述队列，由 CollectorService 自行跳队列。
final class SystemAudioTap: NSObject, CaptureSource, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    let isMock = false

    var onFrame: ((Data) -> Void)?
    var onStopped: ((_ reason: String, _ message: String) -> Void)?

    private let sckQueue = DispatchQueue(label: "sck")
    private let captureQueue = DispatchQueue(label: "capture-audio")

    private var stream: SCStream?
    private var mode: CaptureMode?
    private var aggregator: AudioFrameAggregator?
    private var flushTimer: DispatchSourceTimer?
    private var pendingStartCompletion: ((Result<AudioFormat, Error>) -> Void)?
    private var firstFrameSeen = false
    private var noFrameTimeout: DispatchWorkItem?
    private var retryAttempted = false
    private var stopRequested = false

    var isCapturing: Bool {
        sckQueue.sync { stream != nil }
    }

    // ---- 权限 ----

    func preflightPermission() -> Bool {
        CGPreflightScreenCaptureAccess()
    }

    func requestPermission() {
        _ = CGRequestScreenCaptureAccess()
    }

    func verifyEffectivePermission(_ completion: @escaping (Bool) -> Void) {
        Task {
            do {
                _ = try await SCShareableContent.current
                completion(true)
            } catch {
                completion(false)
            }
        }
    }

    // ---- 应用枚举 ----

    func listApplications(completion: @escaping ([AppEntry]) -> Void) {
        Task {
            do {
                let content = try await SCShareableContent.current
                let ownBundleId = Bundle.main.bundleIdentifier
                var seen = Set<String>()
                var entries: [AppEntry] = []
                for app in content.applications {
                    let bundleId = app.bundleIdentifier
                    if bundleId == ownBundleId { continue }
                    // 空名进程（后台代理等）没有可展示的入口，跳过。
                    if app.applicationName.isEmpty { continue }
                    guard seen.insert(bundleId).inserted else { continue }
                    entries.append(AppEntry(pid: app.processID, bundleId: bundleId, name: app.applicationName))
                }
                completion(entries.sorted {
                    $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
                })
            } catch {
                FileHandle.standardError.write(Data("listApplications failed: \(error)\n".utf8))
                completion([])
            }
        }
    }

    // ---- 采集生命周期 ----

    func start(mode: CaptureMode, completion: @escaping (Result<AudioFormat, Error>) -> Void) {
        sckQueue.async { [self] in
            if pendingStartCompletion != nil {
                completion(.failure(CollectorError.busy()))
                return
            }
            stopRequested = false
            if stream != nil {
                if mode == self.mode, let reported = reportedFormat {
                    // 同参数幂等：直接回成功，不重建流。
                    completion(.success(reported))
                    return
                }
                // 参数变化：先停旧流再启动。
                pendingStartCompletion = completion
                teardownLocked { [self] in
                    startLocked(mode)
                }
                return
            }
            pendingStartCompletion = completion
            startLocked(mode)
        }
    }

    func stop(completion: (() -> Void)?) {
        sckQueue.async { [self] in
            stopRequested = true
            if let pending = pendingStartCompletion {
                // 启动途中被取消：起流成功后也会被立即拆除。
                pendingStartCompletion = nil
                if stream == nil {
                    pending(.failure(CollectorError("busy", "启动已被停止请求打断。")))
                    completion?()
                    return
                }
            }
            guard stream != nil else {
                completion?()
                return
            }
            teardownLocked {
                completion?()
            }
        }
    }

    private var reportedFormat: AudioFormat?

    /// sckQueue 上调用。
    private func startLocked(_ mode: CaptureMode) {
        Task { [self] in
            do {
                let content = try await SCShareableContent.current
                guard let display = content.displays.first(where: { $0.width >= $0.height })
                        ?? content.displays.first else {
                    throw CollectorError.startFailed("没有可用的显示器。")
                }

                let filter: SCContentFilter
                switch mode {
                case .system:
                    filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
                case .apps(let bundleIds):
                    // 按新鲜的应用表把 bundleId 展开到当前全部匹配进程，
                    // 天然容忍应用重启（pid 变化）。
                    let apps = content.applications.filter { bundleIds.contains($0.bundleIdentifier) }
                    guard !apps.isEmpty else {
                        throw CollectorError.invalidParams(
                            "未找到匹配的应用（可能已退出）: \(bundleIds.joined(separator: ", "))")
                    }
                    filter = SCContentFilter(display: display, including: apps, exceptingWindows: [])
                }

                let config = SCStreamConfiguration()
                config.capturesAudio = true
                config.excludesCurrentProcessAudio = true
                config.sampleRate = AudioFormat.wire.sampleRate
                config.channelCount = AudioFormat.wire.channels
                // 纯音频流：视频维度压到最小正数（0 会失败），且不注册 .screen 输出。
                config.width = 2
                config.height = 2

                let stream = SCStream(filter: filter, configuration: config, delegate: self)
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: captureQueue)
                try await stream.startCapture()

                sckQueue.async { [self] in
                    guard !stopRequested else {
                        // 等待中的 stop：立即拆除，回失败。
                        stream.stopCapture { _ in }
                        failPendingStart(CollectorError("busy", "启动已被停止请求打断。"))
                        return
                    }
                    self.stream = stream
                    self.mode = mode
                    self.retryAttempted = false
                    self.firstFrameSeen = false
                    self.reportedFormat = nil
                    self.aggregator = AudioFrameAggregator(targetRate: AudioFormat.wire.sampleRate,
                                                           frameSamples: AudioFormat.wire.sampleRate / 10)
                    startFlushTimer()
                    armNoFrameTimeout()
                    // 首帧到达时读实际交付格式回填；静音目标 1.5s 内无帧也按请求格式放行，
                    // 避免 start 卡死。
                }
            } catch {
                sckQueue.async { [self] in
                    failPendingStart(error as? CollectorError
                                     ?? CollectorError.startFailed(error.localizedDescription))
                }
            }
        }
    }

    /// sckQueue 上调用。
    private func teardownLocked(_ done: @escaping () -> Void) {
        let stream = self.stream
        self.stream = nil
        self.mode = nil
        self.reportedFormat = nil
        stopFlushTimer()
        noFrameTimeout?.cancel()
        noFrameTimeout = nil
        aggregator = nil
        guard let stream else {
            done()
            return
        }
        stream.stopCapture { _ in
            try? stream.removeStreamOutput(self, type: .audio)
            done()
        }
    }

    private func failPendingStart(_ error: Error) {
        pendingStartCompletion?(.failure(error))
        pendingStartCompletion = nil
    }

    private func resolvePendingStart(_ format: AudioFormat) {
        pendingStartCompletion?(.success(format))
        pendingStartCompletion = nil
    }

    private func armNoFrameTimeout() {
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.stream != nil, !self.firstFrameSeen else { return }
            self.reportedFormat = AudioFormat.wire
            self.resolvePendingStart(AudioFormat.wire)
        }
        noFrameTimeout = work
        sckQueue.asyncAfter(deadline: .now() + 1.5, execute: work)
    }

    private func startFlushTimer() {
        let timer = DispatchSource.makeTimerSource(queue: captureQueue)
        timer.schedule(deadline: .now() + 0.06, repeating: 0.06)
        timer.setEventHandler { [weak self] in
            guard let self, let aggregator = self.aggregator else { return }
            if let tail = aggregator.flushStaleTail() {
                self.onFrame?(tail)
            }
        }
        timer.resume()
        flushTimer = timer
    }

    private func stopFlushTimer() {
        flushTimer?.cancel()
        flushTimer = nil
    }

    // ---- SCStreamOutput ----

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, CMSampleBufferIsValid(sampleBuffer) else { return }
        captureQueue.async { [weak self] in
            self?.handleAudio(sampleBuffer)
        }
    }

    private func handleAudio(_ sampleBuffer: CMSampleBuffer) {
        guard let aggregator else { return }
        if !firstFrameSeen {
            firstFrameSeen = true
            sckQueue.async { [self] in
                noFrameTimeout?.cancel()
                noFrameTimeout = nil
                reportedFormat = AudioFormat.wire
                resolvePendingStart(AudioFormat.wire)
            }
        }
        for frame in aggregator.append(sampleBuffer) {
            onFrame?(frame)
        }
    }

    // ---- SCStreamDelegate ----

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        sckQueue.async { [self] in
            guard let current = self.stream, current === stream else { return }

            let mode = self.mode
            teardownLocked { }

            guard let mode else {
                onStopped?("stream-error", error.localizedDescription)
                return
            }

            // 被采集应用全部退出：不重试。
            if case .apps(let bundleIds) = mode {
                Task {
                    let content = try? await SCShareableContent.current
                    let alive = content?.applications.contains { bundleIds.contains($0.bundleIdentifier) } ?? false
                    sckQueue.async { [self] in
                        if !alive {
                            onStopped?("app-exited", "被采集的应用已退出。")
                        } else {
                            retryOrReport(mode: mode, error: error)
                        }
                    }
                }
            } else {
                retryOrReport(mode: mode, error: error)
            }
        }
    }

    private func retryOrReport(mode: CaptureMode, error: Error) {
        if !retryAttempted {
            retryAttempted = true
            FileHandle.standardError.write(Data("stream stopped, retrying once: \(error)\n".utf8))
            sckQueue.asyncAfter(deadline: .now() + 0.5) { [self] in
                // 静默重建：客户端已收到 started，无需重复通知。
                pendingStartCompletion = { _ in }
                startLocked(mode)
            }
        } else {
            onStopped?("stream-error", error.localizedDescription)
        }
    }
}
