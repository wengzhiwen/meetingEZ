import AppKit
import Darwin
import Foundation
import SwiftUI

/// 菜单栏外壳 + 只读状态面板。无任何配置 GUI——一切采集配置由 web 端通过 WebSocket 下发。
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let options: ServeOptions
    private var server: CaptureServer?
    private var service: CollectorService?

    private var statusItem: NSStatusItem?
    private var stateMenuItem: NSMenuItem?
    private var clientMenuItem: NSMenuItem?
    private var restartMenuItem: NSMenuItem?
    private var statusModel: CollectorStatusModel?
    private var statusPanel: NSPanel?

    private var clientConnected = false
    private var capturingDetail = ""
    private var isCapturing = false

    init(options: ServeOptions) {
        self.options = options
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildStatusBarItem()
        registerObservers()

        let source: CaptureSource = options.mockAudio ? MockAudioSource() : SystemAudioTap()
        let server = CaptureServer(port: options.port,
                                   originPolicy: OriginPolicy(extraAllowed: options.extraOrigins))
        do {
            try server.start()
        } catch {
            let message = (error as? CollectorError)?.message ?? error.localizedDescription
            presentFatalError("启动 WebSocket 服务失败\n\n\(message)")
            return
        }
        let service = CollectorService(server: server, source: source)
        service.run()
        self.server = server
        self.service = service

        let model = CollectorStatusModel()
        model.start(port: options.port, source: source)
        statusModel = model

        updateMenu()
    }

    // ---- 菜单栏 ----

    private func buildStatusBarItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = item.button {
            button.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "MeetingEZ Capture")
            button.image?.isTemplate = true
        }
        let menu = NSMenu()

        let panel = NSMenuItem(title: "显示状态面板", action: #selector(toggleStatusPanel), keyEquivalent: "")
        panel.target = self
        menu.addItem(panel)

        let state = NSMenuItem(title: "启动中…", action: nil, keyEquivalent: "")
        state.isEnabled = false
        menu.addItem(state)
        stateMenuItem = state

        let endpoint = NSMenuItem(title: "ws://127.0.0.1:\(options.port)", action: nil, keyEquivalent: "")
        endpoint.isEnabled = false
        menu.addItem(endpoint)

        let client = NSMenuItem(title: "客户端：未连接", action: nil, keyEquivalent: "")
        client.isEnabled = false
        menu.addItem(client)
        clientMenuItem = client

        menu.addItem(.separator())

        let restart = NSMenuItem(title: "重启采集器（使权限生效）", action: #selector(relaunch), keyEquivalent: "")
        restart.target = self
        restart.isHidden = true
        menu.addItem(restart)
        restartMenuItem = restart

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quit)

        item.menu = menu
        statusItem = item
    }

    private func registerObservers() {
        let center = NotificationCenter.default
        center.addObserver(forName: .captureStateDidChange, object: nil, queue: .main) { [weak self] note in
            guard let self else { return }
            self.isCapturing = note.userInfo?["capturing"] as? Bool ?? false
            self.capturingDetail = note.userInfo?["detail"] as? String ?? ""
            self.updateMenu()
        }
        center.addObserver(forName: .captureClientStateDidChange, object: nil, queue: .main) { [weak self] note in
            guard let self else { return }
            self.clientConnected = note.userInfo?["connected"] as? Bool ?? false
            self.updateMenu()
        }
        center.addObserver(forName: .capturePermissionNeedsRestart, object: nil, queue: .main) { [weak self] _ in
            self?.restartMenuItem?.isHidden = false
            self?.updateMenu()
        }
    }

    // ---- 状态面板 ----

    @objc private func toggleStatusPanel() {
        if let panel = statusPanel, panel.isVisible {
            panel.orderOut(nil)
            return
        }
        if statusPanel == nil, let model = statusModel {
            let panel = NSPanel(
                contentRect: NSRect(x: 0, y: 0, width: 320, height: 400),
                styleMask: [.titled, .closable, .nonactivatingPanel],
                backing: .buffered, defer: false)
            panel.title = "MeetingEZ Capture"
            panel.contentView = NSHostingView(rootView: StatusPanelView(model: model))
            panel.isFloatingPanel = true
            panel.level = .floating
            panel.isReleasedWhenClosed = false
            panel.center()
            statusPanel = panel
        }
        statusPanel?.makeKeyAndOrderFront(nil)
    }

    private func updateMenu() {
        statusItem?.button?.image = NSImage(
            systemSymbolName: isCapturing ? "waveform.badge.mic" : "waveform",
            accessibilityDescription: "MeetingEZ Capture")
        statusItem?.button?.image?.isTemplate = true

        if isCapturing {
            stateMenuItem?.title = "采集中：\(capturingDetail.isEmpty ? "未知目标" : capturingDetail)"
        } else {
            stateMenuItem?.title = "空闲（等待 web 端指令）"
        }
        clientMenuItem?.title = clientConnected ? "客户端：已连接" : "客户端：未连接"
    }

    private func presentFatalError(_ message: String) {
        statusItem?.button?.image = NSImage(systemSymbolName: "exclamationmark.triangle",
                                            accessibilityDescription: "MeetingEZ Capture 错误")
        let alert = NSAlert()
        alert.messageText = "MeetingEZ Capture"
        alert.informativeText = message
        alert.addButton(withTitle: "退出")
        alert.runModal()
        NSApp.terminate(nil)
    }

    // ---- 重启（使 TCC 权限生效） ----
    // 必须原样带上启动参数（如 --allow-origin），否则重启后白名单丢失。

    @objc private func relaunch() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        let bundleURL = Bundle.main.bundleURL
        if bundleURL.pathExtension == "app" {
            // .app 形态：OpenConfiguration 透传参数后退出；
            // 新实例的端口绑定重试会等旧实例释放 17642。
            let configuration = NSWorkspace.OpenConfiguration()
            configuration.arguments = arguments
            NSWorkspace.shared.openApplication(at: bundleURL, configuration: configuration)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                NSApp.terminate(nil)
            }
            return
        }
        // 裸二进制形态：posix_spawn 自身并退出。
        let executable = CommandLine.arguments[0]
        var cArguments: [UnsafeMutablePointer<CChar>?] = CommandLine.arguments.map { strdup($0) }
        cArguments.append(nil)
        defer {
            for pointer in cArguments where pointer != nil {
                free(pointer)
            }
        }
        var processId: pid_t = 0
        let result = posix_spawn(&processId, executable, nil, nil, cArguments, environ)
        if result != 0 {
            FileHandle.standardError.write(Data("重启失败: posix_spawn \(result)\n".utf8))
            return
        }
        exit(0)
    }
}
