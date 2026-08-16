import Foundation
import Network

/// Origin 白名单：默认只放行本机回环地址上的 http(s) 页面（meetingEZ Web 端），
/// `--allow-origin` 可追加精确值（写法容忍大小写与尾部斜杠），"*" 全放行（仅限本地联调）。
/// 浏览器 WebSocket 握手必带 Origin；缺失即非浏览器客户端，默认拒绝。
struct OriginPolicy {
    var extraAllowed: [String]

    func allows(origin: String?) -> Bool {
        if extraAllowed.contains("*") { return true }
        guard let origin, !origin.isEmpty else { return false }
        let normalized = Self.normalize(origin)
        if extraAllowed.contains(where: { Self.normalize($0) == normalized }) { return true }

        let lowered = normalized
        guard let schemeEnd = lowered.firstIndex(of: ":") else { return false }
        let scheme = lowered[..<schemeEnd]
        guard scheme == "http" || scheme == "https" else { return false }

        var rest = lowered[lowered.index(after: schemeEnd)...]
        while rest.first == "/" { rest = rest.dropFirst() }
        // 去掉可能的 userinfo（浏览器不会发，防御性处理）。
        if let at = rest.firstIndex(of: "@") { rest = rest[rest.index(after: at)...] }

        let host: String
        if rest.hasPrefix("[") {
            // IPv6 字面量：http://[::1]:5090
            guard let close = rest.firstIndex(of: "]") else { return false }
            host = String(rest[...close])
        } else if let colon = rest.firstIndex(of: ":") {
            host = String(rest[..<colon])
        } else {
            host = String(rest)
        }
        return host == "localhost" || host == "127.0.0.1" || host == "[::1]"
    }

    /// 规范化为 scheme://host[:port]：小写、去尾部斜杠与路径（Origin 头本无路径，
    /// 这里容错用户在 --allow-origin 里按 URL 习惯书写的情况）。
    static func normalize(_ value: String) -> String {
        var normalized = value.trimmingCharacters(in: .whitespaces).lowercased()
        while normalized.hasSuffix("/") { normalized.removeLast() }
        return normalized
    }
}

/// 单个 WebSocket 连接：帧收发 + 背压计数。全部状态 confined 到 serverQueue。
final class ClientConnection {
    let connection: NWConnection
    private let onText: (ClientConnection, String) -> Void
    private let onGone: (ClientConnection) -> Void
    private let onReady: (ClientConnection) -> Void

    private(set) var inFlight = 0
    private(set) var framesDropped = 0
    private(set) var isClosed = false
    /// 最近一次出站流量时间（serverQueue 上读写），用于心跳判断。
    var lastOutboundAt = Date()

    /// 音频帧背压上限：8 帧 ≈ 800ms，超限丢帧而不是堆积。
    static let maxInFlightAudio = 8

    init(connection: NWConnection,
         onReady: @escaping (ClientConnection) -> Void,
         onText: @escaping (ClientConnection, String) -> Void,
         onGone: @escaping (ClientConnection) -> Void) {
        self.connection = connection
        self.onReady = onReady
        self.onText = onText
        self.onGone = onGone
    }

