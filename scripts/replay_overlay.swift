import AppKit
import Foundation

let windowTitle = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "AppAgent-Claw Replay"

final class OverlayController: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var hudWindow: NSWindow!
    private let hudContentView = NSView(frame: .zero)
    private let hudStatusLabel = NSTextField(labelWithString: "准备回放")
    private var inputBuffer = ""
    private var isProgrammaticClose = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        _ = notification
        configureHudWindow()
        showHud()
        startReadingCommands()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        _ = sender
        return false
    }

    func windowWillClose(_ notification: Notification) {
        _ = notification
        guard !isProgrammaticClose else { return }
        DispatchQueue.main.async {
            NSApp.terminate(nil)
        }
    }

    private func configureHudWindow() {
        hudWindow = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 64),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        hudWindow.title = windowTitle
        hudWindow.level = .statusBar
        hudWindow.delegate = self
        hudWindow.isReleasedWhenClosed = false
        hudWindow.isOpaque = false
        hudWindow.hasShadow = true
        hudWindow.backgroundColor = NSColor.windowBackgroundColor.withAlphaComponent(0.95)
        hudWindow.collectionBehavior = [.canJoinAllSpaces, .stationary]
        hudWindow.ignoresMouseEvents = true
        hudWindow.contentView = hudContentView

        hudContentView.frame = NSRect(x: 0, y: 0, width: 420, height: 64)
        hudContentView.wantsLayer = true
        hudContentView.layer?.cornerRadius = 14
        hudContentView.layer?.masksToBounds = true

        hudStatusLabel.alignment = .center
        hudStatusLabel.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
        hudStatusLabel.lineBreakMode = .byTruncatingTail
        hudStatusLabel.frame = NSRect(x: 12, y: 21, width: 396, height: 22)
        hudContentView.addSubview(hudStatusLabel)

        positionHudWindow()
    }

    private func showHud() {
        positionHudWindow()
        hudWindow.orderFrontRegardless()
    }

    private func primaryScreen() -> NSScreen? {
        return NSScreen.screens.first ?? NSScreen.main
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
            showHud()
            return
        }
        if rawLine == "close" {
            closeWindow()
            return
        }
        if rawLine.hasPrefix("status\t") {
            let text = String(rawLine.dropFirst("status\t".count))
            hudStatusLabel.stringValue = text
            showHud()
        }
    }

    private func closeWindow() {
        isProgrammaticClose = true
        FileHandle.standardInput.readabilityHandler = nil
        hudWindow.orderOut(nil)
        hudWindow.close()
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = OverlayController()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
