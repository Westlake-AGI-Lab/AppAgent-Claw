import AppKit
import Foundation

let windowTitle = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "AppAgent-Claw Recorder"

final class OverlayController: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var readyWindow: NSWindow!
    private var hudWindow: NSWindow!
    private let readyContentView = NSView(frame: .zero)
    private let hudContentView = NSView(frame: .zero)
    private let readyStatusLabel = NSTextField(labelWithString: "点击开始录制")
    private let hudStatusLabel = NSTextField(labelWithString: "录制中，ESC 停止")
    private let startButton = NSButton(title: "开始录制", target: nil, action: nil)
    private var inputBuffer = ""
    private var isRecordingMode = false
    private var isProgrammaticClose = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        _ = notification
        configureReadyWindow()
        configureHudWindow()
        showReadyWindow()
        startReadingCommands()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        _ = sender
        return false
    }

    func windowWillClose(_ notification: Notification) {
        _ = notification
        guard !isProgrammaticClose else { return }
        emit(isRecordingMode ? "closed" : "cancel")
        DispatchQueue.main.async {
            NSApp.terminate(nil)
        }
    }

    @objc private func handleStartButton() {
        readyStatusLabel.stringValue = "正在启动录制..."
        startButton.isEnabled = false
        emit("start")
    }

    private func configureReadyWindow() {
        readyWindow = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: 140),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        readyWindow.title = windowTitle
        readyWindow.level = .floating
        readyWindow.center()
        readyWindow.delegate = self
        readyWindow.isReleasedWhenClosed = false
        readyWindow.contentView = readyContentView
        readyWindow.collectionBehavior = [.canJoinAllSpaces]

        readyContentView.frame = NSRect(x: 0, y: 0, width: 320, height: 140)
        readyContentView.wantsLayer = true

        readyStatusLabel.alignment = .center
        readyStatusLabel.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
        readyStatusLabel.frame = NSRect(x: 20, y: 78, width: 280, height: 24)
        readyContentView.addSubview(readyStatusLabel)

        startButton.target = self
        startButton.action = #selector(handleStartButton)
        startButton.frame = NSRect(x: 85, y: 28, width: 150, height: 32)
        readyContentView.addSubview(startButton)

        positionReadyWindow()
    }

    private func configureHudWindow() {
        hudWindow = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 240, height: 56),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        hudWindow.level = .statusBar
        hudWindow.delegate = self
        hudWindow.isReleasedWhenClosed = false
        hudWindow.isOpaque = false
        hudWindow.hasShadow = true
        hudWindow.backgroundColor = NSColor.windowBackgroundColor.withAlphaComponent(0.95)
        hudWindow.collectionBehavior = [.canJoinAllSpaces, .stationary]
        hudWindow.contentView = hudContentView

        hudContentView.frame = NSRect(x: 0, y: 0, width: 240, height: 56)
        hudContentView.wantsLayer = true
        hudContentView.layer?.cornerRadius = 14
        hudContentView.layer?.masksToBounds = true

        hudStatusLabel.alignment = .center
        hudStatusLabel.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        hudStatusLabel.frame = NSRect(x: 12, y: 16, width: 216, height: 22)
        hudContentView.addSubview(hudStatusLabel)

        positionHudWindow()
    }

    private func showReadyWindow() {
        isRecordingMode = false
        readyStatusLabel.stringValue = "点击开始录制"
        startButton.isEnabled = true
        positionReadyWindow()
        hudWindow.orderOut(nil)
        readyWindow.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func showRecordingHud(text: String) {
        isRecordingMode = true
        hudStatusLabel.stringValue = text
        positionHudWindow()
        readyWindow.orderOut(nil)
        hudWindow.orderFrontRegardless()
    }

    private func primaryScreen() -> NSScreen? {
        return NSScreen.screens.first ?? NSScreen.main
    }

    private func positionReadyWindow() {
        guard let screen = primaryScreen() else { return }
        let visibleFrame = screen.visibleFrame
        let windowSize = readyWindow.frame.size
        let x = visibleFrame.midX - (windowSize.width / 2)
        let y = visibleFrame.midY - (windowSize.height / 2)
        readyWindow.setFrameOrigin(NSPoint(x: x, y: y))
    }

    private func positionHudWindow() {
        guard let screen = primaryScreen() else { return }
        let visibleFrame = screen.visibleFrame
        let windowSize = hudWindow.frame.size
        let x = visibleFrame.maxX - windowSize.width - 16
        let y = visibleFrame.maxY - windowSize.height - 16
        hudWindow.setFrameOrigin(NSPoint(x: x, y: y))
    }

    private func startReadingCommands() {
        FileHandle.standardInput.readabilityHandler = { [weak self] handle in
            guard let self else { return }
            let data = handle.availableData
            guard !data.isEmpty, let chunk = String(data: data, encoding: .utf8) else {
                return
            }
            self.inputBuffer.append(chunk)
            while let newlineRange = self.inputBuffer.range(of: "\n") {
                let line = String(self.inputBuffer[..<newlineRange.lowerBound])
                self.inputBuffer.removeSubrange(...newlineRange.lowerBound)
                DispatchQueue.main.async {
                    self.handleCommand(line)
                }
            }
        }
    }

    private func handleCommand(_ rawLine: String) {
        if rawLine == "hide" {
            hudWindow.orderOut(nil)
            return
        }
        if rawLine == "show" {
            if isRecordingMode {
                hudWindow.orderFrontRegardless()
            }
            return
        }
        if rawLine == "close" {
            closeAllWindows()
            return
        }
        if rawLine.hasPrefix("recording\t") {
            let text = String(rawLine.dropFirst("recording\t".count))
            showRecordingHud(text: text)
            return
        }
        if rawLine.hasPrefix("status\t") {
            let text = String(rawLine.dropFirst("status\t".count))
            if isRecordingMode {
                hudStatusLabel.stringValue = text
            } else {
                readyStatusLabel.stringValue = text
            }
        }
    }

    private func closeAllWindows() {
        isProgrammaticClose = true
        FileHandle.standardInput.readabilityHandler = nil
        readyWindow.orderOut(nil)
        hudWindow.orderOut(nil)
        readyWindow.close()
        hudWindow.close()
        NSApp.terminate(nil)
    }

    private func emit(_ message: String) {
        guard let data = "\(message)\n".data(using: .utf8) else {
            return
        }
        FileHandle.standardOutput.write(data)
    }
}

let app = NSApplication.shared
let delegate = OverlayController()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