    func start(on queue: DispatchQueue) {
        connection.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.onReady(self)
            case .failed, .cancelled:
                self.markClosed()
            default:
                break
            }
        }
        connection.start(queue: queue)
        receiveLoop()
    }

    private func receiveLoop() {
        connection.receiveMessage { [weak self] data, context, _, error in
            guard let self else { return }
            if error != nil {
                self.markClosed()
                return
            }
            if let data, !data.isEmpty,
               let metadata = context?.protocolMetadata(definition: NWProtocolWebSocket.definition)
                   as? NWProtocolWebSocket.Metadata {
                switch metadata.opcode {
                case .text:
                    if let text = String(data: data, encoding: .utf8) {
                        self.onText(self, text)
                    }
                case .binary:
                    // 浏览器发来的二进制帧一律忽略。
                    break
                default:
                    break
                }
            }
            if data != nil || context != nil {
                self.receiveLoop()
            } else if error == nil {
                // 空消息且无上下文：连接对端关闭，结束读循环。
                self.markClosed()
            }
        }
    }

    private func markClosed() {
        if isClosed { return }
        isClosed = true
        onGone(self)
    }

    func cancel() {
        markClosed()
        connection.cancel()
    }

    func sendText(_ object: [String: Any]) {
        let data = Data(jsonLine(object).utf8)
        let metadata = NWProtocolWebSocket.Metadata(opcode: .text)
        let context = NWConnection.ContentContext(identifier: "json", metadata: [metadata])
        inFlight += 1
        lastOutboundAt = Date()
        connection.send(content: data, contentContext: context, isComplete: true,
                        completion: .contentProcessed { [weak self] _ in
                            self?.inFlight = max(0, (self?.inFlight ?? 1) - 1)
                        })
    }

    /// 音频二进制帧；背压超限时丢帧并计数。
    func sendAudio(_ data: Data) {
        guard !isClosed else { return }
        if inFlight >= Self.maxInFlightAudio {
            framesDropped += 1
            return
        }
        let metadata = NWProtocolWebSocket.Metadata(opcode: .binary)
        let context = NWConnection.ContentContext(identifier: "audio", metadata: [metadata])
        inFlight += 1
        connection.send(content: data, contentContext: context, isComplete: true,
                        completion: .contentProcessed { [weak self] _ in
                            self?.inFlight = max(0, (self?.inFlight ?? 1) - 1)
                        })
    }
}

/// 127.0.0.1 上的 WebSocket 服务：Origin 校验、last-wins 单控制连接、音频扇出。
final class CaptureServer {
    let port: UInt16
    private let originPolicy: OriginPolicy
    private let queue = DispatchQueue(label: "capture-server")
    private var listener: NWListener?
    private var connections: [ClientConnection] = []
    /// 当前控制连接（最后一个完成握手的连接，会踢掉前一个）。
    private(set) var controlConnection: ClientConnection?
    private var heartbeatTimer: DispatchSourceTimer?

    /// 收到控制 JSON 文本（已在 serverQueue 上）。
    var onCommand: ((ClientConnection, [String: Any]) -> Void)?
    /// 控制连接出现（新页面连接/接管旧页面），携带该连接以便定向发送 hello。
    var onControlConnected: ((ClientConnection) -> Void)?
    /// 所有连接都断开（无客户端时应停止采集）。
    var onAllDisconnected: (() -> Void)?
    /// 连接被新连接取代。
    var onControlReplaced: (() -> Void)?

    init(port: UInt16, originPolicy: OriginPolicy) {
        self.port = port
        self.originPolicy = originPolicy
    }

