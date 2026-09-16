import AuthenticationServices
import Combine
import CryptoKit
import Foundation
import Security
import SwiftUI
import UIKit

@main
struct PicMCPAuthHelperV2App: App {
    @StateObject private var auth = AuthViewModelV2()

    var body: some Scene {
        WindowGroup {
            ContentViewV2()
                .environmentObject(auth)
        }
    }
}

struct ContentViewV2: View {
    @EnvironmentObject private var auth: AuthViewModelV2

    var body: some View {
        NavigationStack {
            Form {
                Section("PicMCP Bridge") {
                    TextField("https://your-picmcp.example", text: $auth.bridgeURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                    Text("Session 由 Helper 在同步前自动向 PicMCP 申请，不再需要手工复制。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Pixiv") {
                    Button {
                        Task { await auth.connect() }
                    } label: {
                        HStack {
                            if auth.isWorking {
                                ProgressView()
                            } else {
                                Image(systemName: "person.crop.circle.badge.checkmark")
                            }
                            Text(auth.isWorking ? "正在连接 Pixiv…" : "连接并同步 Pixiv")
                        }
                    }
                    .disabled(
                        auth.isWorking
                            || auth.bridgeURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )

                    LabeledContent("状态", value: auth.statusText)
                    if let userID = auth.userID {
                        LabeledContent("Pixiv User ID", value: userID)
                    }
                    if let sync = auth.librarySync {
                        LabeledContent("Library Sync", value: sync.status)
                        LabeledContent("公开收藏", value: String(sync.publicCount))
                        LabeledContent("非公开收藏", value: String(sync.privateCount))
                        LabeledContent("已同步", value: String(sync.syncedItems))
                    }
                }

                Section {
                    Text("这一版会在 iPhone 本机完成 Pixiv OAuth、token exchange 和收藏读取。access token 与 refresh token 都不会发送到 PicMCP，也不会写入手机存储；只把规范化后的收藏元数据通过 HTTPS 同步到 PicMCP。单次同步最多 1000 条，超过时会明确标记 PARTIAL_TRUNCATED。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("PicMCP Auth v2")
            .alert(
                "连接失败",
                isPresented: Binding(
                    get: { auth.errorMessage != nil },
                    set: { if !$0 { auth.errorMessage = nil } }
                )
            ) {
                Button("好", role: .cancel) {}
            } message: {
                Text(auth.errorMessage ?? "")
            }
        }
    }
}

@MainActor
final class AuthViewModelV2: ObservableObject {
    @Published var bridgeURL: String
    @Published var isWorking = false
    @Published var statusText = "未连接"
    @Published var userID: String?
    @Published var librarySync: LibrarySyncReceipt?
    @Published var errorMessage: String?

    private let oauth = PixivOAuthSessionV2()
    private let tokenClient = PixivTokenClient()
    private let libraryClient = PixivLibraryClient()
    private let bridge = BridgeClientV2()

    init() {
        bridgeURL = UserDefaults.standard.string(forKey: "PicMCPBridgeURL") ?? ""
    }

    func connect() async {
        guard !isWorking else { return }
        isWorking = true
        errorMessage = nil
        librarySync = nil
        statusText = "准备登录…"
        defer { isWorking = false }

        do {
            let baseURL = try bridge.validatedBaseURL(bridgeURL)
            UserDefaults.standard.set(baseURL.absoluteString, forKey: "PicMCPBridgeURL")

            statusText = "等待 Pixiv 授权…"
            let authorization = try await oauth.authorize()

            statusText = "iPhone 本机交换授权…"
            let token = try await tokenClient.exchange(
                code: authorization.code,
                codeVerifier: authorization.codeVerifier
            )

            statusText = "iPhone 本机读取收藏…"
            let library = try await libraryClient.fetchLibrary(
                accessToken: token.accessToken,
                userID: token.userID
            )

            statusText = "申请一次性同步会话…"
            let sessionID = try await bridge.createSession(baseURL: baseURL)

            statusText = "同步收藏到 PicMCP…"
            let result = try await bridge.completeLibrary(
                baseURL: baseURL,
                sessionID: sessionID,
                userID: token.userID,
                library: library
            )

            guard result.status == "CONNECTED" else {
                throw BridgeClientErrorV2.serverRejected("PicMCP 没有返回 CONNECTED。")
            }

            userID = result.userID
            librarySync = result.librarySync
            statusText = result.librarySync?.status == "PASS" ? "已连接并同步" : "已连接，收藏同步不完整"
        } catch {
            statusText = "未连接"
            errorMessage = error.localizedDescription
        }
    }
}

struct PixivAuthorizationV2 {
    let code: String
    let codeVerifier: String
}

private struct PKCEPreparationV2 {
    let loginURL: URL
    let verifier: String
}

enum PixivOAuthErrorV2: LocalizedError {
    case randomGenerationFailed
    case invalidLoginURL
    case sessionDidNotStart
    case callbackMissing
    case callbackInvalid

    var errorDescription: String? {
        switch self {
        case .randomGenerationFailed: return "无法生成安全的 PKCE 随机数。"
        case .invalidLoginURL: return "无法构造 Pixiv 登录 URL。"
        case .sessionDidNotStart: return "Apple 安全浏览会话没有启动。"
        case .callbackMissing: return "Pixiv 没有返回登录结果。"
        case .callbackInvalid: return "Pixiv callback 不包含有效 authorization code。"
        }
    }
}

@MainActor
final class PixivOAuthSessionV2: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var webSession: ASWebAuthenticationSession?

    func authorize() async throws -> PixivAuthorizationV2 {
        let preparation = try makePreparation()
        return try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: preparation.loginURL,
                callbackURLScheme: "pixiv"
            ) { [weak self] callbackURL, error in
                self?.webSession = nil
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: PixivOAuthErrorV2.callbackMissing)
                    return
                }
                do {
                    let code = try Self.authorizationCode(from: callbackURL)
                    continuation.resume(returning: PixivAuthorizationV2(
                        code: code,
                        codeVerifier: preparation.verifier
                    ))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
            session.presentationContextProvider = self
            session.prefersEphemeralWebBrowserSession = false
            self.webSession = session
            if !session.start() {
                self.webSession = nil
                continuation.resume(throwing: PixivOAuthErrorV2.sessionDidNotStart)
            }
        }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        _ = session
        for scene in UIApplication.shared.connectedScenes {
            guard let windowScene = scene as? UIWindowScene else { continue }
            if let window = windowScene.windows.first(where: { $0.isKeyWindow }) {
                return window
            }
        }
        return UIWindow(frame: .zero)
    }

    private func makePreparation() throws -> PKCEPreparationV2 {
        let verifier = try Self.randomURLSafeString(byteCount: 32)
        let digest = SHA256.hash(data: Data(verifier.utf8))
        let challenge = Data(digest).base64URLEncodedStringV2()

        var components = URLComponents(string: "https://app-api.pixiv.net/web/v1/login")
        components?.queryItems = [
            URLQueryItem(name: "code_challenge", value: challenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "client", value: "pixiv-android")
        ]
        guard let url = components?.url else {
            throw PixivOAuthErrorV2.invalidLoginURL
        }
        return PKCEPreparationV2(loginURL: url, verifier: verifier)
    }

    private static func authorizationCode(from url: URL) throws -> String {
        guard
            url.scheme?.lowercased() == "pixiv",
            url.host?.lowercased() == "account",
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let code = components.queryItems?.first(where: { $0.name == "code" })?.value,
            !code.isEmpty
        else {
            throw PixivOAuthErrorV2.callbackInvalid
        }
        return code
    }

    private static func randomURLSafeString(byteCount: Int) throws -> String {
        var bytes = [UInt8](repeating: 0, count: byteCount)
        let status = bytes.withUnsafeMutableBytes { buffer in
            guard let baseAddress = buffer.baseAddress else { return errSecParam }
            return SecRandomCopyBytes(kSecRandomDefault, byteCount, baseAddress)
        }
        guard status == errSecSuccess else {
            throw PixivOAuthErrorV2.randomGenerationFailed
        }
        return Data(bytes).base64URLEncodedStringV2()
    }
}

struct PixivLocalToken {
    let accessToken: String
    let refreshToken: String
    let userID: String
}

enum PixivTokenClientError: LocalizedError {
    case invalidResponse
    case rejected(Int, String)
    case missingTokenFields

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "Pixiv token endpoint 返回了无效响应。"
        case let .rejected(status, reason):
            return "Pixiv token exchange 失败（HTTP \(status): \(reason)）。"
        case .missingTokenFields:
            return "Pixiv token 响应缺少 access / refresh token 或 user id。"
        }
    }
}

