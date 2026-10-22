// ArbitrageOS.app: Dock icon, menu bar item and a native window around the
// local dashboard. Starts the Python server on launch and stops it on quit.
import Cocoa
import WebKit

let port = 8787
let baseURL = URL(string: "http://127.0.0.1:\(port)/")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var statusItem: NSStatusItem!
    var server: Process?
    var root: URL!
    var statusLine: NSMenuItem!

    // MARK: launch
    func applicationDidFinishLaunching(_ note: Notification) {
        let rootPath = (Bundle.main.object(forInfoDictionaryKey: "ArbOSRoot") as? String)
            ?? (NSHomeDirectory() + "/Developer/ArbitrageOS")
        root = URL(fileURLWithPath: rootPath)
        buildMainMenu()
        buildStatusItem()
        buildWindow()
        startServerIfNeeded()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ note: Notification) {
        if let s = server, s.isRunning { s.terminate() }
    }

    // MARK: server
    func serverIsUp(_ done: @escaping (Bool) -> Void) {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/settings"))
        req.timeoutInterval = 1.0
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            done((resp as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }

    func startServerIfNeeded() {
        serverIsUp { up in
            DispatchQueue.main.async {
                if up { self.setStatus("Running"); self.loadDashboard(); return }
                self.launchServer()
            }
        }
    }

    func runPython(_ args: [String], wait: Bool) -> Process? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = ["-m", "arbos"] + args
        p.currentDirectoryURL = root
        let logDir = root.appendingPathComponent("data")
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        let logURL = logDir.appendingPathComponent("server.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if let h = try? FileHandle(forWritingTo: logURL) {
            h.seekToEndOfFile()
            p.standardOutput = h
            p.standardError = h
        }
        do { try p.run() } catch {
            setStatus("Python not found")
            showError("ArbitrageOS needs Python 3. Run xcode-select --install in Terminal, then open ArbitrageOS again.")
            return nil
        }
        if wait { p.waitUntilExit() }
        return p
    }

    func launchServer() {
        setStatus("Starting…")
        DispatchQueue.global().async {
            let db = self.root.appendingPathComponent("data/arbos.db")
            if !FileManager.default.fileExists(atPath: db.path) {
                _ = self.runPython(["demo"], wait: true)  // first run: sample data
            }
            let p = self.runPython(["serve"], wait: false)
            DispatchQueue.main.async { self.server = p }
            self.waitForServer(tries: 40)
        }
    }

    func waitForServer(tries: Int) {
        serverIsUp { up in
            if up {
                DispatchQueue.main.async { self.setStatus("Running"); self.loadDashboard() }
            } else if tries > 0 {
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.25) { self.waitForServer(tries: tries - 1) }
            } else {
                DispatchQueue.main.async {
                    self.setStatus("Server did not start")
                    self.showError("The ArbitrageOS server didn't start. Details are in data/server.log inside the ArbitrageOS folder.")
                }
            }
        }
    }

    func post(_ path: String, done: @escaping (String) -> Void) {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("1", forHTTPHeaderField: "X-ArbOS")
        req.httpBody = "{}".data(using: .utf8)
        URLSession.shared.dataTask(with: req) { data, resp, err in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let body = data.flatMap { String(data: $0, encoding: .utf8) } ?? (err?.localizedDescription ?? "")
            DispatchQueue.main.async { done(code == 200 ? "ok" : body) }
        }.resume()
    }

    // MARK: window
    func buildWindow() {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: cfg)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 880),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "ArbitrageOS"
        window.minSize = NSSize(width: 760, height: 520)
        window.contentView = webView
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("ArbitrageOSMain")
        if window.frame.origin == .zero { window.center() }
        let html = "<html><body style='font:15px -apple-system;color:#4E5E5C;background:#EEF1EF;display:grid;place-items:center;height:90vh'>Starting ArbitrageOS…</body></html>"
        webView.loadHTMLString(html, baseURL: nil)
        showWindow()
    }

    func loadDashboard(_ hash: String = "") {
        let url = URL(string: baseURL.absoluteString + (hash.isEmpty ? "" : "#/\(hash)"))!
        if let cur = webView.url, cur.host == "127.0.0.1", !hash.isEmpty {
            webView.evaluateJavaScript("location.hash = '#/\(hash)'", completionHandler: nil)
        } else {
            webView.load(URLRequest(url: url))
        }
    }

    @objc func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func open(_ hash: String) {
        showWindow()
        loadDashboard(hash)
    }

    // external links, labels, downloads and receipts open in the default browser
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.allow) }
        let local = url.host == "127.0.0.1" || url.host == "localhost"
        let p = url.path
        if url.scheme == "about" || (local && !(p.hasPrefix("/labels") || p.hasPrefix("/export") || p.hasPrefix("/receipt"))) {
            return decisionHandler(.allow)
        }
        if url.scheme == "http" || url.scheme == "https" { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { r in completionHandler(r == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = message
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = message
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        a.accessoryView = field
        a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in completionHandler(r == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    func showError(_ text: String) {
        let a = NSAlert(); a.messageText = "ArbitrageOS"; a.informativeText = text
        a.alertStyle = .warning
        a.runModal()
    }

    // MARK: menu bar
    func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let b = statusItem.button {
            let img = NSImage(systemSymbolName: "tag.fill", accessibilityDescription: "ArbitrageOS")
            img?.isTemplate = true
            b.image = img
            b.toolTip = "ArbitrageOS"
        }
        let m = NSMenu()
        statusLine = NSMenuItem(title: "Starting…", action: nil, keyEquivalent: "")
        statusLine.isEnabled = false
        m.addItem(statusLine)
        m.addItem(.separator())
        m.addItem(item("Find Best Deals Now", #selector(menuFindDeals), "f"))
        m.addItem(.separator())
        m.addItem(item("Open Dashboard", #selector(menuOverview), "o"))
        m.addItem(item("Check a Deal", #selector(menuCheck), "d"))
        m.addItem(item("Leads", #selector(menuLeads), "l"))
        m.addItem(item("Inventory", #selector(menuInventory), "i"))
        m.addItem(item("Sales and Profit", #selector(menuSales), "p"))
        m.addItem(.separator())
        m.addItem(item("Sync Now", #selector(menuSync), "s"))
        m.addItem(item("Open in Browser", #selector(menuBrowser), "b"))
        m.addItem(item("Show Data Folder", #selector(menuFolder), ""))
        m.addItem(.separator())
        m.addItem(item("Quit ArbitrageOS", #selector(NSApplication.terminate(_:)), "q", target: NSApp))
        statusItem.menu = m
    }

    func item(_ title: String, _ sel: Selector, _ key: String, target: AnyObject? = nil) -> NSMenuItem {
        let i = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        i.target = target ?? self
        return i
    }

    func setStatus(_ s: String) { statusLine?.title = "ArbitrageOS: \(s)" }

    @objc func menuOverview() { open("overview") }
    @objc func menuFindDeals() { open("deals?run=1") }
    @objc func menuCheck() { open("check") }
    @objc func menuLeads() { open("leads") }
    @objc func menuInventory() { open("inventory") }
    @objc func menuSales() { open("sales") }
    @objc func menuBrowser() { NSWorkspace.shared.open(baseURL) }
    @objc func menuFolder() { NSWorkspace.shared.open(root.appendingPathComponent("data")) }
    @objc func menuSync() {
        setStatus("Syncing…")
        post("api/sync") { r in
            self.setStatus(r == "ok" ? "Synced \(DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .short))" : "Sync failed")
            if self.window.isVisible { self.webView.reload() }
        }
    }
    @objc func reloadPage() { webView.reload() }

    // MARK: app menu (needed for copy/paste and shortcuts in the web view)
    func buildMainMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let app = NSMenu()
        app.addItem(withTitle: "About ArbitrageOS", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "Hide ArbitrageOS", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(.separator())
        app.addItem(withTitle: "Quit ArbitrageOS", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = app

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "View")
        let reload = NSMenuItem(title: "Reload", action: #selector(reloadPage), keyEquivalent: "r")
        reload.target = self
        view.addItem(reload)
        viewItem.submenu = view

        let winItem = NSMenuItem(); main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        winItem.submenu = win
        NSApp.windowsMenu = win
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
