import AppKit
import WebKit
import UniformTypeIdentifiers
import Sparkle

// Native macOS window; the existing Python synchronizer runs on 127.0.0.1 only.

// WKWebView fills the transparent titlebar and consumes mouse events.
// A real AppKit overlay is needed for a reliable draggable header.
final class ReSyncWindowDragHandle: NSView {
    override var isOpaque: Bool { false }

    override func mouseDown(with event: NSEvent) {
        window?.performDrag(with: event)
    }
}
final class ReSyncAppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var messageView: NSTextField!
    private var backend: Process?
    private var endpoint: URL?
    private var outputBuffer = ""
    private var closing = false
    private var updaterController: SPUStandardUpdaterController?
    private var pendingBridgeJobs: [String] = []
    private var bridgeWorkers: [Process] = []
    private var launchDate = Date()
    private var bridgeOnlyLaunch = false
    private var appDidFinishLaunching = false


    private var bundleRoot: URL {
        if let root = ProcessInfo.processInfo.environment["RESYNC_BUNDLE_ROOT"] {
            return URL(fileURLWithPath: root, isDirectory: true)
        }
        return Bundle.main.bundleURL
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        appDidFinishLaunching = true
        // macOS may deliver a custom URL before the app has fully launched.
        // In this case run only the background worker, with no ReSync window.
        if !pendingBridgeJobs.isEmpty {
            bridgeOnlyLaunch = true
            NSApp.setActivationPolicy(.accessory)
            let jobs = pendingBridgeJobs
            pendingBridgeJobs.removeAll()
            jobs.forEach { startBridgeJob(at: $0) }
            return
        }
        NSApp.setActivationPolicy(.regular)
        let iconFile = bundleRoot.appendingPathComponent("Contents/Resources/ReSyncIcon.png")
        if let icon = NSImage(contentsOf: iconFile) { NSApp.applicationIconImage = icon }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1150, height: 790),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                              backing: .buffered, defer: false)
        self.window = window
        window.title = "ReSync"
        window.minSize = NSSize(width: 860, height: 630)
        window.center()
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(calibratedWhite: 0.06, alpha: 1)

        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        config.userContentController.add(self, name: "resync")
        config.userContentController.addUserScript(WKUserScript(source: "document.documentElement.classList.add('native-mac');", injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = self
        // Without a WKUIDelegate, file input elements cannot present
        // the native Open panel reliably inside our embedded WKWebView.
        view.uiDelegate = self
        self.webView = view
        view.translatesAutoresizingMaskIntoConstraints = false
        view.isHidden = true
        window.contentView?.addSubview(view)
        if let container = window.contentView {
            NSLayoutConstraint.activate([
                view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
                view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                view.topAnchor.constraint(equalTo: container.topAnchor),
                view.bottomAnchor.constraint(equalTo: container.bottomAnchor)
            ])
        }
        let status = NSTextField(labelWithString: "Iniciando ReSync…")
        status.font = NSFont.systemFont(ofSize: 18, weight: .medium)
        status.textColor = .white
        status.alignment = .center
        status.translatesAutoresizingMaskIntoConstraints = false
        self.messageView = status
        window.contentView?.addSubview(status)
        if let container = window.contentView {
            NSLayoutConstraint.activate([
                status.centerXAnchor.constraint(equalTo: container.centerXAnchor),
                status.centerYAnchor.constraint(equalTo: container.centerYAnchor)
            ])
        }
        // A transparent native drag region, above the web view, makes the
        // application's header draggable without intercepting the macOS
        // traffic lights (left) or the Preferences button (right).
        let dragHandle = ReSyncWindowDragHandle(frame: .zero)
        dragHandle.translatesAutoresizingMaskIntoConstraints = false
        dragHandle.toolTip = "Arraste para mover a janela do ReSync"
        if let container = window.contentView {
            container.addSubview(dragHandle)
            NSLayoutConstraint.activate([
                dragHandle.topAnchor.constraint(equalTo: container.topAnchor),
                dragHandle.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 105),
                dragHandle.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -78),
                dragHandle.heightAnchor.constraint(equalToConstant: 68)
            ])
        }

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        configureUpdater()
        startBackend()
    }

    // Sparkle manages downloading, cryptographic verification, replacement
    // and restart. It is intentionally disabled until the release feed and
    // EdDSA public key have been configured for this app bundle.
    // Premiere UXP can launch a custom URL scheme after user consent.
    // A JSON job file and an XML file are passed through a folder chosen
    // by the user. No insecure HTTP localhost requests are required.
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
                  parts.scheme?.lowercased() == "resync",
                  parts.host?.lowercased() == "sync",
                  let path = parts.queryItems?.first(where: { $0.name == "job" })?.value,
                  path.hasPrefix("/") else { continue }
            let file = URL(fileURLWithPath: path).standardizedFileURL
            let home = FileManager.default.homeDirectoryForCurrentUser.standardizedFileURL.path
            let base = file.deletingLastPathComponent().standardizedFileURL.path
            // Premiere editors frequently use external SSDs (/Volumes).
            // Accept only ordinary local user/workspace folders.
            let allowed = [home, "/Volumes", "/private/var/folders", "/tmp"]
            guard allowed.contains(where: { base == $0 || base.hasPrefix($0 + "/") }),
                  file.lastPathComponent.range(
                    of: #"^resync_[0-9a-f]{24}\.job\.json$"#,
                    options: .regularExpression) != nil,
                  FileManager.default.fileExists(atPath: file.path) else { continue }
            if !appDidFinishLaunching {
                pendingBridgeJobs.append(file.path)
            } else {
                if Date().timeIntervalSince(launchDate) < 8 && bridgeWorkers.isEmpty {
                    bridgeOnlyLaunch = true
                    window?.orderOut(nil)
                    NSApp.setActivationPolicy(.accessory)
                }
                startBridgeJob(at: file.path)
            }
        }
    }

    private func startBridgeJob(at path: String) {
        let resources = bundleRoot.appendingPathComponent("Contents/Resources", isDirectory: true)
        let worker = resources.appendingPathComponent("backend/resync-server/resync-server")
        guard FileManager.default.isExecutableFile(atPath: worker.path) else { return }
        let process = Process()
        process.executableURL = worker
        process.arguments = ["--resync-job", path]
        process.currentDirectoryURL = worker.deletingLastPathComponent()
        var environment = ProcessInfo.processInfo.environment
        let tools = resources.appendingPathComponent("bin").path
        environment["PATH"] = tools + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (environment["PATH"] ?? "")
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        bridgeWorkers.append(process)
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.bridgeWorkers.removeAll { $0 === finished }
                if self.bridgeOnlyLaunch && self.bridgeWorkers.isEmpty {
                    NSApp.terminate(nil)
                }
            }
        }
        do { try process.run() }
        catch {
            bridgeWorkers.removeAll { $0 === process }
            // The UXP panel times out with a useful error if the worker fails.
            NSLog("ReSync bridge could not start: %@", error.localizedDescription)
            if bridgeOnlyLaunch && bridgeWorkers.isEmpty { NSApp.terminate(nil) }
        }
    }

    private func configureUpdater() {
        guard let key = Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String,
              !key.isEmpty,
              let feed = Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String,
              let url = URL(string: feed), url.scheme == "https" else { return }
        updaterController = SPUStandardUpdaterController(
            startingUpdater: true,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )
    }

    private func checkForUpdates() {
        guard let controller = updaterController else {
            let alert = NSAlert()
            alert.messageText = "Atualizações em preparação"
            alert.informativeText = "O verificador já está integrado ao ReSync, mas falta configurar o canal de versões assinadas no GitHub. O aplicativo continuará funcionando normalmente."
            alert.addButton(withTitle: "OK")
            alert.beginSheetModal(for: window)
            return
        }
        controller.checkForUpdates(nil)
    }

    private func startBackend() {
        let resources = bundleRoot.appendingPathComponent("Contents/Resources", isDirectory: true)
        let proc = Process()
        var env = ProcessInfo.processInfo.environment
        env["RESYNC_EMBEDDED"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        let tools = resources.appendingPathComponent("bin", isDirectory: true).path
        env["PATH"] = tools + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
        // Prefer the bundled PyInstaller server, so the standalone .app does
        // not require Python or a manually configured environment variable.
        let bundledServer = resources.appendingPathComponent("backend/resync-server/resync-server")
        if let executable = env["RESYNC_BACKEND_EXECUTABLE"], !executable.isEmpty {
            proc.executableURL = URL(fileURLWithPath: executable)
        } else if FileManager.default.isExecutableFile(atPath: bundledServer.path) {
            proc.executableURL = bundledServer
            proc.currentDirectoryURL = bundledServer.deletingLastPathComponent()
        } else {
            // Developer/launcher fallback: run the source with local Python.
            let source = resources.appendingPathComponent("source", isDirectory: true)
            guard let python = env["RESYNC_PYTHON"], !python.isEmpty else {
                showError("O motor integrado não foi encontrado. Recompile o ReSync ou confira os arquivos do aplicativo.")
                return
            }
            proc.executableURL = URL(fileURLWithPath: python)
            proc.arguments = [source.appendingPathComponent("app.py").path]
            proc.currentDirectoryURL = source
        }
        proc.environment = env
        let pipe = Pipe()
        proc.standardOutput = pipe
        proc.standardError = pipe
        self.backend = proc
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { return }
            let string = String(data: data, encoding: .utf8) ?? ""
            DispatchQueue.main.async { self?.processOutput(string) }
        }
        do {
            try proc.run()
        } catch {
            showError("Não foi possível iniciar o motor de sincronização: \(error.localizedDescription)")
            return
        }
        proc.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, !self.closing, self.endpoint == nil else { return }
                self.showError("O motor de sincronização foi encerrado antes de iniciar. Consulte o arquivo de log do aplicativo.")
            }
        }
    }

    private func processOutput(_ text: String) {
        outputBuffer += text
        let parts = outputBuffer.components(separatedBy: "\n")
        outputBuffer = parts.last ?? ""
        for line in parts.dropLast() {
            if endpoint == nil, let range = line.range(of: "http://127.0.0.1:"),
               let url = URL(string: String(line[range.lowerBound...]).trimmingCharacters(in: .whitespacesAndNewlines)) {
                endpoint = url
                messageView.isHidden = true
                webView.isHidden = false
                webView.load(URLRequest(url: url))
            }
        }
    }

    private func showError(_ description: String) {
        messageView.stringValue = "Não foi possível abrir o ReSync"
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "ReSync — problema ao iniciar"
        alert.informativeText = description
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window)
    }

    // Opens the macOS file chooser for <input type="file"> in the ReSync UI.
    // WKWebView does not provide the default browser file-picker experience.
    // The completion handler hands the selected URL back to WebKit; the
    // existing JavaScript file-change handler continues importing the XML.
    func webView(_ webView: WKWebView,
                 runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType.xml]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.prompt = "Selecionar XML"
        panel.message = "Escolha um XML exportado pelo Premiere Pro (Final Cut Pro 7 XML)."
        panel.beginSheetModal(for: window) { response in
            completionHandler(response == .OK ? panel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" { decisionHandler(.allow); return }
        if let origin = endpoint, url.scheme == origin.scheme, url.host == origin.host, url.port == origin.port {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "resync",
              let body = message.body as? [String: Any],
              let action = body["action"] as? String else { return }
        if action == "checkUpdates" {
            checkForUpdates()
            return
        }
        guard action == "save",
              let route = body["route"] as? String,
              route == "/api/download" || route == "/api/csv",
              let endpoint = endpoint else { return }
        let url = endpoint.appendingPathComponent(String(route.dropFirst()))
        URLSession.shared.dataTask(with: url) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard let data = data, let response = response as? HTTPURLResponse,
                      response.statusCode == 200 else {
                    self.showError("Não foi possível obter o arquivo exportado: \(error?.localizedDescription ?? "erro de exportação")")
                    return
                }
                let filename = response.suggestedFilename ?? (route == "/api/csv" ? "ReSync_relatorio.csv" : "ReSync.xml")
                let panel = NSSavePanel()
                panel.nameFieldStringValue = filename
                panel.canCreateDirectories = true
                panel.beginSheetModal(for: self.window) { result in
                    if result == .OK, let destination = panel.url {
                        do { try data.write(to: destination, options: .atomic) }
                        catch { self.showError("Não foi possível salvar o arquivo: \(error.localizedDescription)") }
                    }
                }
            }
        }.resume()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        closing = true
        if let process = backend, process.isRunning { process.terminate() }
    }
}

let app = NSApplication.shared
let delegate = ReSyncAppDelegate()
app.delegate = delegate
app.run()