final class PixivTokenClient {
    private let session: URLSession
    private let clientID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
    private let clientSecret = ["lsACyCD94FhDUt", "GTXi3QzcFE2uU1hqtDaKeqrdwj"].joined()
    private let redirectURI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"
    private let hashSalt = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c"
    private let userAgent = "PixivAndroidApp/5.0.166 (Android 10.0; Pixel C)"

    init(session: URLSession = .shared) {
        self.session = session
    }

    func exchange(code: String, codeVerifier: String) async throws -> PixivLocalToken {
        guard let url = URL(string: "https://oauth.secure.pixiv.net/auth/token") else {
            throw PixivTokenClientError.invalidResponse
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = formBody([
            "client_id": clientID,
            "client_secret": clientSecret,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": codeVerifier,
            "redirect_uri": redirectURI,
            "include_policy": "true"
        ])
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        standardHeaders().forEach { request.setValue($0.value, forHTTPHeaderField: $0.key) }

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw PixivTokenClientError.invalidResponse
        }

        let object = try? JSONSerialization.jsonObject(with: data)
        let root = object as? [String: Any]
        let payload = (root?["response"] as? [String: Any]) ?? root

        guard (200..<300).contains(http.statusCode) else {
            let reason = Self.safeReason(root, fallback: "HTTP_\(http.statusCode)")
            throw PixivTokenClientError.rejected(http.statusCode, reason)
        }

        guard
            let accessToken = payload?["access_token"] as? String,
            let refreshToken = payload?["refresh_token"] as? String,
            let user = payload?["user"] as? [String: Any],
            let rawUserID = user["id"]
        else {
            throw PixivTokenClientError.missingTokenFields
        }

        let userID = String(describing: rawUserID)
        guard !accessToken.isEmpty, !refreshToken.isEmpty, !userID.isEmpty else {
            throw PixivTokenClientError.missingTokenFields
        }

        return PixivLocalToken(
            accessToken: accessToken,
            refreshToken: refreshToken,
            userID: userID
        )
    }

