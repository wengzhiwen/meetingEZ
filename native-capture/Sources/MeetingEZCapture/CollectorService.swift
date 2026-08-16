import Foundation

extension Notification.Name {
    /// 采集状态变化，userInfo: ["capturing": Bool, "detail": String]
    static let captureStateDidChange = Notification.Name("captureStateDidChange")
    /// 客户端连接状态变化，userInfo: ["connected": Bool]
    static let captureClientStateDidChange = Notification.Name("captureClientStateDidChange")
    /// TCC 已授权但需重启进程才能生效
    static let capturePermissionNeedsRestart = Notification.Name("capturePermissionNeedsRestart")
}

/// 命令路由与状态中枢：把 WS 控制命令翻译成 CaptureSource 操作，把源事件推回客户端。
/// 全部自身状态 confined 到 serviceQueue。
final class CollectorService: @unchecked Sendable {
    let server: CaptureServer
    let source: CaptureSource

    private let serviceQueue = DispatchQueue(label: "collector-service")
    private var pendingOp = false
    private var isCapturing = false
    private var capturingDetail = ""
    private var framesSent = 0
    private var statsTimer: DispatchSourceTimer?
    private var permissionTimer: DispatchSourceTimer?
    private var clientConnected = false

    init(server: CaptureServer, source: CaptureSource) {
        self.server = server
        self.source = source
    }

    func run() {
        server.onCommand = { [weak self] conn, object in
            guard let self else { return }
            self.serviceQueue.async { self.handleCommand(conn, object) }
        }
        server.onControlConnected = { [weak self] conn in
            guard let self else { return }
            self.serviceQueue.async { self.handleClientConnected(conn) }
        }
        server.onAllDisconnected = { [weak self] in
            guard let self else { return }
            self.serviceQueue.async { self.handleAllDisconnected() }
        }
        server.onControlReplaced = nil

        source.onFrame = { [weak self] data in
            guard let self else { return }
            self.serviceQueue.async {
                self.framesSent += 1
                self.server.broadcastAudio(data)
                self.observeLevel(data)
            }
        }
        source.onStopped = { [weak self] reason, message in
            guard let self else { return }
            self.serviceQueue.async { self.handleSourceStopped(reason: reason, message: message) }
        }
    }

    // ---- 入站命令 ----

