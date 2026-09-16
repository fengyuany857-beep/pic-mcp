from pathlib import Path

path = Path("trials/upo-06/ios-helper-v2/Sources/PicMCPAuthHelperV2App.swift")
text = path.read_text(encoding="utf-8")

if "复制 MCP 连接令牌" in text and "MCPAccessReceipt" in text:
    print("Helper MCP credential patch already applied")
    raise SystemExit(0)

replacements = []

replacements.append((
'''                    if let sync = auth.librarySync {
                        LabeledContent("Library Sync", value: sync.status)
                        LabeledContent("公开收藏", value: String(sync.publicCount))
                        LabeledContent("非公开收藏", value: String(sync.privateCount))
                        LabeledContent("已同步", value: String(sync.syncedItems))
                    }
                }

                Section {
                    Text("这一版会在 iPhone 本机完成 Pixiv OAuth、token exchange 和收藏读取。access token 与 refresh token 都不会发送到 PicMCP，也不会写入手机存储；只把规范化后的收藏元数据通过 HTTPS 同步到 PicMCP。单次同步最多 1000 条，超过时会明确标记 PARTIAL_TRUNCATED。")
''',
'''                    if let sync = auth.librarySync {
                        LabeledContent("Library Sync", value: sync.status)
                        LabeledContent("公开收藏", value: String(sync.publicCount))
                        LabeledContent("非公开收藏", value: String(sync.privateCount))
                        LabeledContent("已同步", value: String(sync.syncedItems))
                    }
                    if auth.mcpCredentialReady {
                        LabeledContent("MCP 凭据", value: "已保存到钥匙串")
                        if let endpoint = auth.mcpEndpoint {
                            LabeledContent("MCP Endpoint", value: endpoint)
                                .font(.footnote)
                        }
                        Button("复制 MCP 连接令牌") {
                            auth.copyMCPToken()
                        }
                    }
                    if let notice = auth.mcpCopyNotice {
                        Text(notice)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                Section {
                    Text("这一版会在 iPhone 本机完成 Pixiv OAuth、token exchange 和收藏读取。Pixiv access token 与 refresh token 不会发送到 PicMCP，也不会写入手机存储；只同步规范化收藏元数据。PicMCP 自己的只读 MCP Bearer 会保存到本机 Keychain，重新同步时自动轮换。单次同步最多 1000 条，超过时会明确标记 PARTIAL_TRUNCATED。")
'''))

replacements.append((
'''    @Published var userID: String?
    @Published var librarySync: LibrarySyncReceipt?
    @Published var errorMessage: String?

    private let oauth = PixivOAuthSessionV2()
    private let tokenClient = PixivTokenClient()
    private let libraryClient = PixivLibraryClient()
    private let bridge = BridgeClientV2()

    init() {
        bridgeURL = UserDefaults.standard.string(forKey: "PicMCPBridgeURL") ?? ""
    }
''',
'''    @Published var userID: String?
    @Published var librarySync: LibrarySyncReceipt?
    @Published var mcpCredentialReady = false
    @Published var mcpEndpoint: String?
    @Published var mcpCopyNotice: String?
    @Published var errorMessage: String?

    private let oauth = PixivOAuthSessionV2()
    private let tokenClient = PixivTokenClient()
    private let libraryClient = PixivLibraryClient()
    private let bridge = BridgeClientV2()
    private let mcpTokenStore = MCPTokenStore()

    init() {
        bridgeURL = UserDefaults.standard.string(forKey: "PicMCPBridgeURL") ?? ""
        mcpCredentialReady = mcpTokenStore.exists()
        if mcpCredentialReady,
           let baseURL = try? bridge.validatedBaseURL(bridgeURL) {
            mcpEndpoint = baseURL.appendingPathComponent("mcp").absoluteString
        }
    }
'''))

replacements.append((
'''        errorMessage = nil
        librarySync = nil
        statusText = "准备登录…"
''',
'''        errorMessage = nil
        librarySync = nil
        mcpCopyNotice = nil
        statusText = "准备登录…"
'''))

replacements.append((
'''            guard result.status == "CONNECTED" else {
                throw BridgeClientErrorV2.serverRejected("PicMCP 没有返回 CONNECTED。")
            }

            userID = result.userID
            librarySync = result.librarySync
            statusText = result.librarySync?.status == "PASS" ? "已连接并同步" : "已连接，收藏同步不完整"
        } catch {
''',
'''            guard result.status == "CONNECTED" else {
                throw BridgeClientErrorV2.serverRejected("PicMCP 没有返回 CONNECTED。")
            }
            guard
                let mcpAccess = result.mcpAccess,
                mcpAccess.scheme.caseInsensitiveCompare("Bearer") == .orderedSame,
                mcpAccess.endpoint == "/mcp",
                !mcpAccess.token.isEmpty
            else {
                throw BridgeClientErrorV2.serverRejected("收藏已同步，但 PicMCP 没有返回有效的只读 MCP 连接凭据。请重新同步。")
            }

            try mcpTokenStore.save(mcpAccess.token)
            mcpCredentialReady = true
            mcpEndpoint = baseURL.appendingPathComponent("mcp").absoluteString
            userID = result.userID
            librarySync = result.librarySync
            statusText = result.librarySync?.status == "PASS" ? "已连接并同步" : "已连接，收藏同步不完整"
        } catch {
'''))

replacements.append((
'''            errorMessage = error.localizedDescription
        }
    }
}

struct PixivAuthorizationV2 {
''',
'''            errorMessage = error.localizedDescription
        }
    }

    func copyMCPToken() {
        do {
            guard let token = try mcpTokenStore.load() else {
                mcpCredentialReady = false
                throw BridgeClientErrorV2.serverRejected("钥匙串中没有 PicMCP MCP 连接令牌，请重新同步 Pixiv。")
            }
            UIPasteboard.general.string = token
            mcpCopyNotice = "MCP 连接令牌已复制。只粘贴到你信任的 MCP 客户端；下一次成功同步会让旧令牌失效。"
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct PixivAuthorizationV2 {
'''))

replacements.append((
'''struct BridgeCompleteResponseV2: Decodable {
    let status: String
    let source: String
    let userID: String?
    let librarySync: LibrarySyncReceipt?

    enum CodingKeys: String, CodingKey {
        case status
        case source
        case userID = "user_id"
        case librarySync = "library_sync"
    }
}
''',
'''struct MCPAccessReceipt: Decodable {
    let endpoint: String
    let scheme: String
    let token: String
    let rotation: String
}

struct BridgeCompleteResponseV2: Decodable {
    let status: String
    let source: String
    let userID: String?
    let librarySync: LibrarySyncReceipt?
    let mcpAccess: MCPAccessReceipt?

    enum CodingKeys: String, CodingKey {
        case status
        case source
        case userID = "user_id"
        case librarySync = "library_sync"
        case mcpAccess = "mcp_access"
    }
}
'''))

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one replacement target, found {count}: {old[:80]!r}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Helper MCP credential patch applied")