    private func standardHeaders() -> [String: String] {
        let time = Self.clientTime()
        return [
            "X-Client-Time": time,
            "X-Client-Hash": Self.md5(time + hashSalt),
            "User-Agent": userAgent,
            "Accept-Language": "zh-CN",
            "App-OS": "Android",
            "App-OS-Version": "Android 10.0",
            "App-Version": "5.0.166"
        ]
    }

    private func formBody(_ values: [String: String]) -> Data? {
        var components = URLComponents()
        components.queryItems = values.map { URLQueryItem(name: $0.key, value: $0.value) }
        return components.percentEncodedQuery?.data(using: .utf8)
    }

    private static func clientTime() -> String {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.formatOptions = [.withInternetDateTime, .withColonSeparatorInTimeZone]
        return formatter.string(from: Date()).replacingOccurrences(of: "Z", with: "+00:00")
    }

    private static func md5(_ value: String) -> String {
        Insecure.MD5.hash(data: Data(value.utf8))
            .map { String(format: "%02hhx", $0) }
            .joined()
    }

    private static func safeReason(_ root: [String: Any]?, fallback: String) -> String {
        let candidates: [Any?] = [
            root?["error"],
            root?["error_description"],
            (root?["errors"] as? [String: Any])?["system"],
            root?["message"]
        ]
        for candidate in candidates {
            if let text = candidate as? String, !text.isEmpty {
                return String(text.prefix(160)).replacingOccurrences(of: "\n", with: " ")
            }
        }
        return fallback
    }
}