    private func handleCommand(_ conn: ClientConnection, _ object: [String: Any]) {
        let type = object["type"] as? String ?? ""
        logToFile("command: \(type) state(capturing=\(isCapturing), pendingOp=\(pendingOp))")
        let ref = object["ref"]

        func reply(_ payload: [String: Any]) {
            var full = payload
            if let ref, !(ref is NSNull) {
                full["ref"] = ref
            }
            server.sendText(to: conn, full)
        }

        switch type {
        case "ping":
            reply(["type": "pong"])

        case "requestPermission":
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                // CGRequestScreenCaptureAccess 会弹系统授权窗并等待用户响应。
                source.requestPermission()
                let granted = source.preflightPermission()
                guard granted else {
                    reply(["type": "permission", "granted": false, "effective": false])
                    return
                }
                source.verifyEffectivePermission { effective in
                    reply(["type": "permission", "granted": true, "effective": effective])
                    self.serviceQueue.async { self.finishPermissionCheck(effective: effective) }
                }
            }

        case "listApps":
            guard source.preflightPermission() else {
                let error = CollectorError.noPermission()
                reply(["type": "error", "code": error.code, "message": error.message])
                return
            }
            source.listApplications { entries in
                self.serviceQueue.async {
                    reply(["type": "apps", "apps": entries.map(\.json)])
                }
            }

        case "start":
            handleStart(conn, object, reply)

        case "stop":
            guard !pendingOp else {
                let error = CollectorError.busy()
                reply(["type": "error", "code": error.code, "message": error.message])
                return
            }
            if !isCapturing {
                reply(["type": "stopped", "reason": "request"])
                return
            }
            pendingOp = true
            source.stop {
                self.serviceQueue.async {
                    self.pendingOp = false
                    logToFile("stop completed, replying stopped")
                    self.setCapturing(false, detail: "")
                    self.stopStatsTimer()
                    reply(["type": "stopped", "reason": "request"])
                }
            }

        default:
            let error = CollectorError.invalidParams("未知命令: \(type)")
            reply(["type": "error", "code": error.code, "message": error.message])
        }
    }

    private func handleStart(_ conn: ClientConnection, _ object: [String: Any],
                             _ reply: @escaping ([String: Any]) -> Void) {
        guard source.preflightPermission() else {
            let error = CollectorError.noPermission()
            reply(["type": "error", "code": error.code, "message": error.message])
            return
        }
        guard !pendingOp else {
            let error = CollectorError.busy()
            reply(["type": "error", "code": error.code, "message": error.message])
            return
        }

        let mode: CaptureMode
        switch object["mode"] as? String {
        case "system":
            mode = .system
        case "apps":
            let ids = (object["bundleIds"] as? [String] ?? []).filter { !$0.isEmpty }
            guard !ids.isEmpty else {
                let error = CollectorError.invalidParams("apps 模式需要非空 bundleIds。")
                reply(["type": "error", "code": error.code, "message": error.message])
                return
            }
            mode = .apps(bundleIds: ids)
        default:
            let error = CollectorError.invalidParams("mode 必须是 apps 或 system。")
            reply(["type": "error", "code": error.code, "message": error.message])
            return
        }

        pendingOp = true
        source.start(mode: mode) { result in
            self.serviceQueue.async {
                self.pendingOp = false
                logToFile("start result: \(result)")
                switch result {
                case let .success(format):
                    self.setCapturing(true, detail: self.describe(mode: mode))
                    self.startStatsTimer()
                    reply(["type": "started", "mode": mode.name, "sources": self.sourceNames(mode: mode),
                           "audioFormat": format.json])
                case let .failure(error):
                    let err = error as? CollectorError
                        ?? CollectorError.startFailed(error.localizedDescription)
                    reply(["type": "error", "code": err.code, "message": err.message])
                }
            }
        }
    }

    // ---- 源事件 ----

    private func handleSourceStopped(reason: String, message: String) {
        guard isCapturing else { return }
        isCapturing = false
        capturingDetail = ""
        stopStatsTimer()
        server.broadcastText(["type": "stopped", "reason": reason, "message": message])
        notifyState()
    }

    private func handleClientConnected(_ conn: ClientConnection) {
        let firstClient = !clientConnected
        clientConnected = true
        // hello 只发给新控制连接，不广播。
        server.sendText(to: conn, helloObject())
        if firstClient {
            startPermissionPollingIfNeeded()
        }
        NotificationCenter.default.post(name: .captureClientStateDidChange, object: nil,
                                        userInfo: ["connected": true])
    }

    private func handleAllDisconnected() {
        clientConnected = false
        stopPermissionPolling()
        // 无客户端时立即停止采集：屏幕录制指示器不该在没人监听时点亮。
        if isCapturing || source.isCapturing {
            source.stop {
                self.serviceQueue.async {
                    self.setCapturing(false, detail: "")
                    self.stopStatsTimer()
                }
            }
        }
        NotificationCenter.default.post(name: .captureClientStateDidChange, object: nil,
                                        userInfo: ["connected": false])
    }

    private func helloObject() -> [String: Any] {
        ["type": "hello", "version": protocolVersion,
         "permission": source.preflightPermission(),
         "mock": source.isMock]
    }

    // ---- 权限轮询 ----

    private func startPermissionPollingIfNeeded() {
        guard !source.preflightPermission() else { return }
        guard permissionTimer == nil else { return }
        let timer = DispatchSource.makeTimerSource(queue: serviceQueue)
        timer.schedule(deadline: .now() + 1.5, repeating: 1.5)
        timer.setEventHandler { [self] in
            guard clientConnected else {
                stopPermissionPolling()
                return
            }
            guard source.preflightPermission() else { return }
            stopPermissionPolling()
            source.verifyEffectivePermission { [self] effective in
                serviceQueue.async { [self] in
                    server.broadcastText(["type": "permission", "granted": true, "effective": effective])
                    finishPermissionCheck(effective: effective)
                }
            }
        }
        timer.resume()
        permissionTimer = timer
    }

    private func stopPermissionPolling() {
        permissionTimer?.cancel()
        permissionTimer = nil
    }

    private func finishPermissionCheck(effective: Bool) {
        if effective {
            source.listApplications { [self] entries in
                serviceQueue.async { [self] in
                    server.broadcastText(["type": "apps", "apps": entries.map(\.json)])
                }
            }
        } else {
            NotificationCenter.default.post(name: .capturePermissionNeedsRestart, object: nil)
        }
    }

    // ---- 实时电平（状态面板用，约 250ms 一次通知） ----

    private var levelSamples = 0
    private var levelSquares: Double = 0
    private var lastLevelPost = Date()

    private func observeLevel(_ data: Data) {
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let buffer = raw.bindMemory(to: Float32.self)
            for sample in buffer {
                levelSquares += Double(sample) * Double(sample)
            }
            levelSamples += buffer.count
        }
        guard Date().timeIntervalSince(lastLevelPost) >= 0.25 else { return }
        lastLevelPost = Date()
        let rms = levelSamples > 0 ? (levelSquares / Double(levelSamples)).squareRoot() : 0
        levelSamples = 0
        levelSquares = 0
        NotificationCenter.default.post(
            name: .captureAudioLevel, object: nil,
            userInfo: ["rms": rms, "framesSent": framesSent,
                       "framesDropped": server.totalFramesDropped()])
    }

    // ---- 统计 ----

    private func startStatsTimer() {
        guard statsTimer == nil else { return }
        framesSent = 0
        let timer = DispatchSource.makeTimerSource(queue: serviceQueue)
        timer.schedule(deadline: .now() + 10, repeating: 10)
        timer.setEventHandler { [self] in
            server.broadcastText(["type": "stats", "framesSent": framesSent,
                                  "framesDropped": server.totalFramesDropped()])
        }
        timer.resume()
        statsTimer = timer
    }

    private func stopStatsTimer() {
        statsTimer?.cancel()
        statsTimer = nil
    }

    // ---- 杂项 ----

    private func setCapturing(_ value: Bool, detail: String) {
        isCapturing = value
        capturingDetail = detail
        notifyState()
    }

    private func notifyState() {
        NotificationCenter.default.post(name: .captureStateDidChange, object: nil,
                                        userInfo: ["capturing": isCapturing, "detail": capturingDetail])
    }

    private func describe(mode: CaptureMode) -> String {
        switch mode {
        case .system:
            return "整个系统音频"
        case .apps(let bundleIds):
            return "应用: \(bundleIds.joined(separator: ", "))"
        }
    }

    private func sourceNames(mode: CaptureMode) -> [String] {
        if case .apps(let bundleIds) = mode {
            return bundleIds
        }
        return ["system"]
    }
}