    func start() throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        guard let endpointPort = NWEndpoint.Port(rawValue: port) else {
            throw CollectorError.invalidParams("非法端口: \(port)")
        }
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: .ipv4(.loopback), port: endpointPort)

        let wsOptions = NWProtocolWebSocket.Options()
        wsOptions.autoReplyPing = true
        wsOptions.maximumMessageSize = 1 << 20
        let policy = originPolicy
        wsOptions.setClientRequestHandler(queue) { subprotocols, headers in
            let origin = headers.first { $0.name.caseInsensitiveCompare("origin") == .orderedSame }?.value
            let allowed = policy.allows(origin: origin)
            if !allowed {
                FileHandle.standardError.write(Data("origin rejected: \(origin ?? "<missing>")\n".utf8))
            }
            return NWProtocolWebSocket.Response(status: allowed ? .accept : .reject,
                                                 subprotocol: subprotocols.first,
                                                 additionalHeaders: nil)
        }
        params.defaultProtocolStack.applicationProtocols.insert(wsOptions, at: 0)

        let listener = try NWListener(using: params)
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }

        // 绑定失败（端口被占用等）是异步状态回调，同步等待启动结果，
        // 让调用方能以异常感知并退出。
        let startup = DispatchSemaphore(value: 0)
        var startupError: Error?
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready:
                startup.signal()
            case .failed(let error):
                startupError = error
                startup.signal()
            default:
                break
            }
        }
        listener.start(queue: queue)
        let waitResult = startup.wait(timeout: .now() + 5)
        if waitResult == .timedOut {
            throw CollectorError.startFailed("WebSocket 服务启动超时（端口 \(port)）。")
        }
        if let startupError {
            throw CollectorError.startFailed("WebSocket 服务启动失败（端口 \(port) 被占用？）: \(startupError.localizedDescription)")
        }
        listener.stateUpdateHandler = { state in
            if case .failed(let error) = state {
                FileHandle.standardError.write(Data("listener failed: \(error)\n".utf8))
            }
        }
        self.listener = listener

        startHeartbeat()
    }

    private func accept(_ connection: NWConnection) {
        let client = ClientConnection(connection: connection,
                                      onReady: { [weak self] conn in
                                          self?.promoteToControl(conn)
                                      },
                                      onText: { [weak self] conn, text in
                                          self?.handleText(conn, text)
                                      },
                                      onGone: { [weak self] conn in
                                          self?.handleGone(conn)
                                      })
        connections.append(client)
        client.start(on: queue)
    }

    /// last-wins：新连接就绪后取代旧控制连接（页面刷新/双开标签页不会死锁）。
    private func promoteToControl(_ client: ClientConnection) {
        let previous = controlConnection
        controlConnection = client
        if let previous, previous !== client {
            onControlReplaced?()
            previous.cancel()
        }
        onControlConnected?(client)
    }

    private func handleText(_ conn: ClientConnection, _ text: String) {
        guard let data = text.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            conn.sendText(["type": "error", "code": "invalid-params", "message": "无法解析 JSON。"])
            return
        }
        guard conn === controlConnection || controlConnection == nil else { return }
        onCommand?(conn, object)
    }

    private func handleGone(_ conn: ClientConnection) {
        connections.removeAll { $0 === conn }
        if conn === controlConnection {
            controlConnection = connections.first
            if controlConnection == nil {
                onAllDisconnected?()
            } else if let next = controlConnection {
                onControlConnected?(next)
            }
        }
    }

    // ---- 出站 ----
    // 这些方法可能被任意队列调用（SCK 回调、Task 等），内部统一跳回 serverQueue。

    func broadcastText(_ object: [String: Any]) {
        queue.async { [weak self] in
            guard let self else { return }
            for conn in self.connections where !conn.isClosed {
                conn.sendText(object)
            }
        }
    }

    func sendText(to conn: ClientConnection, _ object: [String: Any]) {
        queue.async {
            conn.sendText(object)
        }
    }

    func broadcastAudio(_ data: Data) {
        queue.async { [weak self] in
            guard let self else { return }
            for conn in self.connections where !conn.isClosed {
                conn.sendAudio(data)
            }
        }
    }

    /// 各连接累计丢帧数（线程安全读取）。
    func totalFramesDropped() -> Int {
        queue.sync { connections.reduce(0) { $0 + $1.framesDropped } }
    }

    var hasClients: Bool {
        !connections.filter { !$0.isClosed }.isEmpty
    }

    /// 30s 无出站流量时向控制连接发一条 JSON ping，探测半死连接。
    private func startHeartbeat() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 30, repeating: 30)
        timer.setEventHandler { [weak self] in
            guard let self, let control = self.controlConnection, !control.isClosed else { return }
            if Date().timeIntervalSince(control.lastOutboundAt) >= 30 {
                control.sendText(["type": "ping"])
            }
        }
        timer.resume()
        heartbeatTimer = timer
    }
}