struct PixivLibraryItem: Encodable {
    let postID: String
    let visibility: String
    let title: String?
    let creatorID: String?
    let creatorName: String?
    let tags: [String]
    let pageURL: String
    let previewURL: String?
    let originalURL: String?
    let mediaType: String?
    let contentRating: Int?

    enum CodingKeys: String, CodingKey {
        case postID = "post_id"
        case visibility
        case title
        case creatorID = "creator_id"
        case creatorName = "creator_name"
        case tags
        case pageURL = "page_url"
        case previewURL = "preview_url"
        case originalURL = "original_url"
        case mediaType = "media_type"
        case contentRating = "content_rating"
    }
}

struct PixivLibrarySnapshot: Encodable {
    let publicCount: Int
    let privateCount: Int
    let items: [PixivLibraryItem]
    let complete: Bool
    let truncatedReason: String?

    enum CodingKeys: String, CodingKey {
        case publicCount = "public_count"
        case privateCount = "private_count"
        case items
        case complete
        case truncatedReason = "truncated_reason"
    }
}

enum PixivLibraryClientError: LocalizedError {
    case invalidURL
    case invalidResponse
    case rejected(Int, String)
    case malformedPayload
    case paginationLoop

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "无法构造 Pixiv 收藏 API URL。"
        case .invalidResponse:
            return "Pixiv 收藏 API 返回了无效响应。"
        case let .rejected(status, reason):
            return "iPhone 读取 Pixiv 收藏失败（HTTP \(status): \(reason)）。"
        case .malformedPayload:
            return "Pixiv 收藏响应结构不符合预期。"
        case .paginationLoop:
            return "Pixiv 收藏分页出现重复 next_url，已停止以避免死循环。"
        }
    }
}

final class PixivLibraryClient {
    private let session: URLSession
    private let maxItemsPerVisibility = 500
    private let maxPagesPerVisibility = 20
    private let userAgent = "PixivIOSApp/7.13.3 (iOS 14.6; iPhone13,2)"

    init(session: URLSession = .shared) {
        self.session = session
    }

    func fetchLibrary(accessToken: String, userID: String) async throws -> PixivLibrarySnapshot {
        async let publicResult = fetchCollection(
            accessToken: accessToken,
            userID: userID,
            visibility: "public"
        )
        async let privateResult = fetchCollection(
            accessToken: accessToken,
            userID: userID,
            visibility: "private"
        )

        let (publicCollection, privateCollection) = try await (publicResult, privateResult)

        var merged: [String: PixivLibraryItem] = [:]
        for item in publicCollection.items { merged[item.postID] = item }
        for item in privateCollection.items { merged[item.postID] = item }
        let items = merged.values.sorted { lhs, rhs in
            (Int64(lhs.postID) ?? 0) > (Int64(rhs.postID) ?? 0)
        }
        let publicCount = items.filter { $0.visibility == "public" }.count
        let privateCount = items.filter { $0.visibility == "private" }.count
        let complete = publicCollection.complete && privateCollection.complete
        let reasons = [publicCollection.truncatedReason, privateCollection.truncatedReason].compactMap { $0 }

        return PixivLibrarySnapshot(
            publicCount: publicCount,
            privateCount: privateCount,
            items: items,
            complete: complete,
            truncatedReason: reasons.isEmpty ? nil : reasons.joined(separator: ",")
        )
    }

    private func fetchCollection(
        accessToken: String,
        userID: String,
        visibility: String
    ) async throws -> (items: [PixivLibraryItem], complete: Bool, truncatedReason: String?) {
        guard var nextURL = initialURL(userID: userID, visibility: visibility) else {
            throw PixivLibraryClientError.invalidURL
        }

        var page = 0
        var itemsByID: [String: PixivLibraryItem] = [:]
        var seenURLs = Set<String>()

        while true {
            if page >= maxPagesPerVisibility {
                return (
                    Array(itemsByID.values),
                    false,
                    "\(visibility)_page_cap"
                )
            }
            if itemsByID.count >= maxItemsPerVisibility {
                return (
                    Array(itemsByID.values.prefix(maxItemsPerVisibility)),
                    false,
                    "\(visibility)_item_cap"
                )
            }
            guard seenURLs.insert(nextURL.absoluteString).inserted else {
                throw PixivLibraryClientError.paginationLoop
            }

            let result = try await fetchPage(
                url: nextURL,
                accessToken: accessToken,
                visibility: visibility
            )
            for item in result.items {
                if itemsByID.count < maxItemsPerVisibility || itemsByID[item.postID] != nil {
                    itemsByID[item.postID] = item
                }
            }
            page += 1

            guard let returnedNextURL = result.nextURL else {
                return (Array(itemsByID.values), true, nil)
            }
            if itemsByID.count >= maxItemsPerVisibility {
                return (
                    Array(itemsByID.values.prefix(maxItemsPerVisibility)),
                    false,
                    "\(visibility)_item_cap"
                )
            }
            nextURL = returnedNextURL
        }
    }

    private func initialURL(userID: String, visibility: String) -> URL? {
        var components = URLComponents(string: "https://app-api.pixiv.net/v1/user/bookmarks/illust")
        components?.queryItems = [
            URLQueryItem(name: "user_id", value: userID),
            URLQueryItem(name: "restrict", value: visibility),
            URLQueryItem(name: "filter", value: "for_ios")
        ]
        return components?.url
    }

    private func fetchPage(
        url: URL,
        accessToken: String,
        visibility: String
    ) async throws -> (items: [PixivLibraryItem], nextURL: URL?) {
        var request = URLRequest(url: url)
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("ios", forHTTPHeaderField: "App-OS")
        request.setValue("14.6", forHTTPHeaderField: "App-OS-Version")
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("zh-CN", forHTTPHeaderField: "Accept-Language")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw PixivLibraryClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let contentType = http.value(forHTTPHeaderField: "Content-Type")?
                .split(separator: ";", maxSplits: 1)
                .first
                .map(String.init) ?? "unknown"
            throw PixivLibraryClientError.rejected(http.statusCode, contentType)
        }

        guard
            let object = try? JSONSerialization.jsonObject(with: data),
            let root = object as? [String: Any],
            let rawItems = root["illusts"] as? [[String: Any]]
        else {
            throw PixivLibraryClientError.malformedPayload
        }

        let items = rawItems.compactMap { normalize(raw: $0, visibility: visibility) }
        if items.count != rawItems.count {
            throw PixivLibraryClientError.malformedPayload
        }

        let nextURL: URL?
        if let rawNext = root["next_url"] as? String, !rawNext.isEmpty {
            guard let parsed = URL(string: rawNext), parsed.scheme == "https" else {
                throw PixivLibraryClientError.malformedPayload
            }
            nextURL = parsed
        } else {
            nextURL = nil
        }
        return (items, nextURL)
    }

    private func normalize(raw: [String: Any], visibility: String) -> PixivLibraryItem? {
        guard let rawID = raw["id"] else { return nil }
        let postID = String(describing: rawID)
        guard postID.range(of: "^[0-9]{1,32}$", options: .regularExpression) != nil else { return nil }

        let title = raw["title"] as? String
        let user = raw["user"] as? [String: Any]
        let creatorID = user?["id"].map { String(describing: $0) }
        let creatorName = user?["name"] as? String
        let tags = (raw["tags"] as? [[String: Any]] ?? []).compactMap { $0["name"] as? String }

        let imageURLs = raw["image_urls"] as? [String: Any]
        let previewURL = (imageURLs?["medium"] as? String)
            ?? (imageURLs?["square_medium"] as? String)

        var originalURL = (raw["meta_single_page"] as? [String: Any])?["original_image_url"] as? String
        if originalURL == nil,
           let pages = raw["meta_pages"] as? [[String: Any]],
           let firstPage = pages.first,
           let pageURLs = firstPage["image_urls"] as? [String: Any] {
            originalURL = pageURLs["original"] as? String
        }

        return PixivLibraryItem(
            postID: postID,
            visibility: visibility,
            title: title,
            creatorID: creatorID,
            creatorName: creatorName,
            tags: tags,
            pageURL: "https://www.pixiv.net/artworks/\(postID)",
            previewURL: previewURL,
            originalURL: originalURL,
            mediaType: raw["type"] as? String,
            contentRating: raw["x_restrict"] as? Int
        )
    }
}

struct LibrarySyncReceipt: Decodable {
    let status: String
    let publicCount: Int
    let privateCount: Int
    let syncedItems: Int
    let complete: Bool
    let truncatedReason: String?
    let sampleIDs: [String]

    enum CodingKeys: String, CodingKey {
        case status
        case publicCount = "public_count"
        case privateCount = "private_count"
        case syncedItems = "synced_items"
        case complete
        case truncatedReason = "truncated_reason"
        case sampleIDs = "sample_ids"
    }
}

struct BridgeCompleteResponseV2: Decodable {
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

private struct BridgeSessionResponseV2: Decodable {
    let status: String
    let sessionID: String

    enum CodingKeys: String, CodingKey {
        case status
        case sessionID = "session_id"
    }
}

enum BridgeClientErrorV2: LocalizedError {
    case invalidBaseURL
    case insecureBaseURL
    case invalidResponse
    case serverRejected(String)

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL: return "PicMCP Bridge URL 无效。"
        case .insecureBaseURL: return "PicMCP Bridge 必须使用 HTTPS。"
        case .invalidResponse: return "PicMCP Bridge 返回了无效响应。"
        case let .serverRejected(message): return message
        }
    }
}

final class BridgeClientV2 {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func validatedBaseURL(_ raw: String) throws -> URL {
        guard
            let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
            url.host != nil
        else {
            throw BridgeClientErrorV2.invalidBaseURL
        }
        guard url.scheme?.lowercased() == "https" else {
            throw BridgeClientErrorV2.insecureBaseURL
        }
        return url
    }

    func createSession(baseURL: URL) async throws -> String {
        let endpoint = baseURL
            .appendingPathComponent("auth")
            .appendingPathComponent("pixiv")
            .appendingPathComponent("mobile")
            .appendingPathComponent("session")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BridgeClientErrorV2.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw BridgeClientErrorV2.serverRejected(
                (body?["message"] as? String)
                    ?? "PicMCP 无法创建一次性同步会话（HTTP \(http.statusCode)）。"
            )
        }
        guard
            let result = try? JSONDecoder().decode(BridgeSessionResponseV2.self, from: data),
            result.status == "PENDING",
            !result.sessionID.isEmpty
        else {
            throw BridgeClientErrorV2.invalidResponse
        }
        return result.sessionID
    }

    func completeLibrary(
        baseURL: URL,
        sessionID: String,
        userID: String,
        library: PixivLibrarySnapshot
    ) async throws -> BridgeCompleteResponseV2 {
        struct Payload: Encodable {
            let authTransport = "ios_local_library_v1"
            let sessionID: String
            let userID: String
            let library: PixivLibrarySnapshot

            enum CodingKeys: String, CodingKey {
                case authTransport = "auth_transport"
                case sessionID = "session_id"
                case userID = "user_id"
                case library
            }
        }

        let endpoint = baseURL
            .appendingPathComponent("auth")
            .appendingPathComponent("pixiv")
            .appendingPathComponent("mobile")
            .appendingPathComponent("complete-client-library")

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(Payload(
            sessionID: sessionID,
            userID: userID,
            library: library
        ))

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BridgeClientErrorV2.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw BridgeClientErrorV2.serverRejected(
                (body?["message"] as? String)
                    ?? "PicMCP Bridge 拒绝了收藏同步（HTTP \(http.statusCode)）。"
            )
        }
        do {
            return try JSONDecoder().decode(BridgeCompleteResponseV2.self, from: data)
        } catch {
            throw BridgeClientErrorV2.invalidResponse
        }
    }
}

private extension Data {
    func base64URLEncodedStringV2() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
